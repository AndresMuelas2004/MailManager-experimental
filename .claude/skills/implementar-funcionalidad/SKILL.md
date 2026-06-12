---
name: implementar-funcionalidad
description: "Ejecuta de forma autónoma la implementación de una funcionalidad ya planificada, a partir de los tres .md que la describen (backend, frontend y descripción general). Encadena en orden los subagentes de implementación backend-implementer y frontend-implementer, y cierra siempre, en este orden, con tests-author-from-diff, guides-updater-from-diff, docs-updater-from-diff y, como red de seguridad del entorno Python, deps-syncer-from-diff. No replanifica ni pide aprobación. Invocación manual."
argument-hint: ruta al directorio (o a los tres archivos) con los .md de la funcionalidad — backend, frontend y general-description
disable-model-invocation: true
model: opus
effort: max
---

# Implementar una funcionalidad ya planificada

**Ejecuta** la implementación de una funcionalidad cuyo diseño ya está cerrado en tres documentos `.md`: orquesta una cadena fija de subagentes —cada uno en su propio contexto aislado— y te devuelve el resultado.

**No planifiques, no explores alternativas y no pidas aprobación de ningún plan.** Esta skill solo ejecuta.

## Entrada

`$ARGUMENTS` apunta a los **tres documentos** que describen la funcionalidad. Pueden llegar como un directorio que los contiene o como las tres rutas explícitas. Identifícalos por su sufijo:

| Rol | Sufijo |
|---|---|
| Backend | `-backend.md` |
| Frontend | `-frontend.md` |
| Descripción general | `-general-description.md` |

## Aislamiento de contexto

Cada subagente arranca con una **ventana de contexto limpia**: no ve esta conversación, ni los `.md`, ni lo que leyeron los subagentes anteriores. Su **único canal de entrada es el task prompt** con el que lo lanzas, así que **toda ruta que necesite debe ir explícita en él** — nada se hereda solo.

## Paso 1 — Resolver y validar las rutas

Antes de lanzar ningún subagente:

1. Resuelve las **tres rutas absolutas** desde el argumento (lista el directorio si hace falta e identifícalas por sufijo).
2. Si el directorio contiene **más de un trío** de documentos (varios slugs) y no te he dejado yo claro en los argumentos que te paso al ejecutar la skill cuáles son los tres correspondientes a usar para la implementación, **detente y pregúntame** cuál implementar.
3. Si **falta** alguno de los tres o **no existe en disco**, **detente y dímelo en una línea** (p. ej. `"falta el …-frontend.md"`). No inventes rutas, no rastrees el filesystem, no implementes nada.

**No leas el contenido de los `.md`**: cada subagente lee los suyos. Tu papel es orquestar y pasar las rutas correctas.

## Reglas de orquestación (válidas para toda la cadena)

- **Estrictamente secuencial.** Lanza un subagente, **espera** su resultado y solo entonces lanza el siguiente. Nunca en paralelo: todos operan sobre el mismo working tree de git, y en el caso de los subagentes de tests y documentación: el diff que dejan los de frontend y backend es la entrada del de tests, y los diffs de los tests más los de los agentes frontend y backend son la entrada de los agentes de documentación; solaparlos corrompería esa entrada.
- **Tú encadenas.** Los subagentes no se llaman entre sí. Los lanzas tú, uno tras otro, con la herramienta Agent y el `subagent_type` indicado en cada paso.
- **Parada ante bloqueo.** Si un subagente **no completa** su tarea —devuelve una descripción de bloqueo en lugar de `done`, responde `misión abortada`, o falla— **detén la cadena ahí**, no lances el siguiente y repórtame el bloqueo literal. Un `misión abortada` en el paso de tests significa que la implementación no produjo cambios: párate y avísame.

## Paso 2 — Implementación backend · `backend-implementer`

@backend-implementer
Lánzalo con un task prompt que incluya las **tres rutas absolutas**, explicando el papel de cada una:

- `…-backend.md` — **referencia principal** de lo que hay que implementar: un plan de alta calidad pero falible, no un guion literal. El subagente lo completa con criterio donde su lectura del código revele huecos técnicos y escala lo que sea decisión de diseño no fijada.
- `…-general-description.md` — léelo **una vez al principio**, solo para hacerte una idea inicial de la funcionalidad (nivel usuario, sin detalle técnico). No es una especificación.
- `…-frontend.md` — **no lo leas para implementar** (su contenido de UX/componentes solo ensuciaría el contexto del backend). Ábrelo **solo al final** y **exclusivamente** para alinear su **sección de endpoints** con el contrato realmente implementado.

