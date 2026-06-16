# Piloto map-reduce TFG

Este proyecto es una primera prueba sencilla de la idea de map-reduce aplicada al TFG. La idea es dividir una pregunta grande en consultas pequeñas por asignatura y después juntar los resultados.

Pregunta piloto:

> ¿Qué asignaturas tienen simultáneamente examen final >40% y prácticas obligatorias?

El planteamiento es:

1. Hacer un inventario de los PDFs de asignaturas.
2. Preguntar a Gemini por cada asignatura por separado.
3. Guardar una salida estructurada.
4. Juntar los resultados en un resumen final.

## Archivos principales

- `config.yaml`: rutas del proyecto, pregunta piloto y modelo utilizado. Para esta primera prueba se usa `gemini-2.5-flash-lite`.
- `.env.example` y `.env.template`: ejemplos para crear el archivo `.env` en local.
- `requirements.txt`: dependencias necesarias.
- `run_map_reduce.py`: script principal del piloto.
- `prompts/map_asignatura.txt`: prompt usado para analizar una asignatura.
- `prompts/reduce_final.txt`: prompt opcional para redactar una respuesta final con Gemini.
- `outputs/resultados/`: carpeta local donde se guardan los resultados generados.
- `docs/prueba_inicial_flash_lite.md`: resumen de la primera prueba realizada.

## Instalación

Desde la carpeta del proyecto:

```powershell
cd "C:\Users\ernes\OneDrive\Desktop\UNI\UPM\5\TFG\map_reduce"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

También se puede ejecutar directamente con el Python del entorno virtual:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --dry-run
```

## Configurar la clave

La clave real de Gemini va solo en un archivo `.env` local. Ese archivo está incluido en `.gitignore`, así que no debería subirse al repositorio.

Para crearlo:

```powershell
Copy-Item .env.template .env
notepad .env
```

Dentro de `.env` debe quedar una línea de este estilo:

```text
GEMINI_API_KEY=tu_clave_real
```

No se debe poner la clave en el código, en el README, en los prompts ni en los resultados.

## Prueba sin gastar créditos

Primero se puede comprobar que el script encuentra los PDFs sin llamar a Gemini:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --dry-run
```

Esto genera el inventario de documentos y termina sin hacer llamadas a la API.

## Primera prueba real

Para controlar el coste, la primera prueba real se hace con un solo PDF:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --limit 1
```

Si esa prueba sale razonable, se puede ampliar a tres PDFs:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --limit 3
```

De momento no tiene sentido lanzar todos los documentos hasta revisar manualmente los primeros resultados.

## Salidas generadas

El script puede generar estos archivos dentro de `outputs/resultados/`:

- `inventario.csv`
- `resultados_map.csv`
- `resultados_map.json`
- `reduce_final.md`
- `reduce_final_llm.md`, solo si se usa `--reduce-llm`

Esa carpeta se deja como salida local de pruebas y no se sube al repositorio.

## Estado actual

Ya se ha hecho una primera prueba con `gemini-2.5-flash-lite` y `--limit 1`. El resumen está en `docs/prueba_inicial_flash_lite.md`.

Lo siguiente sería revisar manualmente ese resultado y, si todo cuadra, repetir la prueba con `--limit 3` antes de ampliar a más asignaturas.
