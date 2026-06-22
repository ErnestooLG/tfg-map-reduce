"""
Piloto sencillo de map-reduce para el TFG.

Flujo:
1. Buscar todos los PDFs de asignaturas.
2. Leer las preguntas de validacion desde questions/preguntas_prueba.json.
3. Para cada pregunta seleccionada:
   - MAP: analizar cada PDF usando solo esa pregunta.
   - Guardar un JSON parcial por PDF en una carpeta propia de la pregunta.
   - REDUCE: agrupar solo los resultados de esa pregunta.
4. Guardar un resumen general de la validacion.

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
BY_QUESTION_DIR = RESULTS_DIR / "por_pregunta"
SUMMARY_PATH = RESULTS_DIR / "resumen_validacion.csv"
SUMMARY_MD_PATH = RESULTS_DIR / "resumen_validacion.md"

MAP_FIELDS = [
    "curso",
    "asignatura",
    "asignatura_base",
    "archivo",
    "id_pregunta",
    "tipo",
    "pregunta",
    "aporta_informacion",
    "respuesta_parcial",
    "evidencia",
    "confianza",
    "observaciones",
    "error",
]

SUMMARY_FIELDS = [
    "id_pregunta",
    "tipo",
    "pregunta",
    "total_pdfs",
    "relevantes",
    "dudosos",
    "sin_informacion",
    "errores",
    "archivo_reduce",
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


def get_asignatura_base(name):
    """Quita sufijos tipo _parte1 para no contar una asignatura dos veces."""
    stem = Path(str(name or "")).stem.strip()
    base = re.sub(r"[\s_-]*parte[\s_-]*\d+$", "", stem, flags=re.IGNORECASE).strip()
    return base or stem or "sin_asignatura"


def find_pdfs(root):
    """
    Busca PDFs dentro de la carpeta de asignaturas.

    Si los PDFs estan directamente dentro de data/guias, el curso se marca
    como sin_curso. Si estan en subcarpetas, toma la primera carpeta como curso.
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
                "asignatura_base": get_asignatura_base(pdf.stem),
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
        writer = csv.DictWriter(
            f,
            fieldnames=["curso", "asignatura", "asignatura_base", "archivo", "tamano_mb"],
        )
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


