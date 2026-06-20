"""
Piloto sencillo de map-reduce para el TFG.

Flujo:
1. Buscar todos los PDFs de asignaturas.
2. MAP: analizar cada PDF de forma independiente y en paralelo.
3. Guardar un JSON parcial por PDF.
4. REDUCE: leer los JSON parciales y agrupar los resultados.

La clave de Gemini se lee desde .env para no dejarla escrita en el codigo.
"""

import argparse
import csv
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_FILE = "config.yaml"
RESULTS_DIR = Path("outputs/resultados")
MAP_DIR = RESULTS_DIR / "map"

MAP_FIELDS = [
    "curso",
    "asignatura",
    "archivo",
    "examen_final_mayor_40",
    "porcentaje_examen",
    "practicas_obligatorias",
    "cumple_ambas_condiciones",
    "evidencia",
    "observaciones",
    "error",
]

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def read_config():
    """Lee la ruta de PDFs y el modelo desde config.yaml."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_text(path):
    """Lee un archivo de texto."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_pdfs(root):
    """
    Busca PDFs dentro de la carpeta de asignaturas.

    Supongo que la estructura es algo parecido a:
    Todo por cursos/Curso 1/ALGE.pdf
    Todo por cursos/Curso 2/RSTC.pdf
    """
    root = Path(root)
    pdfs = []

    for pdf in sorted(root.rglob("*.pdf")):
        relative_path = pdf.relative_to(root)
        course = relative_path.parts[0] if len(relative_path.parts) > 1 else "sin_curso"

        pdfs.append(
            {
                "curso": course,
                "asignatura": pdf.stem,
                "archivo": str(pdf),
                "tamano_mb": round(pdf.stat().st_size / (1024 * 1024), 2),
            }
        )

    return pdfs


def select_items(items, limit):
    """Aplica --limit solo cuando se indica explicitamente."""
    if limit is None:
        return list(items)
    return list(items)[: max(0, limit)]