Además, este subagente **actualiza por su cuenta** las secciones de **tests** y **documentación** del `…-backend.md` si durante la implementación se desvía del plan: esas dos secciones son la entrada de los pasos 4 y 5, así que deben quedar fieles a lo realmente implementado.

Espera a `done`. Si bloquea, detente y reporta.

## Paso 3 — Implementación frontend · `frontend-implementer`

@frontend-implementer
Lánzalo con un task prompt que incluya las **dos rutas absolutas**:

- `…-frontend.md` — **referencia principal** (plan de alta calidad pero falible en el trabajo de frontend; el subagente lo completa con criterio donde el código lo exija). Su sección de endpoints ya quedó alineada en el paso 2 y se asume como **contrato fijo** —ahí no improvisa—, así que, por norma, el frontend se integra sin leer el backend (a lo sumo una consulta puntual y acotada si algo de esa sección no cuadra). Dicho archivo tiene una sección de tests que deberá ser actualizada por el subagente en cuestión solo en caso de ser necesario.
- `…-general-description.md` — léelo **una vez al principio**, solo para la idea inicial de la funcionalidad.

Espera a `done`. Si bloquea, detente y reporta.

## Paso 4 — Tests · `tests-author-from-diff`

@tests-author-from-diff
Lánzalo solo con dos rutas md **…-backend.md y …-frontend.md**: descubre por sí mismo lo implementado leyendo el **diff del working tree** y solo y exclusivamente la sección tests de ambos archivos md cuyas rutas deberás pasarle en el task prompt. Task prompt mínimo, p. ej.: *«Escribe los tests de la funcionalidad recién implementada, trabajando solo desde los cambios sin commitear del working tree y desde la sección tests de dichos archivos md mencionados (mención literal a los archivos).»*

## Paso 5 — Guías técnicas · `guides-updater-from-diff`

@guides-updater-from-diff
Con una ruta md **…-backend.md**, trabaja desde el diff y desde solo la sección de documentación del archivo md mencionado …-backend.md. Actualiza los `*_guide.md`, el `repository_guide.md` y el `README.md` raíz cuando proceda. **Nunca** toques los `CLAUDE.md` ni la capa `docs/`.

## Paso 6 — Documentación de cierre · `docs-updater-from-diff`

@docs-updater-from-diff
Último paso de documentación (tras él solo corre el Paso 7, la red de seguridad de dependencias). Su task prompt **debe incluir la ruta absoluta del `…-general-description.md`**: la necesita como fuente del **porqué** de las decisiones; el resto —comportamiento y cifras— lo verifica contra el diff. Analiza los diffs existentes de la nueva implementación para conocer todos los detalles y actualiza **solo** la capa `docs/`. Existe la posibilidad de que la funcionalidad no sea nueva sino que sea una modificación grande de una anterior, menciónale también en el task prompt que existe esa posibilidad y que el archivo md en cuestión `…-general-description.md` se la aclarará. En ese mismo task prompt incluye literalmente una indicación del tipo: «Si el md en cuestión nombra que es una feature ya existente, acuérdate de no crear nuevos md en `docs/` sino de actualizar los de la feature ya existente».

## Paso 7 — Sincronización de dependencias · `deps-syncer-from-diff`

@deps-syncer-from-diff
Red de seguridad final del entorno Python, para que cuando el usuario corra los tests no fallen por una librería que faltaba en el `.venv` o por una dependencia que el código nuevo usa pero nadie declaró. Trabaja **solo desde el diff** (no necesita ningún `.md`): sincroniza el `.venv` desde los dos manifiestos y detecta, instala y fija (pinneadas) las dependencias de terceros ausentes, actualizando **ambos** `requirements.txt` según la convención de cada uno. Task prompt mínimo, p. ej.: *«Sincroniza las dependencias de la funcionalidad recién implementada trabajando solo desde el diff sin commitear del working tree: instala en `.venv` ambos manifiestos (`requirements.txt` raíz y `backend/requirements.txt`) y añade a ellos las dependencias de terceros nuevas que falten.»*

A diferencia de los pasos anteriores, este es **best-effort**: si reporta un problema (fallo de red de pip, un nombre de paquete que no pudo resolver), inclúyelo en el resumen de cierre pero **no invalida** la implementación ya hecha. Trátalo como bloqueo de la cadena **solo** si devuelve `misión abortada` (no había diff: algo va muy mal).

## Cierre

Cuando terminen los siete pasos (seis subagentes encadenados) —o la cadena se detenga por un bloqueo—, devuélveme un **resumen breve**: qué subagentes corrieron, cuál bloqueó (si alguno) y por qué. Incluye también, si lo hubo, lo que `deps-syncer-from-diff` instaló o añadió a los `requirements.txt`.
