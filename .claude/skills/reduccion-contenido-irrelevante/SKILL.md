---
name: reduccion-contenido-irrelevante
description: "Audit editorial de archivos *_guide.md del proyecto: elimina solo el contenido que puede reconstruirse leyendo el código en ~30 segundos, conservando trampas silenciosas, asimetrías cross-file, invariantes implícitas, decisiones históricas e identificadores fijos. Procesa N archivos en paralelo con fan-out de investigadores por archivo y validadores por candidata, con una única confirmación humana antes de tocar nada."
argument-hint: rutas a uno o varios *_guide.md a auditar (espacio-separadas)
disable-model-invocation: true
allowed-tools: Read Edit Agent
model: opus
effort: max
---

$ARGUMENTS

# Audit editorial — reducción de contenido irrelevante en archivos `*_guide.md`

## ROL

Actúas como auditor editorial de uno o varios archivos `*_guide.md` del proyecto MailManager. Tu trabajo es identificar y eliminar el contenido que no cumple con el criterio de admisión que el propio archivo declara en su cabecera, sin tocar nada que sí lo cumpla.

## ARCHIVOS OBJETIVO

Las rutas de los archivos a auditar están en `$ARGUMENTS` (una o varias, espacio-separadas). Cada archivo es un `*_guide.md` (`repository_guide.md`, `api_guide.md`, `core_guide.md`, `database_guide.md`, etc.). Todos comparten el mismo criterio de admisión declarado en su cabecera, por lo que el audit es uniforme entre ellos.

## CRITERIO DE ADMISIÓN (literal — usado por los subagentes como vara de medir)

A continuación, el bloque CRITERIO_DE_ADMISION que debes pasar **textualmente** a cada subagente validador en FASE 3. No lo resumas ni lo parafrasees al copiarlo.

```
<<<CRITERIO_DE_ADMISION_INICIO>>>
Una línea en un *_guide.md sólo justifica sus tokens si NO puede reconstruirse leyendo el código.

- Si la respuesta a "¿podría reconstruir esto abriendo los archivos relevantes durante ~30 segundos?" es SÍ → bórralo. Caen aquí:
  · catálogos de lo que hacen módulos/funciones/tests,
  · paráfrasis de nombres o cuerpos,
  · enumeraciones exhaustivas de kwargs/campos/config,
  · tablas de flujo que reflejan nombres de archivo/símbolos existentes,
  · recetas paso a paso de código que ya es legible por sí mismo.

- Si la respuesta es NO → mantenlo. Caen aquí:
  · trampas silenciosas al extender la capa,
  · asimetrías cross-file (hermanos que no se comportan igual),
  · reglas de ordenación/ciclo de vida cuya violación rompe todo,
  · invariantes cuya regresión silenciosa pasaría revisión,
  · decisiones históricas cuyo motivo no está en el código,
  · identificadores fijos (UUIDs, datos seed, constantes mágicas) que no se pueden recomputar.

En caso de duda razonable, CONSERVAR. Este sistema favorece falsos negativos sobre la pérdida de información valiosa.
<<<CRITERIO_DE_ADMISION_FIN>>>
```

Además, cada `*_guide.md` declara este criterio en su propia cabecera (con redacción equivalente). Esa cabecera es parte del archivo y **nunca se elimina**: es la regla que se está aplicando al resto del documento.

## ENTREGABLE FINAL

Una versión reescrita de cada archivo pasado por argumento, en la que se ha eliminado únicamente el contenido que falla el criterio anterior **y que además ha sido ratificado por su subagente validador**. Todo lo demás permanece intacto, byte a byte: la cabecera del criterio, el orden de secciones, el formato Markdown, los enlaces internos `@archivo.md`, los bloques de código.

## INSTRUCCIONES

Procede en seis fases. **No te saltes ninguna ni cambies el orden.** Comunica al usuario en qué fase estás al inicio de cada una con una sola frase.

> **Por qué el paralelismo vive en la sesión principal.** Los subagentes lanzados con la tool `Agent` no pueden lanzar a su vez más subagentes (limitación arquitectural documentada). Por eso esta skill orquesta dos niveles de fan-out desde aquí: primero N investigadores en paralelo (uno por archivo) en FASE 1; luego M validadores en paralelo (uno por candidata, de cualquier archivo) en FASE 3. Los subagentes son siempre hojas, nunca nodos intermedios.

---

### FASE 1 — Investigación paralela por archivo

Lanza **un subagente `general-purpose` por cada archivo** pasado en `$ARGUMENTS`, todos en una **única respuesta** con múltiples bloques de tool-call paralelos (fan-out simultáneo). Cada investigador trabaja sobre un único archivo de forma independiente y devuelve la lista de bloques candidatos a eliminar.

