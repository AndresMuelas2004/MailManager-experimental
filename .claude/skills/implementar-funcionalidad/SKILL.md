---
name: implementar-funcionalidad
description: "Ejecuta de forma autónoma la implementación de una funcionalidad ya planificada, a partir de los tres .md que la describen (backend, frontend y descripción general). Encadena en orden los subagentes de implementación backend-implementer y frontend-implementer, y cierra siempre, en este orden, con tests-author-from-diff, guides-updater-from-diff y docs-updater-from-diff. No replanifica ni pide aprobación. Invocación manual."
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

Cada subagente arranca con una **ventana de contexto limpia**: no ve esta conversación, ni los `.md`, ni lo que leyeron los subagentes anteriores. Su **único canal de entrada es el task prompt** con el que lo lanzas, así que **toda ruta que necesite debe ir explícita en él** — nada se hereda solo. Entre subagentes secuenciales, la información viaja por el **working tree de git**: cada uno deja sus cambios sin commitear y el siguiente los descubre por el diff.

## Paso 1 — Resolver y validar las rutas

Antes de lanzar ningún subagente:

1. Resuelve las **tres rutas absolutas** desde el argumento (lista el directorio si hace falta e identifícalas por sufijo).
2. Si el directorio contiene **más de un trío** de documentos (varios slugs), **detente y pregúntame** cuál implementar.
3. Si **falta** alguno de los tres o **no existe en disco**, **detente y dímelo en una línea** (p. ej. `"falta el …-frontend.md"`). No inventes rutas, no rastrees el filesystem, no implementes nada.

**No leas el contenido de los `.md`**: cada subagente lee los suyos. Tu papel es orquestar y pasar las rutas correctas.

## Reglas de orquestación (válidas para toda la cadena)

- **Estrictamente secuencial.** Lanza un subagente, **espera** su resultado y solo entonces lanza el siguiente. Nunca en paralelo: todos operan sobre el mismo working tree de git, y el diff que deja uno es la entrada del siguiente; solaparlos corrompería esa entrada.
- **Tú encadenas.** Los subagentes no se llaman entre sí. Los lanzas tú, uno tras otro, con la herramienta Agent y el `subagent_type` indicado en cada paso.
- **Parada ante bloqueo.** Si un subagente **no completa** su tarea —devuelve una descripción de bloqueo en lugar de `done`, responde `misión abortada`, o falla— **detén la cadena ahí**, no lances el siguiente y repórtame el bloqueo literal. Un `misión abortada` en el paso de tests significa que la implementación no produjo cambios: párate y avísame.

## Paso 2 — Implementación backend · `backend-implementer`

Lánzalo con un task prompt que incluya las **tres rutas absolutas**, explicando el papel de cada una:

- `…-backend.md` — **referencia principal** de lo que hay que implementar: un plan de alta calidad pero falible, no un guion literal. El subagente lo completa con criterio donde su lectura del código revele huecos técnicos y escala lo que sea decisión de diseño no fijada (su propio `.md` detalla esa frontera).
- `…-general-description.md` — léelo **una vez al principio**, solo para hacerte una idea inicial de la funcionalidad (nivel usuario, sin detalle técnico). No es una especificación.
- `…-frontend.md` — **no lo leas para implementar** (su contenido de UX/componentes solo ensuciaría el contexto del backend). Ábrelo **solo al final** y **exclusivamente** para alinear su **sección de endpoints** con el contrato realmente implementado.

Espera a `done`. Si bloquea, detente y reporta.

## Paso 3 — Implementación frontend · `frontend-implementer`

Lánzalo con un task prompt que incluya las **dos rutas absolutas**:

- `…-frontend.md` — **referencia principal** (plan de alta calidad pero falible en el trabajo de frontend; el subagente lo completa con criterio donde el código lo exija). Su sección de endpoints ya quedó alineada en el paso 2 y se asume como **contrato fijo** —ahí no improvisa—, así que el frontend se integra sin leer el backend.
- `…-general-description.md` — léelo **una vez al principio**, solo para la idea inicial de la funcionalidad.

Espera a `done`. Si bloquea, detente y reporta.

## Paso 4 — Tests · `tests-author-from-diff`

Lánzalo **sin rutas de `.md`**: descubre por sí mismo lo implementado leyendo el **diff del working tree**. Task prompt mínimo, p. ej.: *«Escribe los tests de la funcionalidad recién implementada, trabajando solo desde los cambios sin commitear del working tree.»*

## Paso 5 — Guías técnicas · `guides-updater-from-diff`

Igual que el anterior: **sin rutas de `.md`**, trabaja desde el diff. Actualiza los `*_guide.md`, el `repository_guide.md` y el `README.md` raíz cuando proceda. **Nunca** toques los `CLAUDE.md` ni la capa `docs/`.

## Paso 6 — Documentación de cierre · `docs-updater-from-diff`

Paso final. Su task prompt **debe incluir la ruta absoluta del `…-general-description.md`**: la necesita como fuente del **porqué** de las decisiones; el resto —comportamiento y cifras— lo verifica contra el diff. Actualiza **solo** la capa `docs/`.

## Cierre

Cuando terminen los seis pasos —o la cadena se detenga por un bloqueo—, devuélveme un **resumen breve**: qué subagentes corrieron, cuál bloqueó (si alguno) y por qué.
