"""
Prototipo MapReduce sencillo para el TFG.

Flujo:
1. MAP: Gemini analiza cada PDF por separado para una pregunta.
2. REDUCE: Gemini combina las respuestas parciales de esa pregunta.
3. EVALUACION: Gemini compara la respuesta final con la referencia del Excel.
"""

import argparse
import csv
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv


DATA_DIR = Path("data/guias")
QUESTIONS_FILE = Path("questions/preguntas_mapreduce.json")
PROMPT_MAP = Path("prompts/map.txt")
PROMPT_REDUCE = Path("prompts/reduce.txt")
PROMPT_EVALUACION = Path("prompts/evaluacion.txt")
OUTPUT_DIR = Path("outputs/resultados")
MAP_DIR = OUTPUT_DIR / "map"
REDUCE_DIR = OUTPUT_DIR / "reduce"

FINAL_FIELDS = [
    "id_pregunta",
    "numero_excel",
    "pregunta",
    "respuesta_final_mapreduce",
    "respuesta_correcta_excel",
    "evaluacion_gemini",
    "error_evaluacion",
    "evaluacion_manual",
    "comentario_manual",
    "respuestas_map_usadas",
    "errores_map",
]


def cargar_texto(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def cargar_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guardar_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def nombre_seguro(value):
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._")
    return text or "sin_nombre"


def cargar_preguntas(limit=None):
    preguntas = cargar_json(QUESTIONS_FILE)
    if limit is not None:
        preguntas = preguntas[: max(0, limit)]
    return preguntas


def buscar_pdfs(limit=None):
    if not DATA_DIR.exists():
        return []

    pdfs = sorted(DATA_DIR.glob("*.pdf"))
    if limit is not None:
        pdfs = pdfs[: max(0, limit)]
    return pdfs


def obtener_modelo():
    load_dotenv()
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()


def obtener_cliente():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY. Copia .env.example a .env y escribe tu clave.")

    from google import genai

    return genai.Client(api_key=api_key)


def esperar_archivo_gemini(client, uploaded_file, max_wait_seconds=60):
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
            raise RuntimeError("Gemini no pudo procesar el PDF.")

        time.sleep(2)

    return uploaded_file


def extraer_primer_json(text):
    """Extrae el primer objeto JSON aunque venga con texto alrededor."""
    text = str(text or "")
    start = text.find("{")

    while start != -1:
        depth = 0
        in_string = False
        escape = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])

        start = text.find("{", start + 1)

    raise ValueError("Gemini no devolvio un JSON valido.")


def llamar_gemini_texto(prompt):
    client = obtener_cliente()
    response = client.models.generate_content(
        model=obtener_modelo(),
        contents=[prompt],
    )
    return response.text or ""


def normalizar_evaluacion(texto):
    """Convierte la respuesta de Gemini a una de las etiquetas esperadas."""
    value = str(texto or "").strip().lower()

    if "incorrecta" in value:
        return "Incorrecta"
    if "incompleta" in value:
        return "Incompleta"
    if "correcta" in value:
        return "Correcta"
    return "No interpretable"


def evaluar_respuesta(pregunta, respuesta_final):
    """Evalua con Gemini la respuesta MapReduce contra la referencia del Excel."""
    prompt = cargar_texto(PROMPT_EVALUACION)
    prompt = prompt.replace("{pregunta}", pregunta["pregunta"])
    prompt = prompt.replace("{respuesta_correcta}", pregunta["respuesta_correcta_excel"])
    prompt = prompt.replace("{respuesta_mapreduce}", respuesta_final or "")

    raw = llamar_gemini_texto(prompt)
    return normalizar_evaluacion(raw)


