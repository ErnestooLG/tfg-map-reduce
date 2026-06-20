## Estrategia MapReduce propuesta

A partir de la evaluacion inicial se observo que el analisis mejora cuando no se pide al modelo una respuesta global sobre todos los documentos a la vez. Por este motivo, se plantea una estrategia MapReduce sencilla para revisar las guias docentes de forma separada y despues agrupar los resultados.

El caso piloto elegido es la pregunta: "Que asignaturas tienen simultaneamente examen final >40% y practicas obligatorias?". Esta pregunta es adecuada porque obliga a revisar muchas asignaturas y aplicar dos condiciones concretas a cada una.

En la fase MAP, cada guia PDF se analiza de forma independiente. Para cada PDF, Gemini debe indicar si el examen final supera el 40%, si las practicas son obligatorias y si se cumplen ambas condiciones. Cada analisis genera un resultado parcial en formato JSON, lo que facilita revisar errores, evidencias y observaciones por asignatura.

La fase MAP se ejecuta en paralelo usando varios workers. De esta forma, varios PDFs pueden procesarse a la vez sin mezclar sus respuestas, ya que cada PDF mantiene su propio resultado parcial.

En la fase REDUCE se leen todos los resultados parciales generados por la fase MAP y se agrupan las asignaturas en tres bloques: asignaturas que cumplen ambas condiciones, asignaturas dudosas y asignaturas que no cumplen. Este reduce se realiza de forma local, sin necesidad de llamar de nuevo al modelo, aunque se mantiene una opcion adicional para redactar un resumen con LLM.

El flujo permite probar primero con un subconjunto de guias usando `--limit`, por ejemplo `--limit 5`, para comprobar que la salida es correcta y controlar el coste. Una vez validada la prueba, se ejecuta el conjunto completo quitando `--limit`, de modo que se procesan todas las guias PDF encontradas.
