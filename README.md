# Piloto MapReduce TFG

Este proyecto implementa un flujo MapReduce sencillo para analizar varias guias PDF de asignaturas. La pregunta piloto es:

> Que asignaturas tienen simultaneamente examen final >40% y practicas obligatorias?

La idea principal es analizar todas las guias por separado, sin pedir una respuesta global sobre todos los documentos a la vez. En la fase MAP el programa procesa cada guia de forma independiente y despues agrupa todos los resultados parciales en la fase REDUCE.

## Flujo

1. El script busca los PDFs configurados en `config.yaml`.
2. La fase MAP procesa cada PDF de forma independiente.
3. La fase MAP se ejecuta en paralelo usando varios workers (`--workers`).
4. Cada PDF genera un JSON parcial en `outputs/resultados/map/`.
5. Tambien se generan los agregados `outputs/resultados/resultados_map.json` y `outputs/resultados/resultados_map.csv` a partir de esos parciales.
6. La fase REDUCE lee todos los JSON parciales y genera `outputs/resultados/reduce_final.md`.
7. Opcionalmente, `--reduce-llm` genera una redaccion adicional con Gemini.

## Archivos principales

- `config.yaml`: ruta de los PDFs y modelo de Gemini.
- `.env.example`: ejemplo para crear el archivo `.env` local.
- `requirements.txt`: dependencias necesarias.
- `run_map_reduce.py`: script principal.
- `prompts/map_asignatura.txt`: prompt usado para analizar una guia en la fase MAP.
- `prompts/reduce_final.txt`: prompt opcional para el reduce con LLM.
- `outputs/resultados/`: carpeta local de resultados generados.
- `docs/seccion_map_reduce_para_memoria.md`: explicacion breve del enfoque para la memoria.

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

La ruta de las guias se configura en `config.yaml`:

```yaml
ruta_pdfs_por_asignatura: "data/guias"
```

Si los PDFs estan en otra carpeta, solo hay que cambiar esa ruta. Puede ser una ruta relativa al proyecto o una ruta absoluta local.

La clave real de Gemini va solo en un archivo `.env` local. Ese archivo esta incluido en `.gitignore` y no debe subirse al repositorio.

Para crearlo:

```bash
cp .env.example .env
```

En Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

Dentro de `.env` debe quedar una linea de este estilo:

```text
GEMINI_API_KEY=tu_clave_real
```

## Ejemplos de uso

Comprobar que PDFs se detectarian sin llamar a Gemini:

```bash
python run_map_reduce.py --dry-run
```

Procesar primero un subconjunto de 5 PDFs con 3 workers:

```bash
python run_map_reduce.py --limit 5 --workers 3
```

Procesar todos los PDFs encontrados, sin limite, con 3 workers:

```bash
python run_map_reduce.py --workers 3
```

Ejecutar solo la fase REDUCE usando los JSON parciales ya generados:

```bash
python run_map_reduce.py --phase reduce
```

Ejecutar el reduce local y, ademas, una redaccion opcional con Gemini:

```bash
python run_map_reduce.py --phase reduce --reduce-llm
```

## Opciones utiles

- `--dry-run`: lista los PDFs detectados y genera el inventario sin llamar a Gemini.
- `--limit N`: procesa solo los primeros `N` PDFs. Sirve para validar el flujo con un subconjunto antes de ejecutar todas las guias.
- `--workers N`: numero de PDFs que se procesan en paralelo durante la fase MAP. Por defecto usa 3.
- `--phase map`: ejecuta solo la fase MAP.
- `--phase reduce`: ejecuta solo la fase REDUCE leyendo los parciales existentes en `outputs/resultados/map/`.
- `--reduce-llm`: mantiene el reduce local y añade un resumen opcional con Gemini.

Si alguna guia supera los limites admitidos por Gemini, se puede dividir en varios fragmentos PDF y procesarlos como entradas independientes de la fase MAP. En la prueba completa habia 47 guias, pero dos se dividieron en dos partes por superar el limite de paginas, asi que la ejecucion final tuvo 49 entradas MAP.

## Salidas generadas

El script puede generar estos archivos dentro de `outputs/resultados/`:

- `inventario.csv`: listado de PDFs encontrados.
- `map/*.json`: un JSON parcial por PDF procesado.
- `resultados_map.json`: agregado JSON reconstruido desde los parciales.
- `resultados_map.csv`: agregado CSV reconstruido desde los parciales.
- `reduce_final.md`: resultado final local con tres grupos: cumplen, dudosas y no cumplen.
- `reduce_final_llm.md`: salida opcional si se usa `--reduce-llm`.

La carpeta `outputs/resultados/` contiene resultados generados localmente y no se sube al repositorio.

## Entrega

El codigo permite probar primero con un subconjunto usando `--limit` y despues procesar todas las guias quitando ese limite. La fase MAP procesa los PDFs por separado y en paralelo, guarda un resultado parcial por PDF y la fase REDUCE agrupa todos esos resultados parciales.
