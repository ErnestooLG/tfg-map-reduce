## Estrategia map-reduce propuesta

A partir de la evaluación inicial se observó que Gemini mejoraba cuando se reducía el número de documentos utilizados para responder. Por este motivo, la siguiente prueba se plantea con una estrategia map-reduce sencilla.

El caso piloto elegido es la pregunta: “¿Qué asignaturas tienen simultáneamente examen final >40% y prácticas obligatorias?”. Esta pregunta es adecuada porque obliga a revisar muchas asignaturas y aplicar dos condiciones concretas a cada una. Si se pregunta directamente sobre todos los documentos, es fácil que el modelo omita asignaturas o mezcle criterios.

En la fase map se analiza cada asignatura por separado. Para cada PDF, Gemini debe indicar si el examen final supera el 40%, si las prácticas son obligatorias y si se cumplen ambas condiciones. La respuesta se guarda en un formato estructurado para poder revisarla después.

En la fase reduce se juntan todas las respuestas individuales y se separan las asignaturas en tres grupos: las que cumplen claramente, las dudosas y las que no cumplen. Esta separación permite revisar manualmente los casos conflictivos y evita dar por buena una respuesta global sin comprobar de dónde sale.

La implementación queda preparada para ejecutarse mediante la API de Gemini cuando se disponga de la clave. La primera prueba recomendada es ejecutar solo unas pocas asignaturas para comprobar que el formato de salida es correcto y después lanzar el conjunto completo.
