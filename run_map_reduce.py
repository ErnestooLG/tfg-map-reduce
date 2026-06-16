"""
Piloto sencillo de map-reduce para el TFG.

El flujo es:
1. Buscar los PDFs de asignaturas.
2. Analizar cada PDF por separado.
3. Juntar los resultados en un resumen final.

La clave de Gemini se lee desde .env para no dejarla escrita en el código.
"""

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_FILE = "config.yaml"
RESULTS_DIR = Path("outputs/resultados")


def read_config():
    """Lee las rutas, la pregunta y el modelo desde config.yaml."""
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

        pdfs.append({
            "curso": course,
            "asignatura": pdf.stem,
            "archivo": str(pdf),
            "tamano_mb": round(pdf.stat().st_size / (1024 * 1024), 2),
        })

    return pdfs


def save_inventory(items):
    """Guarda un inventario para revisar qué PDFs se han encontrado."""
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
            "Copia .env.example a .env y añade la clave real."
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
    Lo hago así porque a veces el modelo puede envolverlo en ```json.
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
    """Acorta textos largos para que el CSV sea fácil de revisar."""
    text = " ".join(str(value or "").split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def calculate_cumple(examen_final, practicas):
    """Calcula el resultado final a partir de las dos condiciones principales."""
    examen_final = normalize(examen_final)
    practicas = normalize(practicas)

    if examen_final == "si" and practicas == "si":
        return "si"
    if examen_final == "no" or practicas == "no":
        return "no"
    return "dudoso"


def complete_map_answer(answer, item):
    """Completa campos mínimos para mantener siempre la misma salida."""
    if "cumple_ambas_condiciones" not in answer:
        answer["cumple_ambas_condiciones"] = answer.pop("cumple", "dudoso")
    else:
        answer.pop("cumple", None)

    answer.setdefault("asignatura", item["asignatura"])
    answer.setdefault("examen_final_mayor_40", "dudoso")
    answer.setdefault("porcentaje_examen", "")
    answer.setdefault("practicas_obligatorias", "dudoso")
    answer.setdefault("evidencia", "")
    answer.setdefault("observaciones", "")
    calculated = calculate_cumple(
        answer["examen_final_mayor_40"],
        answer["practicas_obligatorias"],
    )
    original = normalize(answer["cumple_ambas_condiciones"])
    if original and original != calculated:
        answer["observaciones"] = (
            f"{answer['observaciones']} "
            f"Resultado final recalculado como '{calculated}' "
            "porque no coincidía con las dos condiciones anteriores."
        ).strip()
    answer["cumple_ambas_condiciones"] = calculated
    answer["evidencia"] = brief_text(answer["evidencia"])
    answer["observaciones"] = brief_text(answer["observaciones"])
    answer["curso"] = item["curso"]
    answer["archivo"] = item["archivo"]
    return answer


def run_map(items, config, limit=None):
    """Analiza cada asignatura por separado."""
    client = get_gemini_client()
    model = config.get("modelo", "gemini-2.5-flash-lite")
    prompt = read_text("prompts/map_asignatura.txt")

    if limit:
        items = items[:limit]

    results = []

    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Analizando {item['asignatura']}")

        try:
            answer = ask_gemini_map(client, model, item["archivo"], prompt)
        except Exception as error:
            # Si algo falla, lo dejo como dudoso para revisarlo después.
            answer = {
                "asignatura": item["asignatura"],
                "examen_final_mayor_40": "dudoso",
                "porcentaje_examen": "",
                "practicas_obligatorias": "dudoso",
                "cumple_ambas_condiciones": "dudoso",
                "evidencia": "",
                "observaciones": f"Error al procesar: {error}",
            }

        answer = complete_map_answer(answer, item)
        results.append(answer)
        save_map_results(results)

    return results


def save_map_results(results):
    """Guarda los resultados en JSON y CSV."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = RESULTS_DIR / "resultados_map.json"
    csv_path = RESULTS_DIR / "resultados_map.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    fields = [
        "curso",
        "asignatura",
        "archivo",
        "examen_final_mayor_40",
        "porcentaje_examen",
        "practicas_obligatorias",
        "cumple_ambas_condiciones",
        "evidencia",
        "observaciones",
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def load_map_results():
    """Carga los resultados anteriores para hacer el resumen final."""
    path = RESULTS_DIR / "resultados_map.json"

    if not path.exists():
        raise RuntimeError("No existe outputs/resultados/resultados_map.json. Ejecuta antes la fase map.")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(value):
    """Normaliza respuestas tipo sí/no/dudoso."""
    return str(value or "").strip().lower().replace("í", "i")


def append_group(lines, title, group):
    """Añade un grupo de asignaturas al resumen Markdown."""
    lines.append(f"## {title}\n")

    if not group:
        lines.append("No hay casos en este grupo.\n")
        return

    for row in group:
        lines.append(
            f"- **{row.get('asignatura', '')}** ({row.get('curso', '')}): "
            f"examen={row.get('porcentaje_examen', '')}; "
            f"prácticas={row.get('practicas_obligatorias', '')}. "
            f"Evidencia: {row.get('evidencia', '')}"
        )

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


def main():
    parser = argparse.ArgumentParser(description="Piloto map-reduce para el TFG")
    parser.add_argument("--dry-run", action="store_true", help="Solo genera inventario, sin llamar a Gemini")
    parser.add_argument("--limit", type=int, default=None, help="Procesa solo los primeros N PDFs")
    parser.add_argument("--course", type=str, default=None, help="Filtra por nombre de carpeta de curso")
    parser.add_argument("--phase", choices=["all", "map", "reduce"], default="all")
    parser.add_argument("--reduce-llm", action="store_true", help="Hace también el reduce final con Gemini")
    args = parser.parse_args()

    config = read_config()
    items = find_pdfs(config["ruta_pdfs_por_asignatura"])

    if args.course:
        items = [item for item in items if args.course.lower() in item["curso"].lower()]

    save_inventory(items)
    print(f"PDFs encontrados: {len(items)}")

    if args.dry_run:
        print("Dry-run terminado. No se ha llamado a Gemini.")
        return

    if args.phase in ["all", "map"]:
        run_map(items, config, args.limit)

    if args.phase in ["all", "reduce"]:
        results = load_map_results()
        local_reduce(results)
        if args.reduce_llm:
            llm_reduce(config, results)


if __name__ == "__main__":
    main()