Prompt **EXACTO** que debes pasar a cada subagente investigador (sustituyendo `{RUTA_ARCHIVO}` y `{CRITERIO_DE_ADMISION}`):

```
Eres un investigador editorial. Tu única tarea es identificar los bloques candidatos a eliminar en UN archivo *_guide.md. NO valides nada — solo identifica. NO edites el archivo. NO lances subagentes.

ARCHIVO A INVESTIGAR (ruta absoluta):
{RUTA_ARCHIVO}

CRITERIO DE ADMISIÓN (úsalo como única vara para identificar candidatas):
{CRITERIO_DE_ADMISION}

TU PROTOCOLO
1. Lee el archivo completo de una sola vez con la tool Read.
2. Recorre el documento de arriba a abajo identificando bloques candidatos a eliminar. Un "bloque candidato" es una unidad coherente: un bullet, una "Note:" completa, un párrafo, o una subsección enmarcada. NO marques fragmentos de frase sueltos — si una frase dentro de una Note sobra pero la Note como conjunto sí aporta, ignórala (este audit no reescribe a nivel de frase, sólo a nivel de bloque).
3. NO marques nunca como candidata la cabecera del archivo que declara el propio criterio de admisión: esa cabecera es la regla que se está aplicando, no contenido auditable.
4. Para cada bloque candidato anota: cita literal completa, número de línea inicial y final, e hipótesis de por qué falla el criterio (p. ej. "paráfrasis de nombres de funciones que ya están en services_helpers.py", "tabla de flujo que duplica imports visibles en email_html_pipeline.py").

FORMATO DE REPORTE (devuelve esto y nada más, en este orden, como un bloque JSON parseable):

{
  "archivo": "{RUTA_ARCHIVO}",
  "candidatas": [
    {
      "id": 1,
      "linea_inicio": 42,
      "linea_fin": 47,
      "cita_literal": "...texto completo del bloque, sin truncar...",
      "hipotesis": "una frase explicando por qué falla el criterio"
    },
    {
      "id": 2,
      ...
    }
  ]
}

Si no encuentras ninguna candidata, devuelve "candidatas": []. NO propongas reescrituras parciales. NO opines sobre otros archivos. NO ejecutes cambios.
```

Cuando todos los investigadores terminen, recoge sus reportes y mantenlos en memoria de la sesión principal para las fases siguientes.

---

### FASE 2 — Presentación de candidatas al usuario (informativa, NO bloqueante)

Imprime en el chat la lista completa de candidatas **agrupadas por archivo**, en este formato:

```
Archivo: {ruta}
  - Candidata #N (líneas X-Y): «primeros ~120 caracteres del bloque…»
    Hipótesis: {una frase}
  - Candidata #N+1 ...
```

Esto es informativo — para que el usuario vea qué entra al doble filtrado. **No pidas confirmación aquí**, sigue directamente a FASE 3.

---

### FASE 3 — Validación paralela por candidata

Lanza **un subagente `general-purpose` por cada candidata identificada en FASE 1**, todos en paralelo. La unidad de paralelización es la candidata individual, no el archivo: una candidata del archivo A y una candidata del archivo B se validan en el mismo turno como dos subagentes independientes.

**Regla de batch:** no excedas 8 subagentes paralelos por turno. Si el total de candidatas es > 8, agrúpalas en batches de 8 y lanza un batch por respuesta hasta agotarlas. Mantén el orden estable (por archivo, luego por id) para que sea trazable.

Prompt **EXACTO** que debes pasar a cada subagente validador (sustituyendo los placeholders):

```
Eres un validador editorial. Tu única tarea es decidir si UN bloque específico debe eliminarse de un archivo *_guide.md o conservarse. NO analices nada más del documento. NO edites el archivo. NO lances subagentes.

CRITERIO DE ADMISIÓN del archivo (cópialo y úsalo como única vara de medir):
{CRITERIO_DE_ADMISION}

BLOQUE A EVALUAR (cita literal):
{CITA_LITERAL_CANDIDATA}

LOCALIZACIÓN en el archivo:
{RUTA_ARCHIVO}, líneas {LINEA_INICIO}-{LINEA_FIN}.

TU PROTOCOLO
1. Lee el bloque a evaluar. Identifica todas las afirmaciones concretas que hace (referencias a funciones, archivos, comportamientos, invariantes, decisiones, identificadores).
2. Para cada afirmación, abre con Read/Grep/Glob los archivos de código que correspondan y verifica si la información puede reconstruirse leyendo el código en ~30 segundos o si, por el contrario, es un saber que no está en el código (trampa silenciosa, asimetría cross-file, invariante implícita, decisión histórica, identificador fijo, etc.).
3. Decide: ELIMINAR si todas las afirmaciones del bloque son reconstruibles desde el código; CONSERVAR si al menos una afirmación cae en las categorías protegidas del criterio. En caso de duda razonable, CONSERVAR (este sistema favorece falsos negativos sobre la pérdida de información valiosa).

FORMATO DE REPORTE (devuelve esto y nada más, en este orden):
- VEREDICTO: ELIMINAR | CONSERVAR
- RAZÓN PRINCIPAL: una frase.
- EVIDENCIA: lista de archivos consultados con path:line cuando aplique, indicando para cada afirmación del bloque si es o no reconstruible desde el código.
- AFIRMACIONES PROTEGIDAS (sólo si CONSERVAR): qué frase(s) concretas del bloque le dan derecho a quedarse.

NO propongas reescrituras parciales. NO opines sobre otros bloques. NO ejecutes cambios. NO lances subagentes.
```