def llamar_gemini_pdf(prompt, pdf_path):
    from google.genai import types

    client = obtener_cliente()
    uploaded_file = client.files.upload(file=str(pdf_path))
    uploaded_file = esperar_archivo_gemini(client, uploaded_file)

    response = client.models.generate_content(
        model=obtener_modelo(),
        contents=[prompt, uploaded_file],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


def normalizar_hay_informacion(value):
    value = str(value or "").strip().lower()
    if value in {"si", "no", "dudoso"}:
        return value
    return "dudoso"


def crear_resultado_map(pregunta, pdf_path, respuesta=None, error=None):
    asignatura = pdf_path.stem
    respuesta = respuesta or {}

    return {
        "id_pregunta": pregunta["id"],
        "numero_excel": pregunta["numero_excel"],
        "pregunta": pregunta["pregunta"],
        "pdf": pdf_path.name,
        "asignatura": asignatura,
        "respuesta_map": str(respuesta.get("respuesta_map", "")).strip(),
        "hay_informacion_util": normalizar_hay_informacion(respuesta.get("hay_informacion_util")),
        "evidencia": str(respuesta.get("evidencia", "")).strip(),
        "error": error,
    }


def guardar_resultado_map(pregunta, pdf_path, resultado):
    filename = f"{nombre_seguro(pregunta['id'])}_{nombre_seguro(pdf_path.stem)}.json"
    guardar_json(MAP_DIR / filename, resultado)


def limpiar_map_de_pregunta(pregunta):
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    for path in MAP_DIR.glob(f"{nombre_seguro(pregunta['id'])}_*.json"):
        path.unlink()


def procesar_pdf_map(pregunta, pdf_path, prompt_template):
    prompt = prompt_template.replace("{pregunta}", pregunta["pregunta"])
    prompt += f"\n\nPDF actual: {pdf_path.name}\nAsignatura: {pdf_path.stem}"

    try:
        raw = llamar_gemini_pdf(prompt, pdf_path)
        respuesta = extraer_primer_json(raw)
        resultado = crear_resultado_map(pregunta, pdf_path, respuesta=respuesta)
        print(f"[{pregunta['id']}] {pdf_path.name} OK")
    except Exception as error:
        resultado = crear_resultado_map(pregunta, pdf_path, error=str(error))
        print(f"[{pregunta['id']}] {pdf_path.name} ERROR: {error}")

    guardar_resultado_map(pregunta, pdf_path, resultado)
    return resultado


def ejecutar_map(preguntas, pdfs, workers):
    print("\nEjecutando MAP...")
    prompt_template = cargar_texto(PROMPT_MAP)
    workers = max(1, int(workers or 1))

    for pregunta in preguntas:
        limpiar_map_de_pregunta(pregunta)

        if workers == 1:
            for pdf_path in pdfs:
                procesar_pdf_map(pregunta, pdf_path, prompt_template)
            continue

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(procesar_pdf_map, pregunta, pdf_path, prompt_template)
                for pdf_path in pdfs
            ]
            for future in as_completed(futures):
                future.result()


def cargar_resultados_map(pregunta):
    rows = []
    pattern = f"{nombre_seguro(pregunta['id'])}_*.json"
    for path in sorted(MAP_DIR.glob(pattern)):
        try:
            rows.append(cargar_json(path))
        except Exception as error:
            rows.append(
                {
                    "id_pregunta": pregunta["id"],
                    "numero_excel": pregunta["numero_excel"],
                    "pregunta": pregunta["pregunta"],
                    "pdf": path.name,
                    "asignatura": path.stem,
                    "respuesta_map": "",
                    "hay_informacion_util": "dudoso",
                    "evidencia": "",
                    "error": f"No se pudo leer el JSON MAP: {error}",
                }
            )
    return rows


def resumen_para_reduce(resultados_map):
    resumen = []
    for row in resultados_map:
        resumen.append(
            {
                "pdf": row.get("pdf", ""),
                "asignatura": row.get("asignatura", ""),
                "hay_informacion_util": row.get("hay_informacion_util", ""),
                "respuesta_map": row.get("respuesta_map", ""),
                "evidencia": row.get("evidencia", ""),
                "error": row.get("error"),
            }
        )
    return resumen


