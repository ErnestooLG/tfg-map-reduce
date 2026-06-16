"""
Piloto mínimo map-reduce

La idea es hacerlo lo más simple posible:
1. Buscar los PDFs de asignaturas.
2. Preguntar a Gemini por cada asignatura por separado (fase map).
3. Juntar los resultados en un resumen final (fase reduce).

No se mete ninguna API key en el código. La clave se lee desde un archivo .env.
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
    """Lee config.yaml, donde están las rutas y el modelo."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_text(path):
    """Lee un archivo de texto, normalmente un prompt."""
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
    """Guarda el inventario de PDFs para poder comprobar qué se va a procesar."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "inventario.csv"

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curso", "asignatura", "archivo", "tamano_mb"])
        writer.writeheader()
        writer.writerows(items)

    print(f"Inventario guardado en {path}")


def get_gemini_client():
    """Crea el cliente de Gemini. Si no hay clave, avisa claro."""
    load_dotenv()

    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError(
            "No se ha encontrado GEMINI_API_KEY. "
            "Copia .env.example a .env y añade la clave real."
        )

    from google import genai
    return genai.Client()


def wait_file_ready(client, uploaded_file, max_wait_seconds=60):
    """Espera a que Gemini termine de procesar el PDF subido."""
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
    """Sube un PDF y pregunta a Gemini solo por esa asignatura."""
    uploaded_file = client.files.upload(file=str(pdf_path))
    uploaded_file = wait_file_ready(client, uploaded_file)

    response = client.models.generate_content(
        model=model,
        contents=[prompt, uploaded_file],
    )

    return extract_json(response.text)


def run_map(items, config, limit=None):
    """Ejecuta la fase map: una llamada a Gemini por cada asignatura."""
    client = get_gemini_client()
    model = config.get("modelo", "gemini-2.5-flash")
    prompt = read_text("prompts/map_asignatura.txt")

    if limit:
        items = items[:limit]

    results = []

    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Analizando {item['asignatura']}")

        try:
            answer = ask_gemini_map(client, model, item["archivo"], prompt)
        except Exception as error:
            # Si algo falla, no invento resultado. Lo marco como dudoso para revisarlo.
            answer = {
                "asignatura": item["asignatura"],
                "examen_final_mayor_40": "dudoso",
                "porcentaje_examen": "",
                "practicas_obligatorias": "dudoso",
                "evidencia": "",
                "cumple": "dudoso",
                "observaciones": f"Error al procesar: {error}",
            }

        answer["curso"] = item["curso"]
        answer["archivo"] = item["archivo"]
        results.append(answer)
        save_map_results(results)

    return results


def save_map_results(results):
    """Guarda los resultados map en JSON y CSV."""
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
        "evidencia",
        "cumple",
        "observaciones",
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def load_map_results():
    """Carga resultados_map.json para poder hacer el reduce."""
    path = RESULTS_DIR / "resultados_map.json"

    if not path.exists():
        raise RuntimeError("No existe outputs/resultados/resultados_map.json. Ejecuta antes la fase map.")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(value):
    """Normaliza respuestas tipo sí/no/dudoso para compararlas mejor."""
    return str(value or "").strip().lower().replace("í", "i")


def append_group(lines, title, group):
    """Añade un grupo de asignaturas al Markdown final."""
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


def local_reduce(results):
    """
    Reduce sencillo sin llamar a Gemini.
    Solo agrupa las asignaturas en cumplen, dudosas y no cumplen.
    """
    cumplen = [row for row in results if normalize(row.get("cumple")) == "si"]
    dudosas = [row for row in results if normalize(row.get("cumple")) == "dudoso"]
    no_cumplen = [row for row in results if normalize(row.get("cumple")) == "no"]

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
    """
    Reduce opcional usando Gemini.
    Esto encaja con la idea del tutor de juntar las respuestas y pasarlas otra vez al LLM.
    """
    client = get_gemini_client()
    model = config.get("modelo", "gemini-2.5-flash")
    prompt = read_text("prompts/reduce_final.txt")

    response = client.models.generate_content(
        model=model,
        contents=[prompt, json.dumps(results, ensure_ascii=False, indent=2)],
    )

    path = RESULTS_DIR / "reduce_final_llm.md"
    path.write_text(response.text, encoding="utf-8")
    print(f"Reduce con Gemini guardado en {path}")


def main():
    parser = argparse.ArgumentParser(description="Piloto mínimo map-reduce para el TFG")
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
