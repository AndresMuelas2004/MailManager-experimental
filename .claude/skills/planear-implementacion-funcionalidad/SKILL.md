---
name: planear-implementacion-funcionalidad
description: "Fase de investigación y planificación previa a la implementación: analiza primero el código afectado, decide con ese conocimiento si necesita consultar la documentación local de las APIs de Gmail/Outlook (external-apis-used/) y sobre qué temas, resuelve contigo todas las dudas y decisiones de diseño, y genera los tres documentos .md (backend, frontend y descripción general) que dejan la funcionalidad lista para implementar. El número de iteraciones de la creación de los documentos es configurable desde el argumento. Claude NUNCA debe lanzar esta skill por decisión propia: se ejecuta solo cuando el usuario, otra skill o algún otro recurso la invoca de forma explícita."
argument-hint: descripción detallada de la funcionalidad a planificar (alcance, comportamiento esperado, complejidad); opcionalmente precedida por una primera frase que fije cuántas iteraciones quieres para la creación de los documentos — 1 a 3 (sin esa frase se usan 3)
model: opus
effort: max
---

# Planear implementación de una funcionalidad

Esta es la **fase de investigación y planificación** previa a la implementación. Sus únicos productos son tres documentos `.md` que dejan la funcionalidad completamente planeada para implementarla después. **En esta skill no se escribe ni se modifica código de la aplicación.**

**Modo de razonamiento — ultrathink.** Opera con el presupuesto máximo de extended thinking durante toda la skill.

## La funcionalidad a planificar

$ARGUMENTS

## Interpretación del argumento

El argumento llega en una de estas dos formas:

1. **Frase de iteraciones + descripción.** La **primera frase** fija cuántas iteraciones quiero para la creación de los documentos (p. ej. *«Quiero que realices tres iteraciones sobre la creación de los md»*) y todo lo que sigue es la descripción de la funcionalidad.
2. **Solo la descripción.** No hay frase de iteraciones: aplica el valor por defecto.

El único contador y su semántica — **el número siempre cuenta la pasada inicial**, no solo los repasos:

| Contador | Proceso | Rango | Significado | Default |
|---|---|---|---|---|
| **N_md** | Creación de los documentos (pasos 5 y 6) | 1–3 | Pasadas totales sobre los tres `.md` de la feature, **contando la redacción inicial**. `1` = solo la redacción (el paso 6 se omite); `3` = redacción + 2 iteraciones de revisión. | 3 |

Reglas de interpretación:

- **`N_md` cuenta solo pasadas de redacción — no acota nada más.** Frases como *«una iteración de la (primera) skill de planificación»*, *«solo una iteración de planear»* o *«haz N iteraciones»* fijan **exclusivamente** `N_md` (cuántas veces se redactan/repasan los tres `.md`), y nada más. En modo orquestado (`/implementar-feature-completa`) **no** significan «ejecuta solo esta primera skill y detén el pipeline»: el número de iteraciones y el alcance del pipeline —que tras planificar continúa por sí solo a implementación, review, etc.— son cosas independientes. El pipeline solo se detiene tras la planificación si el usuario lo pide de forma explícita («para tras planificar» / «solo planifica»), nunca por el mero hecho de fijar las iteraciones.
- Nunca recibirás `0`: equivaldría a no crear nada.
- Si la frase de iteraciones resulta ambigua o trae un valor fuera de rango, pregúntame con `AskUserQuestion` antes de arrancar.

## Modo orquestado (pipeline /implementar-feature-completa)

Si `$ARGUMENTS` empieza por la marca literal `[MODO-ORQUESTADO]`, estás corriendo como subagente dentro del pipeline `/implementar-feature-completa`. Retira la marca y trata el resto como el argumento normal (frase de iteraciones opcional + descripción). Todo el flujo de la skill aplica igual, con UNA única diferencia — el canal de preguntas:

- Como subagente **no tienes `AskUserQuestion`** (está vetada en subagentes). **Toda** pregunta que esta skill te pida hacerme (la ambigüedad de iteraciones de arriba, el paso 4 completo, cualquier confirmación puntual) se canaliza así: termina tu turno emitiendo como mensaje final **únicamente** el bloque `PREGUNTAS-PENDIENTES` de abajo. El orquestador me presentará esas preguntas y te reenviará mis respuestas como mensaje de continuación con el bloque `RESPUESTAS`. Tu contexto sigue intacto entre rondas: continúa exactamente donde lo dejaste. Repite tantas rondas como necesites — la exigencia del paso 4 («no dejes de preguntar hasta estar al 100 % seguro») sigue vigente.
- Cada pregunta lleva de **2 a 4 opciones cerradas** (el usuario siempre podrá además responder con texto libre) y marca `[RECOMENDADA]` la que recomiendes:

```
PREGUNTAS-PENDIENTES
---
id: 1
pregunta: <una frase interrogativa completa y autocontenida>
contexto: <1-2 líneas: por qué surge y qué implica cada camino>
opciones:
- <opción A> [RECOMENDADA]
- <opción B>
---
id: 2
...
```

- El bloque `RESPUESTAS` que recibirás tiene esta forma (una respuesta por id; puede ser el texto literal de una opción o texto libre del usuario):

```
RESPUESTAS
---
id: 1
respuesta: <texto>
---
...
```

- Al cerrar la última iteración del paso 6 (o el paso 5 si `N_md = 1`), tu mensaje final debe ser **únicamente**:

```
PLAN-COMPLETADO
slug: <feature-slug>
dir: <ruta absoluta de nueva-implementacion-en-curso/<feature-slug>>
```

- Ante un bloqueo irresoluble que ni siquiera pueda formularse como pregunta, responde una sola línea: `FALLO: <motivo>`.
- Fuera de esos tres mensajes (`PREGUNTAS-PENDIENTES`, `PLAN-COMPLETADO`, `FALLO: …`) no devuelvas nada más al orquestador: ni resúmenes, ni avances parciales — todo el producto vive en los tres `.md`.

Sin la marca `[MODO-ORQUESTADO]` esta sección no aplica en absoluto: pregunta con `AskUserQuestion` como define el resto de la skill.

## Resultado esperado

Tres documentos `.md` dentro del directorio `nueva-implementacion-en-curso/<feature-slug>/`, en la raíz del proyecto — una subcarpeta por feature, nombrada con el mismo slug de los archivos (crea la ruta completa si no existe):

| Archivo | Contenido |
|---|---|
| `implementation-<feature-slug>-backend.md` | Todo lo que afecte al directorio `backend/`. |
| `implementation-<feature-slug>-frontend.md` | Todo lo que afecte al directorio `frontend/`. |
| `implementation-<feature-slug>-general-description.md` | Descripción funcional de la feature a nivel de usuario: qué cambia en la aplicación para quien la usa. Nada técnico ni de código. |

Reglas de nombrado y formato:

- `<feature-slug>` es un nombre corto y descriptivo de la funcionalidad en `kebab-case` ASCII: sin tildes, sin comillas, sin espacios. Usa el **mismo** slug en los tres archivos y en la subcarpeta que los contiene.
- Genera **siempre los tres** documentos. Si la feature no toca una capa, su documento lo indica de forma explícita ("Sin cambios en backend/frontend") en lugar de omitirse.
- Redacta los tres documentos **en español**.
- El directorio es un artefacto de trabajo temporal y **no se versiona**: comprueba que `nueva-implementacion-en-curso/` figura en el `.gitignore` del proyecto y añádelo si falta.
- El documento `implementation-<feature-slug>-frontend.md` debe incluir una **sección dedicada y claramente delimitada** que enumere los **endpoints del backend que el frontend consumirá**: URL, método HTTP y la forma de request/response tal como se planean. Es el contrato API desde la perspectiva del frontend.
- **Actualización de una funcionalidad existente.** Si lo que se pide no es una implementación nueva sino la revisión/actualización de una funcionalidad ya existente —porque lo diga el argumento o porque lo deduzca tu análisis del código—, los **tres** documentos deben dejarlo bien claro con un aviso destacado al inicio (p. ej. *«⚠️ Actualización de la funcionalidad existente \<nombre\> — no es una implementación nueva»*). La fase de implementación posterior cuenta con esa marca: su paso de documentación espera que el `…-general-description.md` le aclare si la feature es nueva o una modificación de una anterior. Si es una implementación nueva no añadas nada — es el caso por defecto y no necesita declararse.