def brief_text(value, max_chars=900):
    """Acorta textos largos para que el CSV sea facil de revisar."""
    text = " ".join(str(value or "").split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def normalize(value):
    """Normaliza texto para comparar valores sencillos."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def normalize_decision(value):
    """Devuelve solo si, no o dudoso."""
    value = normalize(value)
    if value in {"si", "no", "dudoso"}:
        return value
    return "dudoso"


def contains_any(text, patterns):
    """Comprueba si aparece alguna expresion normalizada."""
    text = normalize(text)
    return any(pattern in text for pattern in patterns)


def add_manual_review_note(result):
    """Marca una posible contradiccion para revision manual."""
    note = "REVISION MANUAL: posible contradiccion entre la decision y el texto generado."
    observations = str(result.get("observaciones") or "").strip()

    if note not in observations:
        result["observaciones"] = brief_text(f"{note} {observations}", max_chars=450)


def detect_possible_contradiction(result):
    """Detecta contradicciones textuales claras sin decidir semanticamente."""
    combined_text = " ".join(
        str(result.get(field) or "")
        for field in ["respuesta_parcial", "evidencia", "observaciones"]
    )

    negative_markers = [
        "no cumple",
        "no se cumplen",
        "no cumple ambas",
        "por lo tanto, no cumple",
        "no cumple el criterio",
    ]
    positive_markers = [
        "si cumple",
        "cumple la primera condicion",
        "cumple la segunda condicion",
        "cumple ambas condiciones",
        "cumple ambos criterios",
        "por tanto, la asignatura si cumple",
        "por lo tanto, la asignatura si cumple",
    ]

    if result.get("aporta_informacion") == "si" and contains_any(combined_text, negative_markers):
        result["aporta_informacion"] = "dudoso"
        add_manual_review_note(result)
    elif result.get("aporta_informacion") == "no" and contains_any(combined_text, positive_markers):
        result["aporta_informacion"] = "dudoso"
        add_manual_review_note(result)

    return result


def is_temporary_error(error):
    """Detecta errores temporales tipicos de cuota o alta demanda."""
    text = normalize(str(error))
    temporary_markers = [
        "503",
        "429",
        "unavailable",
        "resource_exhausted",
        "resource exhausted",
        "high demand",
    ]
    return any(marker in text for marker in temporary_markers)


def normalize_confidence(value):
    """Devuelve solo alta, media o baja."""
    value = normalize(value)
    if value in {"alta", "media", "baja"}:
        return value
    return "baja"


def normalize_question_id(value):
    """Permite comparar IDs escritos como 4.19 o 4_19."""
    return str(value or "").strip().replace("_", ".")


def read_questions(path):
    """Lee las preguntas de validacion desde un JSON flexible y sencillo."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"No se ha encontrado el archivo de preguntas: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if "preguntas" in data:
            rows = data["preguntas"]
        elif "questions" in data:
            rows = data["questions"]
        else:
            rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        raise RuntimeError("El archivo de preguntas debe contener una lista u objeto JSON.")

    questions = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise RuntimeError(f"La pregunta {index} no es un objeto JSON.")

        question = {
            "id_pregunta": str(row.get("id_pregunta") or row.get("id") or "").strip(),
            "tipo": str(row.get("tipo") or "").strip(),
            "pregunta": str(row.get("pregunta") or row.get("question") or "").strip(),
            "instruccion_map": str(row.get("instruccion_map") or "").strip(),
        }

        if not question["id_pregunta"]:
            raise RuntimeError(f"La pregunta {index} no tiene id_pregunta.")
        if not question["pregunta"]:
            raise RuntimeError(f"La pregunta {question['id_pregunta']} no tiene texto.")

        questions.append(question)

    return questions


def select_questions(questions, question_id=None, all_questions=False):
    """Selecciona una pregunta concreta o todas, siempre una detras de otra."""
    if all_questions or not question_id:
        return list(questions)

    requested = normalize_question_id(question_id)
    for question in questions:
        if normalize_question_id(question["id_pregunta"]) == requested:
            return [question]

    available = ", ".join(question["id_pregunta"] for question in questions)
    raise RuntimeError(f"No existe la pregunta {question_id}. Disponibles: {available}")


def safe_name(value, default="sin_nombre"):
    """Convierte un texto en un nombre seguro para Windows."""
    name = str(value or "").strip()
    name = name.replace(".", "_")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip(" ._")

    if not name:
        name = default
    if name.upper() in WINDOWS_RESERVED_NAMES:
        name = f"{name}_valor"

    return name


def question_slug(question):
    """Devuelve el nombre de carpeta de una pregunta."""
    return safe_name(question["id_pregunta"], default="pregunta")


def question_dir(question):
    """Carpeta propia de una pregunta."""
    return BY_QUESTION_DIR / question_slug(question)


def map_dir_for_question(question):
    """Carpeta de parciales MAP de una pregunta."""
    return question_dir(question) / "map"


def empty_map_result(item=None, question=None, error=None):
    """Crea la estructura comun que tendran todos los JSON parciales."""
    item = item or {}
    question = question or {}
    return {
        "curso": item.get("curso", ""),
        "asignatura": item.get("asignatura", ""),
        "asignatura_base": item.get(
            "asignatura_base",
            get_asignatura_base(item.get("asignatura") or item.get("archivo")),
        ),
        "archivo": item.get("archivo", ""),
        "id_pregunta": question.get("id_pregunta", ""),
        "tipo": question.get("tipo", ""),
        "pregunta": question.get("pregunta", ""),
        "aporta_informacion": "dudoso",
        "respuesta_parcial": "",
        "evidencia": "",
        "confianza": "baja",
        "observaciones": "",
        "error": error,
    }


def complete_map_answer(answer, item, question):
    """Completa campos minimos para mantener siempre la misma salida."""
    if not isinstance(answer, dict):
        raise ValueError("Gemini no devolvio un objeto JSON.")

    if "aporta_informacion" not in answer:
        answer["aporta_informacion"] = answer.get("aporta") or answer.get("relevante") or "dudoso"

    result = empty_map_result(item, question)

    for field in MAP_FIELDS:
        if field in answer and answer[field] is not None:
            result[field] = answer[field]

    result["curso"] = item["curso"]
    result["asignatura"] = item["asignatura"]
    result["asignatura_base"] = item["asignatura_base"]
    result["archivo"] = item["archivo"]
    result["id_pregunta"] = question["id_pregunta"]
    result["tipo"] = question["tipo"]
    result["pregunta"] = question["pregunta"]
    result["aporta_informacion"] = normalize_decision(result["aporta_informacion"])
    result["confianza"] = normalize_confidence(result["confianza"])
    result["respuesta_parcial"] = brief_text(result["respuesta_parcial"])
    result["evidencia"] = brief_text(result["evidencia"])
    result["observaciones"] = brief_text(result["observaciones"], max_chars=450)
    result["error"] = None
    return detect_possible_contradiction(result)


def safe_json_filename(pdf_path):
    """Convierte el nombre del PDF en un nombre JSON seguro para Windows."""
    stem = Path(pdf_path).stem.strip()
    return f"{safe_name(stem)}.json"


def assign_partial_paths(items, map_dir):
    """Asigna un JSON parcial unico a cada PDF dentro de una pregunta."""
    used_names = set()
    assignments = []

    for item in items:
        json_name = safe_json_filename(item["archivo"])
        base = Path(json_name).stem
        candidate = map_dir / json_name
        counter = 2

        while candidate.name.lower() in used_names:
            candidate = map_dir / f"{base}_{counter}.json"
            counter += 1

        used_names.add(candidate.name.lower())
        assignments.append((item, candidate))

    return assignments


def clean_map_dir(map_dir):
    """Limpia los JSON parciales antiguos antes de una nueva fase MAP."""
    map_dir.mkdir(parents=True, exist_ok=True)
    for path in map_dir.glob("*.json"):
        path.unlink()


def write_partial_result(path, result):
    """Guarda el resultado parcial de un PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def build_map_prompt(question):
    """Construye el prompt MAP anadiendo una unica pregunta."""
    base_prompt = read_text("prompts/map_asignatura.txt")
    question_data = json.dumps(question, ensure_ascii=False, indent=2)
    local_instruction = question.get("instruccion_map", "").strip()

    if local_instruction:
        return (
            f"{base_prompt}\n\n"
            f"Pregunta global de validacion:\n{question['pregunta']}\n\n"
            f"Instruccion local MAP obligatoria:\n{local_instruction}\n\n"
            f"Datos de la pregunta actual:\n{question_data}"
        )

    return f"{base_prompt}\n\nDatos de la pregunta actual:\n{question_data}"


def process_pdf(item, model, prompt, output_path, question):
    """Procesa un PDF de forma independiente y guarda su JSON parcial."""
    retry_waits = [10, 20]

    for attempt in range(3):
        try:
            client = get_gemini_client()
            answer = ask_gemini_map(client, model, item["archivo"], prompt)
            result = complete_map_answer(answer, item, question)
            break
        except Exception as error:
            if attempt < len(retry_waits) and is_temporary_error(error):
                wait_seconds = retry_waits[attempt]
                print(
                    f"[REINTENTO] {Path(item['archivo']).name}: "
                    f"error temporal, esperando {wait_seconds}s..."
                )
                time.sleep(wait_seconds)
                continue

            result = empty_map_result(item, question, error=str(error))
            if is_temporary_error(error) and attempt >= len(retry_waits):
                message = f"Error temporal tras varios reintentos: {error}"
            else:
                message = f"Error al procesar: {error}"
            result["observaciones"] = brief_text(message, max_chars=450)
            break

    write_partial_result(output_path, result)
    return result


def run_map(items, config, question, limit=None, workers=3):
    """Analiza cada asignatura por separado usando solo una pregunta."""
    selected_items = select_items(items, limit)
    map_dir = map_dir_for_question(question)
    clean_map_dir(map_dir)

    workers = max(1, int(workers or 1))
    model = config.get("modelo", "gemini-2.5-flash-lite")
    prompt = build_map_prompt(question)

    print(
        f"Pregunta {question['id_pregunta']}: "
        f"procesando {len(selected_items)} PDFs con {workers} workers..."
    )

    assignments = assign_partial_paths(selected_items, map_dir)
    futures = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for item, output_path in assignments:
            future = executor.submit(process_pdf, item, model, prompt, output_path, question)
            futures[future] = (item, output_path)

        for future in as_completed(futures):
            item, output_path = futures[future]
            pdf_name = Path(item["archivo"]).name

            try:
                result = future.result()
            except Exception as error:
                result = empty_map_result(item, question, error=str(error))
                result["observaciones"] = brief_text(f"Error al procesar: {error}", max_chars=450)
                write_partial_result(output_path, result)

            if result.get("error"):
                print(f"[ERROR] {pdf_name}")
            else:
                print(f"[OK] {pdf_name}")

    results = load_partial_results(question)
    save_map_results(question, results)
    return results


def load_partial_results(question):
    """Lee todos los JSON parciales de una pregunta."""
    map_dir = map_dir_for_question(question)
    if not map_dir.exists():
        return []

    results = []
    for path in sorted(map_dir.glob("*.json")):
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
                question,
                error=f"No se pudo leer el JSON parcial: {error}",
            )

        results.append(ensure_map_fields(row, question))

    return sorted(results, key=lambda row: (row.get("curso", ""), row.get("asignatura", "")))


def ensure_map_fields(row, question):
    """Asegura que un resultado cargado tenga todos los campos esperados."""
    if not isinstance(row, dict):
        row = {"error": "El JSON parcial no contiene un objeto."}

    result = empty_map_result(question=question)
    for field in MAP_FIELDS:
        if field in row and row[field] is not None:
            result[field] = row[field]

    result["id_pregunta"] = question["id_pregunta"]
    result["tipo"] = question["tipo"]
    result["pregunta"] = question["pregunta"]
    result["asignatura_base"] = get_asignatura_base(
        result.get("asignatura") or result.get("archivo") or result.get("asignatura_base")
    )
    result["aporta_informacion"] = normalize_decision(result["aporta_informacion"])
    result["confianza"] = normalize_confidence(result["confianza"])
    result["respuesta_parcial"] = brief_text(result["respuesta_parcial"])
    result["evidencia"] = brief_text(result["evidencia"])
    result["observaciones"] = brief_text(result["observaciones"], max_chars=450)
    return result


def save_map_results(question, results):
    """Guarda los agregados de MAP a partir de los JSON parciales."""
    out_dir = question_dir(question)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "resultados_map.json"
    csv_path = out_dir / "resultados_map.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAP_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"Agregados MAP guardados en {json_path} y {csv_path}")


def load_map_results(question):
    """Carga los JSON parciales de una pregunta y regenera sus agregados."""
    results = load_partial_results(question)

    if not results:
        raise RuntimeError(
            f"No hay JSON parciales en {map_dir_for_question(question)}. "
            "Ejecuta antes la fase map."
        )

    save_map_results(question, results)
    return results


def subject_label(row):
    """Etiqueta breve con asignatura y asignatura_base."""
    asignatura = row.get("asignatura", "")
    base = row.get("asignatura_base", "") or get_asignatura_base(asignatura)
    if base and asignatura and base != asignatura:
        return f"{base} ({asignatura})"
    return asignatura or base


def append_group(lines, title, group):
    """Anade un grupo de asignaturas al resumen Markdown."""
    lines.append(f"## {title}\n")

    if not group:
        lines.append("No hay casos en este grupo.\n")
        return

    for row in group:
        lines.append(f"- **{subject_label(row)}** ({row.get('curso', '')})")

        if row.get("respuesta_parcial"):
            lines.append(f"  Respuesta parcial: {row.get('respuesta_parcial')}")
        if row.get("evidencia"):
            lines.append(f"  Evidencia: {row.get('evidencia')}")
        if row.get("observaciones"):
            lines.append(f"  Observaciones: {row.get('observaciones')}")
        if row.get("error"):
            lines.append(f"  Error: {row.get('error')}")

    lines.append("")


def aporta_value(row):
    """Devuelve el valor normalizado de aporta_informacion."""
    return normalize_decision(row.get("aporta_informacion"))


def is_question(question, question_id):
    """Comprueba el id de pregunta aceptando 4.21 y 4_21."""
    return normalize_question_id(question.get("id_pregunta")) == normalize_question_id(question_id)


def unique_base_decisions(results):
    """Agrupa resultados por asignatura_base y combina partes divididas."""
    grouped = {}

    for row in results:
        if row.get("error"):
            continue

        base = row.get("asignatura_base") or get_asignatura_base(row.get("asignatura") or row.get("archivo"))
        if base not in grouped:
            grouped[base] = {
                "decision": "no",
                "rows": [],
            }

        grouped[base]["rows"].append(row)
        decision = aporta_value(row)
        current = grouped[base]["decision"]

        if decision == "si" or (decision == "dudoso" and current == "no"):
            grouped[base]["decision"] = decision

    return grouped


def add_question_421_summary(lines, results):
    """Anade el calculo de porcentaje para trabajo en grupo."""
    grouped = unique_base_decisions(results)
    total = len(grouped)
    with_group_work = sorted(base for base, data in grouped.items() if data["decision"] == "si")
    doubtful = sorted(base for base, data in grouped.items() if data["decision"] == "dudoso")

    lines.append("## Calculo especifico de la pregunta 4.21\n")
    lines.append("El calculo usa `asignatura_base` para contar una sola vez las asignaturas divididas en varios PDFs.\n")

    if total == 0:
        lines.append("No hay resultados MAP validos para calcular el porcentaje.\n")
        return

    percentage = len(with_group_work) / total * 100
    lines.append(f"- Asignaturas analizadas: {total}")
    lines.append(f"- Asignaturas con trabajo en grupo: {len(with_group_work)}")
    lines.append(f"- Porcentaje: {len(with_group_work)} / {total} · 100 = {percentage:.2f} %")

    if with_group_work:
        lines.append(f"- Asignaturas contadas como si: {', '.join(with_group_work)}")
    if doubtful:
        lines.append(f"- Asignaturas dudosas: {', '.join(doubtful)}")

    lines.append("")


def local_reduce(question, results):
    """Agrupa los resultados de una unica pregunta sin llamar a Gemini."""
    valid_results = [row for row in results if not row.get("error")]
    relevantes = [row for row in valid_results if aporta_value(row) == "si"]
    dudosos = [row for row in valid_results if aporta_value(row) == "dudoso"]
    sin_informacion = [row for row in valid_results if aporta_value(row) == "no"]
    errores = [row for row in results if row.get("error")]

    lines = [
        "# Resultado final map-reduce",
        "",
        f"Pregunta {question['id_pregunta']}: {question['pregunta']}",
        f"Tipo: {question['tipo'] or 'sin_tipo'}",
        "",
    ]

    if is_question(question, "4.21"):
        add_question_421_summary(lines, results)

    append_group(lines, "PDFs que aportan informacion", relevantes)
    append_group(lines, "PDFs dudosos", dudosos)
    append_group(lines, "PDFs/asignaturas que no cumplen el criterio", sin_informacion)
    append_group(lines, "Errores", errores)

    lines.append("## Resumen\n")
    lines.append(f"- Total PDFs: {len(results)}")
    lines.append(f"- Relevantes: {len(relevantes)}")
    lines.append(f"- Dudosos: {len(dudosos)}")
    lines.append(f"- No cumplen el criterio: {len(sin_informacion)}")
    lines.append(f"- Errores: {len(errores)}")

    out_dir = question_dir(question)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "reduce_final.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Reduce final guardado en {path}")
    return path


def llm_reduce(config, question, results):
    """Reduce opcional usando Gemini para redactar una respuesta final."""
    client = get_gemini_client()
    model = config.get("modelo", "gemini-2.5-flash-lite")
    prompt = read_text("prompts/reduce_final.txt")
    payload = {
        "pregunta": question,
        "resultados_map": results,
    }

    response = client.models.generate_content(
        model=model,
        contents=[prompt, json.dumps(payload, ensure_ascii=False, indent=2)],
    )

    out_dir = question_dir(question)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "reduce_final_llm.md"
    path.write_text(response.text, encoding="utf-8")
    print(f"Reduce con Gemini guardado en {path}")
    return path


def summary_row(question, results):
    """Construye una fila del resumen general."""
    errores = [row for row in results if row.get("error")]
    valid_results = [row for row in results if not row.get("error")]
    reduce_path = question_dir(question) / "reduce_final.md"

    return {
        "id_pregunta": question["id_pregunta"],
        "tipo": question["tipo"],
        "pregunta": question["pregunta"],
        "total_pdfs": len(results),
        "relevantes": sum(1 for row in valid_results if aporta_value(row) == "si"),
        "dudosos": sum(1 for row in valid_results if aporta_value(row) == "dudoso"),
        "sin_informacion": sum(1 for row in valid_results if aporta_value(row) == "no"),
        "errores": len(errores),
        "archivo_reduce": str(reduce_path) if reduce_path.exists() else "",
    }


def save_validation_summary(questions):
    """Guarda el CSV de resumen general para las preguntas seleccionadas."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = [summary_row(question, load_partial_results(question)) for question in questions]

    with open(SUMMARY_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Resumen general guardado en {SUMMARY_PATH}")
    save_validation_summary_md(rows)


def save_validation_summary_md(rows):
    """Guarda una version Markdown sencilla del resumen general."""
    lines = [
        "# Resumen de validacion",
        "",
        "| Pregunta | Tipo | PDFs | Relevantes | Dudosos | Sin informacion | Errores | Reduce |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    for row in rows:
        reduce_link = row["archivo_reduce"] or ""
        lines.append(
            f"| {row['id_pregunta']} | {row['tipo']} | {row['total_pdfs']} | "
            f"{row['relevantes']} | {row['dudosos']} | {row['sin_informacion']} | "
            f"{row['errores']} | {reduce_link} |"
        )

    lines.append("")
    SUMMARY_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Resumen Markdown guardado en {SUMMARY_MD_PATH}")


def print_dry_run(items, questions, limit):
    """Lista lo que se procesaria sin llamar a Gemini."""
    selected_items = select_items(items, limit)
    print(
        f"Dry-run: {len(questions)} preguntas seleccionadas y "
        f"{len(selected_items)} PDFs por pregunta. No se ha llamado a Gemini."
    )

    print("Preguntas:")
    for question in questions:
        print(f"- {question['id_pregunta']} ({question['tipo']}): {question['pregunta']}")

    print("PDFs:")
    for item in selected_items:
        print(f"- {item['archivo']}")


def main():
    parser = argparse.ArgumentParser(description="Piloto map-reduce para el TFG")
    parser.add_argument("--questions-file", default="questions/preguntas_prueba.json")
    parser.add_argument("--question-id", type=str, default=None, help="Ejecuta solo una pregunta concreta")
    parser.add_argument(
        "--all-questions",
        action="store_true",
        help="Ejecuta todas las preguntas del JSON (tambien es el valor por defecto)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo lista el trabajo, sin llamar a Gemini")
    parser.add_argument("--limit", type=int, default=None, help="Procesa solo los primeros N PDFs por pregunta")
    parser.add_argument("--workers", type=int, default=3, help="Numero de PDFs procesados en paralelo")
    parser.add_argument("--course", type=str, default=None, help="Filtra por nombre de carpeta de curso")
    parser.add_argument("--phase", choices=["all", "map", "reduce"], default="all")
    parser.add_argument("--reduce-llm", action="store_true", help="Hace tambien el reduce final con Gemini")
    args = parser.parse_args()

    if args.question_id and args.all_questions:
        parser.error("Usa --question-id o --all-questions, pero no ambos a la vez.")

    try:
        questions = read_questions(args.questions_file)
        selected_questions = select_questions(
            questions,
            question_id=args.question_id,
            all_questions=args.all_questions,
        )
    except RuntimeError as error:
        print(error)
        return

    config = read_config()
    items = find_pdfs(config["ruta_pdfs_por_asignatura"])

    if args.course:
        items = [item for item in items if args.course.lower() in item["curso"].lower()]

    save_inventory(items)
    print(f"PDFs encontrados: {len(items)}")

    if args.dry_run:
        print_dry_run(items, selected_questions, args.limit)
        return

    for question in selected_questions:
        print("")
        print(f"=== Pregunta {question['id_pregunta']} ===")
        results = None

        if args.phase in ["all", "map"]:
            selected_items = select_items(items, args.limit)
            if not selected_items:
                print(
                    f"No se han encontrado PDFs en {config['ruta_pdfs_por_asignatura']}. "
                    "Anade las guias antes de ejecutar el map."
                )
                continue

            results = run_map(items, config, question, limit=args.limit, workers=args.workers)

        if args.phase in ["all", "reduce"]:
            if results is None:
                try:
                    results = load_map_results(question)
                except RuntimeError as error:
                    print(error)
                    continue

            print(f"Ejecutando reduce con {len(results)} resultados parciales...")
            local_reduce(question, results)
            if args.reduce_llm:
                llm_reduce(config, question, results)

    save_validation_summary(selected_questions)


if __name__ == "__main__":
    main()
