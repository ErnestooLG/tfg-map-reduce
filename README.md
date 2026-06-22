# Piloto MapReduce TFG

Este proyecto implementa un flujo MapReduce sencillo para validar cinco preguntas reales sobre guias PDF de asignaturas.

La validacion se hace pregunta por pregunta. El programa selecciona una pregunta, ejecuta el MAP sobre todos los PDFs usando solo esa pregunta, ejecuta el REDUCE de esa pregunta y despues pasa a la siguiente. Asi Gemini se concentra en una sola tarea y los resultados parciales son mas faciles de revisar.

## Flujo

1. El script busca los PDFs configurados en `config.yaml`.
2. Lee las preguntas desde `questions/preguntas_prueba.json`.
3. Para cada pregunta seleccionada, ejecuta la fase MAP sobre todos los PDFs.
4. Cada PDF genera un JSON parcial en la carpeta propia de esa pregunta.
5. Se generan `resultados_map.json` y `resultados_map.csv` para esa pregunta.
6. La fase REDUCE lee solo los JSON parciales de esa pregunta y genera `reduce_final.md`.
7. Opcionalmente, `--reduce-llm` genera tambien `reduce_final_llm.md`.
8. Al final se genera `outputs/resultados/resumen_validacion.csv` y `outputs/resultados/resumen_validacion.md`.

## Archivos principales

- `config.yaml`: ruta de los PDFs y modelo de Gemini.
- `questions/preguntas_prueba.json`: cinco preguntas de validacion.
- `.env.example`: ejemplo para crear el archivo `.env` local.
- `requirements.txt`: dependencias necesarias.
- `run_map_reduce.py`: script principal.
- `prompts/map_asignatura.txt`: prompt MAP usado con una unica pregunta cada vez.
- `prompts/reduce_final.txt`: prompt opcional para el reduce con LLM.
- `outputs/resultados/`: carpeta local de resultados generados.

## Instalacion

Desde la carpeta del proyecto:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

En Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuracion

La ruta de las guias se mantiene en `config.yaml`:

```yaml
ruta_pdfs_por_asignatura: "data/guias"
```

Los PDFs se colocan directamente dentro de:

```text
data/guias/
```

El repositorio incluye `data/guias/.gitkeep` para que la carpeta exista al clonarlo, pero no incluye PDFs reales. Los PDFs, `.env`, `.venv` y los resultados generados no deben subirse al repositorio.

El campo `curso` se conserva como metadato local. Con una carpeta plana normalmente saldra como `sin_curso`, y no es importante para las preguntas finales.

El campo `asignatura_base` se calcula localmente a partir del nombre del archivo para no contar dos veces asignaturas divididas en partes. Por ejemplo, `CDPS_parte1` y `CDPS_parte2` se cuentan como `CDPS`; `IWEB_parte1` y `IWEB_parte2` se cuentan como `IWEB`.

La clave real de Gemini va solo en un archivo `.env` local:

```text
GEMINI_API_KEY=tu_clave_real
```

## Ejemplos de uso

Comprobar preguntas y PDFs sin llamar a Gemini:

```bash
python run_map_reduce.py --dry-run
```

Ejecutar las cinco preguntas una detras de otra, con un worker:

```bash
python run_map_reduce.py --workers 1
```

Ejecutar solo la pregunta 4.19:

```bash
python run_map_reduce.py --question-id 4.19 --workers 1
```

Probar una pregunta con solo 5 PDFs:

```bash
python run_map_reduce.py --question-id 4.19 --limit 5 --workers 1
```

Ejecutar solo MAP para todas las preguntas:

```bash
python run_map_reduce.py --phase map --workers 1
```

Ejecutar solo REDUCE usando los JSON parciales ya generados:

```bash
python run_map_reduce.py --phase reduce
```

Ejecutar el reduce local y, ademas, una redaccion opcional con Gemini:

```bash
python run_map_reduce.py --question-id 4.19 --phase reduce --reduce-llm
```

## Opciones utiles

- `--questions-file questions/preguntas_prueba.json`: archivo JSON de preguntas.
- `--question-id 4.19`: ejecuta una unica pregunta.
- `--all-questions`: ejecuta todas las preguntas del JSON, igual que el comportamiento por defecto.
- `--limit N`: procesa solo los primeros `N` PDFs por pregunta.
- `--workers N`: numero de PDFs procesados en paralelo durante la fase MAP.
- `--dry-run`: lista preguntas y PDFs sin llamar a Gemini.
- `--phase all`: ejecuta MAP y REDUCE para cada pregunta seleccionada.
- `--phase map`: ejecuta solo la fase MAP.
- `--phase reduce`: ejecuta solo la fase REDUCE leyendo parciales existentes.
- `--reduce-llm`: mantiene el reduce local y anade una redaccion opcional con Gemini.

Si no se indica `--question-id`, el programa ejecuta todas las preguntas de validacion.

## Salidas generadas

Cada pregunta tiene su propia carpeta:

```text
outputs/resultados/por_pregunta/
  4_19/
    map/
      ADCT.json
      ADSW.json
      ...
    resultados_map.json
    resultados_map.csv
    reduce_final.md
    reduce_final_llm.md

  4_20/
    map/
    resultados_map.json
    resultados_map.csv
    reduce_final.md
```

El resumen general se guarda en:

```text
outputs/resultados/resumen_validacion.csv
outputs/resultados/resumen_validacion.md
```

con estas columnas en el CSV:

```text
id_pregunta,tipo,pregunta,total_pdfs,relevantes,dudosos,sin_informacion,errores,archivo_reduce
```

La carpeta `outputs/resultados/` contiene resultados generados localmente y no se sube al repositorio.

## Entrega

El codigo permite probar primero con `--limit` y despues procesar todas las guias quitando ese limite. La diferencia importante respecto al prototipo inicial es que ahora el flujo multipregunta no mezcla tareas: cada ejecucion MAP recibe una sola pregunta, el REDUCE solo ve resultados de esa misma pregunta y la revision queda separada por carpetas.
