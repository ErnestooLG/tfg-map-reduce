# Prototipo MapReduce TFG

Este repositorio contiene un prototipo sencillo para probar una idea concreta del TFG: usar MapReduce con Gemini sobre guias docentes en PDF.

La idea es:

1. Se seleccionan cinco preguntas reales que Gemini fallo cuando recibia todos los documentos.
2. En MAP, Gemini procesa todos los PDFs y responde la pregunta mirando cada PDF de asignatura por separado.
3. En REDUCE, Gemini combina las respuestas MAP marcadas como utiles.
4. En EVALUACION, Gemini compara la respuesta final con la respuesta correcta de referencia.
5. El programa guarda la evaluacion `Correcta`, `Incompleta` o `Incorrecta`.

La metrica importante no es que el programa ejecute sin errores. Eso solo es una comprobacion tecnica. Lo importante es revisar si la respuesta final MapReduce acierta el contenido de la respuesta correcta.

## Preparar Entorno

En Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Crear el archivo `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

Dentro de `.env` hay que poner la clave de Gemini:

```text
GEMINI_API_KEY=tu_clave_real
GEMINI_MODEL=gemini-2.5-flash-lite
```

## PDFs

Los PDFs de las guias se colocan en:

```text
data/guias/
```

No se suben al repositorio. La carpeta se mantiene con `.gitkeep`.

Los PDFs partidos, como `CDPS_parte1.pdf`, `CDPS_parte2.pdf`, `IWEB_parte1.pdf` e `IWEB_parte2.pdf`, se procesan como PDFs independientes en MAP.

## Preguntas

Las cinco preguntas estan en:

```text
questions/preguntas_mapreduce.json
```

Cada pregunta incluye tambien la respuesta correcta del Excel. Esa respuesta no se pasa a Gemini en MAP ni en REDUCE; solo se usa despues, en la fase de EVALUACION.

## Uso

Ver preguntas y PDFs detectados sin llamar a Gemini:

```powershell
python run_map_reduce.py --dry-run
```

Probar con una pregunta y pocos PDFs:

```powershell
python run_map_reduce.py --phase all --limit-preguntas 1 --limit-pdfs 2 --workers 1
```

Ejecutar todo:

```powershell
python run_map_reduce.py --phase all --workers 1
```

Ejecutar solo MAP:

```powershell
python run_map_reduce.py --phase map --workers 1
```

Ejecutar solo REDUCE con resultados MAP ya existentes:

```powershell
python run_map_reduce.py --phase reduce
```

Ejecutar solo EVALUACION con resultados REDUCE ya existentes:

```powershell
python run_map_reduce.py --phase evaluacion
```

Opciones utiles:

- `--phase map`: ejecuta solo MAP.
- `--phase reduce`: ejecuta solo REDUCE.
- `--phase evaluacion`: ejecuta solo la evaluacion con Gemini.
- `--phase all`: ejecuta MAP, REDUCE y EVALUACION.
- `--limit-preguntas 1`: procesa solo la primera pregunta.
- `--limit-pdfs 5`: procesa solo los primeros cinco PDFs.
- `--workers 3`: procesa varios PDFs en paralelo durante MAP.

Por defecto se usa `--phase all` y `--workers 1`.

## Resultados

Las respuestas parciales MAP se guardan en:

```text
outputs/resultados/map/
```

Ejemplo:

```text
outputs/resultados/map/P03_CALC.json
```

Las respuestas finales REDUCE se guardan en:

```text
outputs/resultados/reduce/
```

Ejemplo:

```text
outputs/resultados/reduce/P03.json
```

Cada JSON final incluye:

- `respuesta_final_mapreduce`
- `respuesta_correcta_excel`
- `evaluacion_gemini`
- `error_evaluacion`
- `evaluacion_manual`
- `comentario_manual`

Tambien se generan:

```text
outputs/resultados/resultados_finales.json
outputs/resultados/resultados_finales.csv
outputs/resultados/resumen_evaluacion.csv
```

`resultados_finales.csv` contiene la respuesta MapReduce, la respuesta correcta del Excel y la evaluacion generada por Gemini.

`resumen_evaluacion.csv` incluye `evaluacion_gemini` y queda preparado para revision manual:

```text
Correcta
Incompleta
Incorrecta
```

## Evaluacion

Despues de ejecutar REDUCE, Gemini compara cada `respuesta_final_mapreduce` con `respuesta_correcta_excel`.

Gemini debe devolver solo:

- `Correcta`
- `Incompleta`
- `Incorrecta`

Despues se puede revisar manualmente en los campos:

- `evaluacion_manual`
- `comentario_manual`

Esto permite defender en la memoria que el prototipo no se evalua por "ejecutar sin errores", sino por la calidad de las respuestas finales respecto a las respuestas correctas del Excel.