Estos tres documentos son el input de la fase de implementación posterior. La calidad de la implementación posterior depende de forma directa de lo completos y correctos que sean, así que deben cubrir el máximo de detalle y de casos extremos posible.

## Regla dura — esta skill solo planifica

Hasta producir y revisar los tres `.md`, **no edites código de la aplicación, ni tests, ni ningún otro `.md` del repositorio**. Quedan fuera de esta prohibición los artefactos que el propio flujo genera: los tres `.md` de la feature en `nueva-implementacion-en-curso/` y la entrada en `.gitignore` necesaria para ese directorio. El paso 2 solo **lee** la documentación de `external-apis-used/` (no escribe nada allí). El trabajo aquí es investigar, decidir y documentar. La implementación real ocurre después, en una fase aparte que se basará en estos documentos y en su propio análisis del código para refinar lo que el plan haya podido pasar por alto.

## Flujo

### 1. Análisis inicial del código afectado

Antes de decidir nada sobre APIs externas, **lee directamente el código** que la descripción sugiere que la feature va a tocar: capas implicadas, contratos existentes y —sobre todo— si la implementación llega hasta los clientes de proveedores externos (Gmail, Outlook, etc.). El objetivo de esta primera pasada **no** es el análisis exhaustivo (eso llega en el paso 3), sino reunir en **tu** contexto el criterio suficiente para responder dos preguntas: ¿la feature depende de APIs externas? y, si sí, ¿qué pares (API, tema concreto) hay que investigar y con qué task prompt? Puedes apoyarte en subagentes de exploración, pero el conocimiento que sustenta esa decisión debe acabar en tu contexto, no solo en el de un subagente.

### 2. Consulta de la documentación local de las APIs externas (solo si la feature las requiere)

Con el análisis inicial hecho, decide si la implementación depende de APIs externas. En este proyecto las únicas APIs externas son **Gmail** y **Microsoft Graph (Outlook)**, y su documentación ya está investigada y volcada en el directorio `external-apis-used/` de la raíz del proyecto (`external-apis-used/Gmail/` y `external-apis-used/Outlook/`). Si la feature **no** depende de esas APIs, salta directamente al paso 3 y olvídate de esta vertiente.

Si **sí** depende de ellas, **no lances ningún subagente ni hagas iteraciones**: la información ya existe. Simplemente entra en `external-apis-used/` y **lee lo que necesites** del cliente que corresponda (Gmail y/o Outlook), nada más allá de eso:

1. **Enruta con el índice.** Cada carpeta tiene un `CLAUDE.md` de clasificación (`external-apis-used/Gmail/CLAUDE.md`, `external-apis-used/Outlook/CLAUDE.md`) que lista sus temarios con una línea «Ir aquí si…». Léelo primero para saltar directo al `.md` de tema que te interese, sin leerte la carpeta entera.
2. **Lee informado por el código.** Gracias al paso 1 sabes qué operaciones hace ya nuestra integración y qué endpoint o método concreto necesita la feature; usa ese criterio para abrir solo los temarios relevantes (p. ej. adjuntos, borradores, delta/history, OAuth, cuotas/límites) y quedarte en tu contexto lo que la implementación vaya a necesitar.

Léelo con la profundidad que la feature exija. El conocimiento de estas APIs debe acabar en **tu** contexto, porque tendrás que volcarlo íntegro en `implementation-<feature-slug>-backend.md` (ver paso 5).

### 3. Análisis profundo del resto del código

Completa la investigación de cada parte del código que la feature vaya a tocar: capas implicadas, convenciones aplicables y contratos existentes. Elige el método que consideres más eficiente —normalmente varios subagentes de exploración que se reparten el repositorio— con el objetivo de tener en contexto todas las zonas afectadas antes de planificar.

Si este análisis profundo revela que la feature necesita un tema de una API externa que el paso 2 no detectó, léelo de `external-apis-used/` en cuanto lo descubras, con la misma mecánica del paso 2.

### 4. Resolución de dudas y decisiones (conmigo)

Con toda la información en contexto (código + descripción de la feature + APIs externas), **pregúntame con `AskUserQuestion` todo lo que necesites resolver**:

- Cualquier duda sobre el comportamiento de la feature que no haya dejado claro en el argumento.
- Cualquier decisión de diseño que sea un trade-off (elegir A mejora algo pero empeora otra cosa).
- Cualquier elección de implementación con más de una opción razonable.
- Si has **deducido** del código que la feature es la actualización de una funcionalidad existente (no te lo dije yo en el argumento) y el caso es fronterizo —p. ej. una extensión que añade capacidades nuevas sobre una feature existente—, confírmalo aquí antes de marcar los documentos. Si la deducción es inequívoca, márcala sin gastar pregunta.

**Cuándo preguntar.** En la medida de lo posible, pregunta de forma **consolidada en este paso**, cuando ya lo hayas analizado todo y tengas claro exactamente qué preguntar y por qué. Pregunta "sobre la marcha" (en cualquier punto del flujo) solo cuando sea necesario — p. ej. una ambigüedad que bloquea la propia investigación.

Cuando puedas recomendar una opción, márcala como **recomendada** en la propia pregunta. No dejes de preguntar hasta estar al 100% seguro de todas las decisiones necesarias para planificar la implementación.

### 5. Redacción de los tres documentos

Con las dudas resueltas, redacta los tres `.md` siguiendo el reparto y las reglas de la sección **Resultado esperado** (incluida la marca de actualización de funcionalidad existente cuando aplique). Esta redacción es la **primera de las N_md pasadas** sobre los documentos. Deben explicar a la perfección lo necesario para implementar la feature después.
Ten muy en cuenta lo siguiente: cuando termine este proceso de planificación, se llevará a cabo un proceso de implementación **independiente** que se basará **solo y exclusivamente en los tres documentos que aquí se generan**. Ese proceso **nunca leerá** la documentación de `external-apis-used/` que consultaste en el paso 2. Por eso, todo el conocimiento sobre cómo funcionan y cómo se usan esas APIs externas debe quedar **volcado y bien explicado** en `implementation-<feature-slug>-backend.md`: es la única fuente sobre las APIs que tendrá la fase de implementación. Sé muy realista y detallado al describir cómo usar dichas APIs para la feature; ahí tiene que estar toda la información necesaria, sin que falte nada.

### 6. Revisión final con subagentes (N_md − 1 iteraciones secuenciales)

Si `N_md = 1`, omite este paso: la planificación queda cerrada con la redacción del paso 5. En otro caso, refuerza la calidad de los documentos con **N_md − 1 iteraciones secuenciales** (con el default, dos).

Cada iteración lanza **dos subagentes en orden secuencial** —primero backend, después frontend— que tú creas y diriges en el momento (no son agentes preexistentes). Como cada subagente arranca con el **contexto en blanco**, en su task prompt **menciona de forma literal las rutas absolutas de los documentos que debe leer**; sin esas rutas explícitas no puede localizar los archivos:

1. **Primero el subagente de BACKEND.** Lee el documento de backend y el de `general-description`, y **corrige, completa y cura el `.md` de backend**. Espera a que termine antes de lanzar el siguiente.
2. **Después el subagente de FRONTEND.** Lee el documento de frontend, el de `general-description` **y además el de backend como contexto de SOLO lectura**, para contrastar la **sección de endpoints** del documento de frontend contra el contrato que el revisor de backend acaba de dejar revisado. **Corrige, completa y cura únicamente el `.md` de frontend.**

El orden backend → frontend dentro de cada iteración existe para que el contrato de endpoints nunca se desincronice entre capas: el revisor de frontend valida siempre contra la versión final del documento de backend de esa iteración, nunca contra una versión a medio editar.

Como mínimo, cada subagente debe: contrastar el plan contra el **código real** de la aplicación y verificar que lo propuesto encaja con él y con el análisis de archivos ya existente; detectar lo que la planificación **dejó fuera** (casos extremos, archivos olvidados, pasos omitidos); corregir **imprecisiones** (contratos, nombres, rutas, tipos); y asegurar la **coherencia con la `general-description`**, de modo que el plan de capa cumpla de verdad lo que la feature promete a nivel de usuario. El de `general-description` es contexto de **solo lectura** para ambos: es la referencia estable de lo que la feature promete al usuario y ningún revisor de capa debe redefinirla.

Completa una iteración entera (backend y después frontend) antes de lanzar la siguiente: cada iteración adicional es una pasada de calidad que caza las omisiones e imprecisiones que la anterior aún haya dejado.

Al cerrar la última iteración, la planificación queda lista para la fase de implementación.