def save_inventory(items):
    """Guarda un inventario para revisar que PDFs se han encontrado."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "inventario.csv"

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curso", "asignatura", "archivo", "tamano_mb"])
        writer.writeheader()
        writer.writerows(items)

    print(f"Inventario guardado en {path}")


def get_gemini_client():
    """Crea el cliente de Gemini leyendo la clave desde .env."""
    load_dotenv()

    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError(
            "No se ha encontrado GEMINI_API_KEY. "
            "Copia .env.example a .env y anade la clave real."
        )

    from google import genai

    return genai.Client()


def wait_file_ready(client, uploaded_file, max_wait_seconds=60):
    """Espera a que Gemini termine de preparar el PDF subido."""
    file_name = getattr(uploaded_file, "name", None)
    if not file_name:
        return uploaded_file

    start = time.time()

    while time.time() - start < max_wait_seconds:
        current_file = client.files.get(name=file_name)
        state = getattr(getattr(current_file, "state", None), "name", "ACTIVE")

        if state == "ACTIVE":
            return current_file
        if state == "FAILED":
            raise RuntimeError(f"Gemini no pudo procesar el archivo {file_name}")

        time.sleep(2)

    return uploaded_file


def extract_json(text):
    """
    Intenta extraer el JSON de la respuesta de Gemini.
    Lo hago asi porque a veces el modelo puede envolverlo en ```json.
    """
    text = text.strip()
    text = re.sub(r"^```json", "", text).strip()
    text = re.sub(r"^```", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    return json.loads(text)


def ask_gemini_map(client, model, pdf_path, prompt):
    """Sube un PDF y lo analiza de forma individual."""
    from google.genai import types

    uploaded_file = client.files.upload(file=str(pdf_path))
    uploaded_file = wait_file_ready(client, uploaded_file)

    response = client.models.generate_content(
        model=model,
        contents=[f"{prompt}\n\nPDF analizado: {Path(pdf_path).name}", uploaded_file],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    return extract_json(response.text)


def brief_text(value, max_chars=450):
    """Acorta textos largos para que el CSV sea facil de revisar."""
    text = " ".join(str(value or "").split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def normalize(value):
    """Normaliza respuestas tipo si/no/dudoso."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def normalize_decision(value):
    """Devuelve solo si, no o dudoso."""
    value = normalize(value)
    if value in {"si", "no", "dudoso"}:
        return value
    return "dudoso"


def calculate_cumple(examen_final, practicas):
    """Calcula el resultado final a partir de las dos condiciones principales."""
    examen_final = normalize_decision(examen_final)
    practicas = normalize_decision(practicas)

    if examen_final == "si" and practicas == "si":
        return "si"
    if examen_final == "no" or practicas == "no":
        return "no"
    return "dudoso"


def empty_map_result(item=None, error=None):
    """Crea la estructura comun que tendran todos los JSON parciales."""
    item = item or {}
    return {
        "curso": item.get("curso", ""),
        "asignatura": item.get("asignatura", ""),
        "archivo": item.get("archivo", ""),
        "examen_final_mayor_40": "dudoso",
        "porcentaje_examen": "",
        "practicas_obligatorias": "dudoso",
        "cumple_ambas_condiciones": "dudoso",
        "evidencia": "",
        "observaciones": "",
        "error": error,
    }


def complete_map_answer(answer, item):
    """Completa campos minimos para mantener siempre la misma salida."""
    if not isinstance(answer, dict):
        raise ValueError("Gemini no devolvio un objeto JSON.")

    if "cumple_ambas_condiciones" not in answer:
        answer["cumple_ambas_condiciones"] = answer.pop("cumple", "dudoso")
    else:
        answer.pop("cumple", None)

    result = empty_map_result(item)

    for field in MAP_FIELDS:
        if field in answer and answer[field] is not None:
            result[field] = answer[field]

    result["curso"] = item["curso"]
    result["asignatura"] = item["asignatura"]
    result["archivo"] = item["archivo"]
    result["examen_final_mayor_40"] = normalize_decision(result["examen_final_mayor_40"])
    result["practicas_obligatorias"] = normalize_decision(result["practicas_obligatorias"])

    calculated = calculate_cumple(
        result["examen_final_mayor_40"],
        result["practicas_obligatorias"],
    )
    original = normalize_decision(result["cumple_ambas_condiciones"])
    if original != calculated:
        result["observaciones"] = (
            f"{result['observaciones']} "
            f"Resultado final recalculado como '{calculated}' "
            "porque no coincidia con las dos condiciones anteriores."
        ).strip()

    result["cumple_ambas_condiciones"] = calculated
    result["evidencia"] = brief_text(result["evidencia"])
    result["observaciones"] = brief_text(result["observaciones"])
    result["error"] = None
    return result


def safe_json_filename(pdf_path):
    """Convierte el nombre del PDF en un nombre JSON seguro para Windows."""
    stem = Path(pdf_path).stem.strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", "_", stem).strip(" ._")

    if not stem:
        stem = "sin_nombre"
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        stem = f"{stem}_pdf"

    return f"{stem}.json"


def assign_partial_paths(items):
    """Asigna un JSON parcial unico a cada PDF."""
    used_names = set()
    assignments = []

    for item in items:
        json_name = safe_json_filename(item["archivo"])
        base = Path(json_name).stem
        candidate = MAP_DIR / json_name
        counter = 2

        while candidate.name.lower() in used_names:
            candidate = MAP_DIR / f"{base}_{counter}.json"
            counter += 1

        used_names.add(candidate.name.lower())
        assignments.append((item, candidate))

    return assignments


def clean_map_dir():
    """Limpia los JSON parciales antiguos antes de una nueva fase MAP."""
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    for path in MAP_DIR.glob("*.json"):
        path.unlink()


def write_partial_result(path, result):
    """Guarda el resultado parcial de un PDF."""
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def process_pdf(item, model, prompt, output_path):
    """Procesa un PDF de forma independiente y guarda su JSON parcial."""
    try:
        client = get_gemini_client()
        answer = ask_gemini_map(client, model, item["archivo"], prompt)
        result = complete_map_answer(answer, item)
    except Exception as error:
        result = empty_map_result(item, error=str(error))
        result["observaciones"] = brief_text(f"Error al procesar: {error}")

    write_partial_result(output_path, result)
    return result


def run_map(items, config, limit=None, workers=3):
    """Analiza cada asignatura por separado, en paralelo."""
    selected_items = select_items(items, limit)
    clean_map_dir()

    workers = max(1, int(workers or 1))
    model = config.get("modelo", "gemini-2.5-flash-lite")
    prompt = read_text("prompts/map_asignatura.txt")

    print(f"Procesando {len(selected_items)} PDFs en paralelo con {workers} workers...")

    assignments = assign_partial_paths(selected_items)
    futures = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for item, output_path in assignments:
            future = executor.submit(process_pdf, item, model, prompt, output_path)
            futures[future] = (item, output_path)

        for future in as_completed(futures):
            item, output_path = futures[future]
            pdf_name = Path(item["archivo"]).name

            try:
                result = future.result()
            except Exception as error:
                result = empty_map_result(item, error=str(error))
                result["observaciones"] = brief_text(f"Error al procesar: {error}")
                write_partial_result(output_path, result)

            if result.get("error"):
                print(f"[ERROR] {pdf_name}")
            else:
                print(f"[OK] {pdf_name}")

    results = load_partial_results()
    save_map_results(results)
    return results


def load_partial_results():
    """Lee todos los JSON parciales de outputs/resultados/map/."""
    if not MAP_DIR.exists():
        return []

    results = []
    for path in sorted(MAP_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception as error:
            row = empty_map_result(
                {
                    "curso": "",
                    "asignatura": path.stem,
                    "archivo": str(path),
                },
                error=f"No se pudo leer el JSON parcial: {error}",
            )

        results.append(ensure_map_fields(row))

    return sorted(results, key=lambda row: (row.get("curso", ""), row.get("asignatura", "")))


def ensure_map_fields(row):
    """Asegura que un resultado cargado tenga todos los campos esperados."""
    if not isinstance(row, dict):
        row = {"error": "El JSON parcial no contiene un objeto."}

    result = empty_map_result()
    for field in MAP_FIELDS:
        if field in row:
            result[field] = row[field]

    result["examen_final_mayor_40"] = normalize_decision(result["examen_final_mayor_40"])
    result["practicas_obligatorias"] = normalize_decision(result["practicas_obligatorias"])
    result["cumple_ambas_condiciones"] = calculate_cumple(
        result["examen_final_mayor_40"],
        result["practicas_obligatorias"],
    )
    result["evidencia"] = brief_text(result["evidencia"])
    result["observaciones"] = brief_text(result["observaciones"])
    return result


def save_map_results(results):
    """Guarda los agregados de MAP a partir de los JSON parciales."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = RESULTS_DIR / "resultados_map.json"
    csv_path = RESULTS_DIR / "resultados_map.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAP_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"Agregados MAP guardados en {json_path} y {csv_path}")


def load_map_results():
    """Carga los JSON parciales y regenera los agregados actuales."""
    results = load_partial_results()

    if not results:
        raise RuntimeError(
            "No hay JSON parciales en outputs/resultados/map/. "
            "Ejecuta antes la fase map."
        )

    save_map_results(results)
    return results


def append_group(lines, title, group):
    """Anade un grupo de asignaturas al resumen Markdown."""
    lines.append(f"## {title}\n")

    if not group:
        lines.append("No hay casos en este grupo.\n")
        return

    for row in group:
        lines.append(
            f"- **{row.get('asignatura', '')}** ({row.get('curso', '')}): "
            f"examen={row.get('porcentaje_examen', '')}; "
            f"practicas={row.get('practicas_obligatorias', '')}. "
            f"Evidencia: {row.get('evidencia', '')}"
        )

        if row.get("error"):
            lines.append(f"  Error: {row.get('error')}")

    lines.append("")


def cumple_value(row):
    """Calcula el valor final desde las dos condiciones base."""
    return calculate_cumple(
        row.get("examen_final_mayor_40"),
        row.get("practicas_obligatorias"),
    )


def local_reduce(results):
    """Agrupa los resultados sin hacer otra llamada a Gemini."""
    cumplen = [row for row in results if cumple_value(row) == "si"]
    dudosas = [row for row in results if cumple_value(row) == "dudoso"]
    no_cumplen = [row for row in results if cumple_value(row) == "no"]

    lines = ["# Resultado final map-reduce\n"]
    append_group(lines, "Asignaturas que cumplen ambas condiciones", cumplen)
    append_group(lines, "Asignaturas dudosas", dudosas)
    append_group(lines, "Asignaturas que no cumplen", no_cumplen)

    lines.append("## Resumen\n")
    lines.append(f"- Cumplen: {len(cumplen)}")
    lines.append(f"- Dudosas: {len(dudosas)}")
    lines.append(f"- No cumplen: {len(no_cumplen)}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "reduce_final.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Reduce final guardado en {path}")


def llm_reduce(config, results):
    """Reduce opcional usando Gemini para redactar una respuesta final."""
    client = get_gemini_client()
    model = config.get("modelo", "gemini-2.5-flash-lite")
    prompt = read_text("prompts/reduce_final.txt")

    response = client.models.generate_content(
        model=model,
        contents=[prompt, json.dumps(results, ensure_ascii=False, indent=2)],
    )

    path = RESULTS_DIR / "reduce_final_llm.md"
    path.write_text(response.text, encoding="utf-8")
    print(f"Reduce con Gemini guardado en {path}")


def print_dry_run(items, limit):
    """Lista los PDFs que se procesarian sin llamar a Gemini."""
    selected_items = select_items(items, limit)
    print(f"Dry-run: se listan {len(selected_items)} PDFs. No se ha llamado a Gemini.")

    for item in selected_items:
        print(f"- {item['archivo']}")


def main():
    parser = argparse.ArgumentParser(description="Piloto map-reduce para el TFG")
    parser.add_argument("--dry-run", action="store_true", help="Solo genera inventario, sin llamar a Gemini")
    parser.add_argument("--limit", type=int, default=None, help="Procesa solo los primeros N PDFs")
    parser.add_argument("--workers", type=int, default=3, help="Numero de PDFs procesados en paralelo")
    parser.add_argument("--course", type=str, default=None, help="Filtra por nombre de carpeta de curso")
    parser.add_argument("--phase", choices=["all", "map", "reduce"], default="all")
    parser.add_argument("--reduce-llm", action="store_true", help="Hace tambien el reduce final con Gemini")
    args = parser.parse_args()

    config = read_config()
    items = find_pdfs(config["ruta_pdfs_por_asignatura"])

    if args.course:
        items = [item for item in items if args.course.lower() in item["curso"].lower()]

    save_inventory(items)
    print(f"PDFs encontrados: {len(items)}")

    if args.dry_run:
        print_dry_run(items, args.limit)
        return

    results = None

    if args.phase in ["all", "map"]:
        selected_items = select_items(items, args.limit)
        if not selected_items:
            print(
                f"No se han encontrado PDFs en {config['ruta_pdfs_por_asignatura']}. "
                "Añade las guías antes de ejecutar el map."
            )
            return

        results = run_map(items, config, limit=args.limit, workers=args.workers)

    if args.phase in ["all", "reduce"]:
        if results is None:
            try:
                results = load_map_results()
            except RuntimeError as error:
                print(error)
                return

        print(f"Ejecutando reduce con {len(results)} resultados parciales...")
        local_reduce(results)
        if args.reduce_llm:
            llm_reduce(config, results)


if __name__ == "__main__":
    main()
