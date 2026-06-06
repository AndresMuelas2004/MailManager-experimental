---
name: planear-implementacion-funcionalidad
description: "Fase de investigación y planificación previa a la implementación: investiga el código afectado y las APIs externas implicadas, resuelve contigo todas las dudas y decisiones de diseño, y genera los tres documentos .md (backend, frontend y descripción general) que dejan la funcionalidad lista para implementar. Invocación manual."
argument-hint: descripción detallada de la funcionalidad a implementar — alcance, comportamiento esperado y complejidad (cuanto más detalle, mejor será el plan)
disable-model-invocation: true
model: opus
effort: max
---

# Planear implementación de una funcionalidad

Esta es la **fase de investigación y planificación** previa a la implementación. Su único producto son tres documentos `.md` que dejan la funcionalidad completamente planeada para implementarla después. **En esta skill no se escribe ni se modifica código de la aplicación.**

**Modo de razonamiento — ultrathink.** Opera con el presupuesto máximo de extended thinking durante toda la skill.

## La funcionalidad a planificar

$ARGUMENTS

## Resultado esperado

Tres documentos `.md` dentro del directorio `nueva-implementacion-en-curso/`, en la raíz del proyecto (créalo si no existe):

| Archivo | Contenido |
|---|---|
| `implementation-<feature-slug>-backend.md` | Todo lo que afecte al directorio `backend/`. |
| `implementation-<feature-slug>-frontend.md` | Todo lo que afecte al directorio `frontend/`. |
| `implementation-<feature-slug>-general-description.md` | Descripción funcional de la feature a nivel de usuario: qué cambia en la aplicación para quien la usa. Nada técnico ni de código. |

Reglas de nombrado y formato:

- `<feature-slug>` es un nombre corto y descriptivo de la funcionalidad en `kebab-case` ASCII: sin tildes, sin comillas, sin espacios. Usa el **mismo** slug en los tres archivos.
- Genera **siempre los tres** documentos. Si la feature no toca una capa, su documento lo indica de forma explícita ("Sin cambios en backend/frontend") en lugar de omitirse.
- Redacta los tres documentos **en español**.
- El directorio es un artefacto de trabajo temporal y **no se versiona**: comprueba que `nueva-implementacion-en-curso/` figura en el `.gitignore` del proyecto y añádelo si falta.
- El documento `implementation-<feature-slug>-frontend.md` debe incluir una **sección dedicada y claramente delimitada** que enumere los **endpoints del backend que el frontend consumirá**: URL, método HTTP y la forma de request/response tal como se planean. Es el contrato API desde la perspectiva del frontend.

Estos tres documentos son el input de la fase de implementación posterior. La calidad de la implementación posterior depende de forma directa de lo completos y correctos que sean, así que deben cubrir el máximo de detalle y de casos extremos posible.

## Regla dura — esta skill solo planifica

Hasta producir y revisar los tres `.md`, **no edites código de la aplicación, ni tests, ni ningún otro `.md` del repositorio**. El trabajo aquí es investigar, decidir y documentar. La implementación real ocurre después, en una fase aparte que se basará en estos documentos y en su propio análisis del código para refinar lo que el plan haya podido pasar por alto.

## Flujo

### 1. Investigación (en paralelo)

Arranca a la vez las dos vertientes de investigación:

**a) Código afectado.** Investiga a fondo cada parte del código que la feature vaya a tocar: capas implicadas, convenciones aplicables y contratos existentes. Elige el método que consideres más eficiente —normalmente varios subagentes de exploración que se reparten el repositorio— con el objetivo de tener en contexto toda zona afectada antes de planificar.

**b) APIs externas (solo si la feature las requiere).** Si la implementación depende de una API externa, no te conformes con una sola investigación por API: un subagente que arranca con el contexto en blanco puede equivocarse u omitir detalles críticos. Por eso cada API necesaria se investiga en **tres rondas secuenciales** de subagentes `external-api-docs-researcher` —crear, revisar, enriquecer—, de modo que cada ronda cace lo que la anterior dejó fuera y el `.md` de la API quede lo más completo y fiable posible.

**Paso previo obligatorio.** **Lee primero** el cuerpo del agente en `C:\Users\amuel\.claude\agents\external-api-docs-researcher.md`. Es **obligatorio y fundamental**: de ahí sacas (i) el formato de task prompt que espera, (ii) que el propio agente ya hace una doble pasada interna y persiste el resultado en `externalAPIinformation/{tema}-{api}.md`, y (iii) que cuando ese archivo ya existe lo **refresca y amplía conservando lo previo** —el comportamiento exacto en el que se apoyan las rondas 2 y 3—. Sin leerlo no podrás lanzar el número correcto de agentes ni redactar los task prompts de cada ronda.

