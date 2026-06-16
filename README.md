# Piloto map-reduce TFG

Este proyecto es una versión mínima para probar la idea: dividir una pregunta grande en preguntas pequeñas por asignatura y después juntar las respuestas.

Caso piloto:

¿Qué asignaturas tienen simultáneamente examen final >40% y prácticas obligatorias?

Idea:

1. Se hace un inventario de los PDFs de asignaturas.
2. En la fase **map**, se pregunta a Gemini por cada asignatura por separado.
3. En la fase **reduce**, se juntan los resultados y se separan las asignaturas que cumplen, las dudosas y las que no cumplen.

## Qué hace cada archivo

- `config.yaml`: guarda las rutas de las carpetas y el modelo de Gemini. Si muevo las carpetas, solo tengo que cambiar este archivo.
- `.env.example`: plantilla para crear el archivo `.env`, donde irá la API key cuando Javier me la pase.
- `requirements.txt`: librerías necesarias para ejecutar el script.
- `run_map_reduce.py`: script principal. Hace el inventario, ejecuta la fase map y genera el reduce final.
- `prompts/map_asignatura.txt`: prompt que se usa para analizar una asignatura concreta.
- `prompts/reduce_final.txt`: prompt opcional para que Gemini redacte una respuesta final a partir de los resultados map.
- `outputs/resultados/`: carpeta donde se guardan el inventario y los resultados.
- `outputs/memoria/seccion_map_reduce_para_memoria.md`: texto base para copiar en la memoria.

## Instalación

```powershell
cd "C:\Users\ernes\OneDrive\Desktop\UNI\UPM\5\TFG\map_reduce"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Probar sin API key

```powershell
python run_map_reduce.py --dry-run
```

Esto solo comprueba las carpetas y genera el inventario de PDFs. No llama a Gemini.

## Ejecutar con API key

Cuando tenga la clave:

```powershell
Copy-Item .env.example .env
```

Después abro `.env` y pongo:

```text
GEMINI_API_KEY=tu_clave_real
```

Primera prueba pequeña:

```powershell
python run_map_reduce.py --limit 3
```

Ejecución completa:

```powershell
python run_map_reduce.py
```

Si quiero que Gemini redacte también el reduce final a partir de los resultados:

```powershell
python run_map_reduce.py --phase reduce --reduce-llm
```

## Salidas

El script genera:

- `outputs/resultados/inventario.csv`
- `outputs/resultados/resultados_map.csv`
- `outputs/resultados/resultados_map.json`
- `outputs/resultados/reduce_final.md`
- `outputs/resultados/reduce_final_llm.md` si se usa `--reduce-llm`

## Qué queda pendiente

- Revisar el prompt con el tutor.
- Meter la API key real.
- Ejecutar primero una prueba pequeña.
- Revisar manualmente los casos dudosos.