---

### FASE 4 — Consolidación de veredictos

Recoge los reportes de todos los validadores. Construye una **tabla por archivo** con: ID de candidata, rango de líneas, veredicto, razón principal.

**Regla de decisión: el subagente validador es la autoridad final.** Si tu hipótesis inicial era "eliminar" pero el validador votó "conservar", el bloque se queda — sin renegociar, sin pedir un segundo subagente.

---

### FASE 5 — Confirmación humana única antes de tocar archivos

Presenta al usuario la **lista definitiva de bloques a eliminar**, agrupados por archivo, con: cita literal + rango de líneas + razón del validador. Esta es **una sola confirmación para todas las eliminaciones de todos los archivos**, no una por archivo.

Pregunta explícitamente: «¿Procedo con estas eliminaciones?». **No toques ningún archivo hasta recibir aprobación.** Si el usuario pide excluir alguna candidata de la lista, exclúyela del set a aplicar y sigue con el resto.

---

### FASE 6 — Reescritura paralela

1. Aplica las eliminaciones aprobadas usando `Edit`, **un `Edit` por bloque**, con `old_string` = cita literal exacta del bloque y `new_string` = `""` (o el ajuste mínimo para colapsar dobles líneas en blanco que quedarían contiguas). Las ediciones de archivos distintos pueden ir en una misma respuesta como bloques de tool-call paralelos — las de un mismo archivo van también en paralelo siempre que sus `old_string` no se solapen.
2. **No fusiones eliminaciones de bloques adyacentes en un único `Edit` con un `old_string` largo:** hazlo bloque a bloque para que cualquier fallo de match sea localizable.
3. Tras todas las ediciones, **releé cada archivo entero con `Read`** (en paralelo, una llamada por archivo) y verifica: (a) la cabecera del criterio sigue intacta, (b) no han aparecido dobles líneas en blanco no intencionales ni rupturas de listas Markdown, (c) los enlaces internos `@archivo.md` y las referencias a rutas siguen exactos, (d) los bloques de código no se han roto. Si detectas un desperfecto cosmético, arréglalo con un `Edit` quirúrgico adicional y vuelve a verificar.
4. Reporta al usuario por archivo: cuántos bloques se eliminaron, cuántos se conservaron tras la validación, y el delta aproximado en líneas (antes vs. después).

---

## REGLAS DURAS

- **No modifiques ningún `CLAUDE.md`** bajo ninguna circunstancia. Están protegidos por hook y además no son objeto de este audit. Si por error una de las rutas pasadas en `$ARGUMENTS` es un `CLAUDE.md`, descártala con un aviso al usuario y continúa con el resto.
- **No elimines bloques que el validador votó CONSERVAR**, aunque sigas pensando que sobran. La autoridad la tiene el validador.
- **No fusiones eliminaciones en un único `Edit`**: un `Edit` por bloque, sin excepción.
- **No reescribas ni resumas el contenido conservado**. Este audit es elimina-o-deja, no reescribe.
- **No toques el código fuente.** Solo los `*_guide.md` listados en `$ARGUMENTS`.
- **No alteres la cabecera del criterio de admisión** de ninguno de los archivos auditados: es la regla que se está aplicando, no contenido auditable.
- **No ejecutes nada en paralelo desde dentro de un subagente.** Los subagentes son hojas: solo leen, validan y devuelven texto. Todo el fan-out ocurre desde la sesión principal.

## CRITERIO DE ÉXITO

El audit termina con éxito si simultáneamente:

1. Cada bloque eliminado fue **ratificado por su validador** (veredicto ELIMINAR explícito).
2. Ningún bloque con veredicto CONSERVAR fue tocado.
3. El formato Markdown sigue válido en todos los archivos auditados (sin dobles líneas en blanco no intencionales, sin listas rotas, sin bloques de código mal cerrados).
4. Los enlaces internos `@archivo.md` y las rutas referenciadas siguen exactos.
5. La cabecera con el criterio de admisión permanece literalmente igual en cada archivo.
6. El usuario aprobó la lista final en FASE 5 antes de cualquier `Edit`.