Determina los pares **(API, tema concreto)**: lanzarás **un subagente por par en cada ronda**. Dentro de una misma ronda los subagentes corren **en paralelo** —cada uno escribe en el `.md` de su propia API, así que no se pisan—; las **tres rondas son estrictamente secuenciales** entre sí, porque cada una parte del `.md` que dejó la anterior. **Espera a que toda una ronda termine antes de lanzar la siguiente.**

1. **Ronda 1 — creación.** Lanza, en paralelo, un `external-api-docs-researcher` por cada par, con el task prompt en el formato que el agente espera (API objetivo + tema concreto al que ceñirse). Cada uno crea el `.md` de su API. **Anota la ruta del `.md` que cada subagente devuelve al terminar**: la necesitarás para apuntar a ella en las rondas siguientes.
2. **Ronda 2 — revisión y mejora.** Terminada por completo la ronda 1, relanza un subagente por par sobre la **misma** API y el **mismo** tema, pero con el task prompt reorientado: su misión principal **ya no es crear, sino revisar el `.md` existente**. Indícale **de forma literal la ruta absoluta de ese `.md`** (la que devolvió la ronda 1) y encárgale reinvestigar la API para **detectar lo importante que la primera pasada omitió**, corregir imprecisiones y rellenar huecos, manteniendo el archivo centrado en lo relevante para la feature.
3. **Ronda 3 — revisión final y enriquecimiento.** Terminada por completo la ronda 2, relanza una última vez un subagente por par sobre la misma API y tema. Pásale **la ruta absoluta del `.md` ya mejorado** y encárgale una pasada final: reinvestigar, releer el documento y cazar cualquier información crítica, relevante o sutil que aún falte, hasta dejarlo lo más completo, preciso y útil posible para implementar la feature.

El producto de las tres rondas es, por cada API, un único `.md` de investigación de la máxima calidad que entra como contexto en la fase de resolución de dudas.

### 2. Resolución de dudas y decisiones (conmigo)

Con toda la información en contexto (código + descripción de la feature + APIs externas), **pregúntame con `AskUserQuestion` todo lo que necesites resolver**:

- Cualquier duda sobre el comportamiento de la feature que no haya dejado claro en el argumento.
- Cualquier decisión de diseño que sea un trade-off (elegir A mejora algo pero empeora otra cosa).
- Cualquier elección de implementación con más de una opción razonable.

Cuando puedas recomendar una opción, márcala como **recomendada** en la propia pregunta. No dejes de preguntar hasta estar al 100% seguro de todas las decisiones necesarias para planificar la implementación.

### 3. Redacción de los tres documentos

Con las dudas resueltas, redacta los tres `.md` siguiendo el reparto y las reglas de la sección **Resultado esperado**. Deben explicar a la perfección lo necesario para implementar la feature después.

### 4. Revisión final con subagentes (dos iteraciones secuenciales)

Refuerza la calidad de los documentos con **dos iteraciones secuenciales**. Cada iteración lanza **dos subagentes en paralelo** —uno para backend y otro para frontend— que tú creas y rediges en el momento (no son agentes preexistentes). Como cada subagente arranca con el **contexto en blanco**, en su task prompt **menciona de forma literal las rutas absolutas de los documentos que debe revisar**: al subagente de backend, las del documento de backend y el de `general-description`; al de frontend, las del documento de frontend y el de `general-description`. Sin esas rutas explícitas no puede localizar los archivos:

- Cada subagente lee **solo los dos documentos que le corresponden** —el de su capa (backend o frontend) y el de `general-description`— y **corrige, completa y cura ese `.md` de capa** hasta dejarlo lo más limpio, completo, preciso y útil posible para implementar. Como mínimo debe: contrastar el plan contra el **código real** de la aplicación y verificar que lo propuesto encaja con él y con el análisis de archivos ya existente; detectar lo que la planificación inicial **dejó fuera** (casos extremos, archivos olvidados, pasos omitidos); corregir **imprecisiones** (contratos, nombres, rutas, tipos); y asegurar la **coherencia con la `general-description`**, de modo que el plan de capa cumpla de verdad lo que la feature promete a nivel de usuario. El de `general-description` es contexto de **solo lectura** (no lo edita: dos subagentes en paralelo tocándolo se pisarían).
- Completa la primera iteración (los dos subagentes en paralelo) y solo entonces lanza la segunda (otros dos en paralelo) sobre los documentos ya corregidos: una segunda pasada de calidad que caza las omisiones e imprecisiones que la primera aún haya dejado y vuelve a actualizar cada `.md` de capa.

Al cerrar la segunda iteración, la planificación queda lista para la fase de implementación.
