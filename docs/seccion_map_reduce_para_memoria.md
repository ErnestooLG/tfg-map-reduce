## Estrategia MapReduce propuesta

A partir de la evaluacion inicial se observo que el analisis mejora cuando no se pide al modelo una respuesta global sobre todos los documentos a la vez. Por este motivo, se plantea una estrategia MapReduce sencilla para revisar las guias docentes de forma separada y despues agrupar los resultados.

El prototipo inicial se valido con una pregunta piloto sobre evaluacion. La version final mantiene esa idea, pero permite ejecutar cinco preguntas reales de validacion. Para evitar que el modelo mezcle criterios, las preguntas no se mandan juntas: el sistema selecciona una pregunta, procesa todos los PDFs con esa unica pregunta y solo despues pasa a la siguiente.

Las guias PDF se colocan directamente en `data/guias/`. En la fase MAP, cada guia se analiza de forma independiente. Para cada PDF, Gemini recibe solamente la pregunta actual y debe indicar si ese documento aporta informacion, no aporta informacion o queda como dudoso. Cada analisis genera un resultado parcial en formato JSON, con respuesta parcial, evidencia, confianza, observaciones y posibles errores.

El sistema calcula `asignatura_base` a partir del nombre del archivo. Esto evita contar dos veces una misma asignatura si se ha dividido en varios PDFs, por ejemplo `CDPS_parte1` y `CDPS_parte2`, que se agrupan como `CDPS`, o `IWEB_parte1` y `IWEB_parte2`, que se agrupan como `IWEB`.

La fase MAP puede ejecutarse en paralelo usando varios workers. Aunque varios PDFs se procesen al mismo tiempo, cada llamada mantiene dos aislamientos: un solo PDF y una sola pregunta. Esto facilita revisar los resultados y defender que el modelo no esta resolviendo varias tareas a la vez.

En la fase REDUCE se leen unicamente los resultados parciales de la pregunta actual. El reduce agrupa los PDFs que aportan informacion, los casos dudosos, los casos sin informacion y los errores. En la pregunta sobre trabajo en grupo se calcula el porcentaje usando asignaturas base, no numero de PDFs.

Cada pregunta genera su propia carpeta dentro de `outputs/resultados/por_pregunta/`, con sus parciales MAP, sus agregados CSV/JSON y su `reduce_final.md`. Al terminar, el sistema genera tambien `outputs/resultados/resumen_validacion.csv` y `outputs/resultados/resumen_validacion.md`, que resumen para cada pregunta cuantos PDFs fueron relevantes, dudosos, sin informacion o erroneos.

El flujo permite probar primero con un subconjunto de guias usando `--limit`, por ejemplo `--limit 5`, para comprobar que la salida es correcta y controlar el coste. Una vez validada la prueba, se ejecuta el conjunto completo quitando `--limit`, de modo que cada pregunta se evalua sobre todas las guias PDF encontradas.