def crear_resultado_reduce(pregunta, respuesta_final="", respuestas_map_usadas=0, errores_map=0, error=None):
    return {
        "id_pregunta": pregunta["id"],
        "numero_excel": pregunta["numero_excel"],
        "pregunta": pregunta["pregunta"],
        "respuesta_final_mapreduce": respuesta_final,
        "respuesta_correcta_excel": pregunta["respuesta_correcta_excel"],
        "evaluacion_gemini": "",
        "error_evaluacion": None,
        "evaluacion_manual": "",
        "comentario_manual": "",
        "respuestas_map_usadas": respuestas_map_usadas,
        "errores_map": errores_map,
        "error": error,
    }


def ejecutar_reduce(preguntas):
    print("\nEjecutando REDUCE...")
    prompt_template = cargar_texto(PROMPT_REDUCE)
    resultados_finales = []

    for pregunta in preguntas:
        resultados_map = cargar_resultados_map(pregunta)
        errores_map = sum(1 for row in resultados_map if row.get("error"))
        resultados_map_utiles = [
            row
            for row in resultados_map
            if str(row.get("hay_informacion_util", "")).strip().lower() == "si"
            and not row.get("error")
        ]

        if not resultados_map:
            resultado = crear_resultado_reduce(
                pregunta,
                respuestas_map_usadas=0,
                errores_map=0,
                error="No hay resultados MAP para esta pregunta.",
            )
            guardar_json(REDUCE_DIR / f"{nombre_seguro(pregunta['id'])}.json", resultado)
            resultados_finales.append(resultado)
            print(f"[{pregunta['id']}] reduce ERROR: no hay resultados MAP")
            continue

        if not resultados_map_utiles:
            mensaje = "No se ha encontrado informacion util en los resultados MAP para responder a esta pregunta."
            resultado = crear_resultado_reduce(
                pregunta,
                respuesta_final=mensaje,
                respuestas_map_usadas=0,
                errores_map=errores_map,
                error=mensaje,
            )
            guardar_json(REDUCE_DIR / f"{nombre_seguro(pregunta['id'])}.json", resultado)
            resultados_finales.append(resultado)
            print(f"[{pregunta['id']}] reduce ERROR: no hay respuestas MAP utiles")
            continue

        payload = json.dumps(resumen_para_reduce(resultados_map_utiles), ensure_ascii=False, indent=2)
        prompt = prompt_template.replace("{pregunta}", pregunta["pregunta"])
        prompt += "\n\nRespuestas parciales MAP:\n" + payload

        try:
            respuesta_final = llamar_gemini_texto(prompt).strip()
            resultado = crear_resultado_reduce(
                pregunta,
                respuesta_final=respuesta_final,
                respuestas_map_usadas=len(resultados_map_utiles),
                errores_map=errores_map,
            )
            print(f"[{pregunta['id']}] reduce OK")
        except Exception as error:
            resultado = crear_resultado_reduce(
                pregunta,
                respuestas_map_usadas=len(resultados_map_utiles),
                errores_map=errores_map,
                error=str(error),
            )
            print(f"[{pregunta['id']}] reduce ERROR: {error}")

        guardar_json(REDUCE_DIR / f"{nombre_seguro(pregunta['id'])}.json", resultado)
        resultados_finales.append(resultado)

    guardar_resultados_globales(resultados_finales)
    return resultados_finales


def cargar_resultado_reduce(pregunta):
    path = REDUCE_DIR / f"{nombre_seguro(pregunta['id'])}.json"
    if not path.exists():
        return crear_resultado_reduce(
            pregunta,
            error="No hay resultado REDUCE para esta pregunta.",
        )
    return cargar_json(path)


