# Prueba inicial con Flash-Lite

Fecha de la prueba: 17/06/2026

## Qué he probado

He hecho una primera prueba del piloto map-reduce con la pregunta:

> ¿Qué asignaturas tienen simultáneamente examen final >40% y prácticas obligatorias?

La prueba se ha hecho muy limitada para no gastar de más. Primero lancé un `--dry-run`, que solo comprueba que el script encuentra los PDFs. Después hice una prueba real con un único PDF usando `--limit 1`.

No he lanzado todavía todos los documentos.

## Modelo usado

En `config.yaml` dejé configurado:

```text
gemini-2.5-flash-lite
```

La idea es empezar con un modelo de la familia Flash-Lite para controlar el coste antes de probar con más documentos.

## Comandos ejecutados

Preparé un entorno virtual para instalar las dependencias sin tocar el Python global:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Después ejecuté:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --dry-run
```

Y para la primera prueba real:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --limit 1
```

## Resultado de la prueba

El `--dry-run` encontró 47 PDFs.

La prueba real final procesó solo este archivo:

```text
Todo 1/ALGE.pdf
```

El resultado obtenido fue:

- Curso: `Todo 1`.
- Asignatura/PDF: `Algebra` / `ALGE.pdf`.
- Examen final >40%: `si`.
- Porcentaje indicado: `70% (convocatoria global) y 100% (convocatoria extraordinaria)`.
- Prácticas obligatorias: `no`.
- Cumple ambas condiciones: `no`.

El resumen local dejó:

- Cumplen: 0.
- Dudosas: 0.
- No cumplen: 1.

## Incidencias

El primer intento de ejecutar el script falló porque faltaba `PyYAML` en el Python global. Lo solucioné creando `.venv` e instalando las dependencias desde `requirements.txt`.

En una de las respuestas, Gemini devolvió una contradicción: marcaba que las prácticas no eran obligatorias, pero a la vez indicaba que la asignatura cumplía ambas condiciones. Para evitar ese tipo de fallo, dejé una comprobación sencilla en el script: el campo final se recalcula a partir de las dos condiciones principales.

Así, si el examen final es `si` pero las prácticas obligatorias son `no`, el resultado final pasa a ser `no`.

## Archivos generados

La ejecución generó estos archivos locales:

- `outputs/resultados/inventario.csv`
- `outputs/resultados/resultados_map.json`
- `outputs/resultados/resultados_map.csv`
- `outputs/resultados/reduce_final.md`

Estos resultados se quedan como salida local de la prueba. No los subiría todavía al repositorio.

## Próximo paso

Antes de seguir, revisaría manualmente el caso de `ALGE.pdf`, sobre todo la interpretación de las prácticas obligatorias.

Si el resultado parece correcto, el siguiente paso sería probar con tres PDFs como máximo:

```powershell
.\.venv\Scripts\python.exe run_map_reduce.py --limit 3
```

Después de eso ya se podría decidir si merece la pena ampliar la prueba a más asignaturas. Por ahora prefiero no lanzar todos los PDFs para controlar el coste.

La clave real sigue estando solo en `.env`, que está ignorado por Git.