def ejecutar_evaluacion(preguntas):
    print("\nEjecutando EVALUACION...")
    resultados_finales = []

    for pregunta in preguntas:
        resultado = cargar_resultado_reduce(pregunta)
        respuesta_final = str(resultado.get("respuesta_final_mapreduce") or "").strip()

        if not respuesta_final:
            resultado["evaluacion_gemini"] = "No interpretable"
            resultado["error_evaluacion"] = "No hay respuesta final MapReduce para evaluar."
            print(f"[{pregunta['id']}] evaluacion ERROR: no hay respuesta final")
        else:
            try:
                resultado["evaluacion_gemini"] = evaluar_respuesta(pregunta, respuesta_final)
                resultado["error_evaluacion"] = None
                print(f"[{pregunta['id']}] evaluacion {resultado['evaluacion_gemini']}")
            except Exception as error:
                resultado["evaluacion_gemini"] = "No interpretable"
                resultado["error_evaluacion"] = str(error)
                print(f"[{pregunta['id']}] evaluacion ERROR: {error}")

        resultado.setdefault("evaluacion_manual", "")
        resultado.setdefault("comentario_manual", "")
        resultado.setdefault("respuestas_map_usadas", 0)
        resultado.setdefault("errores_map", 0)
        guardar_json(REDUCE_DIR / f"{nombre_seguro(pregunta['id'])}.json", resultado)
        resultados_finales.append(resultado)

    guardar_resultados_globales(resultados_finales)
    return resultados_finales


def guardar_resultados_globales(resultados_finales):
    guardar_json(OUTPUT_DIR / "resultados_finales.json", resultados_finales)
    guardar_csv(OUTPUT_DIR / "resultados_finales.csv", resultados_finales, FINAL_FIELDS)

    resumen = [
        {
            "id_pregunta": row["id_pregunta"],
            "numero_excel": row["numero_excel"],
            "pregunta": row["pregunta"],
            "evaluacion_gemini": row.get("evaluacion_gemini", ""),
            "evaluacion_manual": row.get("evaluacion_manual", ""),
            "comentario_manual": row.get("comentario_manual", ""),
        }
        for row in resultados_finales
    ]
    guardar_csv(
        OUTPUT_DIR / "resumen_evaluacion.csv",
        resumen,
        [
            "id_pregunta",
            "numero_excel",
            "pregunta",
            "evaluacion_gemini",
            "evaluacion_manual",
            "comentario_manual",
        ],
    )


def dry_run(preguntas, pdfs):
    print(f"Preguntas cargadas: {len(preguntas)}")
    print(f"PDFs encontrados: {len(pdfs)}")
    print(f"Modelo Gemini: {obtener_modelo()}")
    print("\nPreguntas:")
    for pregunta in preguntas:
        print(f"- {pregunta['id']} (Excel {pregunta['numero_excel']}): {pregunta['pregunta']}")

    print("\nPDFs:")
    if not pdfs:
        print("- No hay PDFs en data/guias/")
    for pdf_path in pdfs:
        print(f"- {pdf_path.name}")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Prototipo MapReduce sencillo para el TFG")
    parser.add_argument("--phase", choices=["map", "reduce", "evaluacion", "eval", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-preguntas", type=int, default=None)
    parser.add_argument("--limit-pdfs", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    try:
        preguntas = cargar_preguntas(args.limit_preguntas)
        pdfs = buscar_pdfs(args.limit_pdfs)
    except Exception as error:
        print(f"Error de configuracion: {error}")
        return

    if args.dry_run:
        dry_run(preguntas, pdfs)
        return

    print(f"Preguntas cargadas: {len(preguntas)}")
    print(f"PDFs encontrados: {len(pdfs)}")
    print(f"Modelo Gemini: {obtener_modelo()}")

    if args.phase in {"map", "all"}:
        if not pdfs:
            print("No hay PDFs en data/guias/. Anade las guias antes de ejecutar MAP.")
            return
        try:
            ejecutar_map(preguntas, pdfs, args.workers)
        except RuntimeError as error:
            print(error)
            return

    if args.phase in {"reduce", "all"}:
        try:
            ejecutar_reduce(preguntas)
        except RuntimeError as error:
            print(error)
            return

    if args.phase in {"evaluacion", "eval", "all"}:
        try:
            ejecutar_evaluacion(preguntas)
        except RuntimeError as error:
            print(error)
            return

    print(f"\nResultados guardados en {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
