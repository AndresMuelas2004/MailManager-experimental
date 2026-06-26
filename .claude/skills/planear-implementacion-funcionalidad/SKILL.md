---
name: planear-implementacion-funcionalidad
description: "Fase de investigación y planificación previa a la implementación: analiza primero el código afectado, decide con ese conocimiento si hay que investigar APIs externas y sobre qué temas, resuelve contigo todas las dudas y decisiones de diseño, y genera los tres documentos .md (backend, frontend y descripción general) que dejan la funcionalidad lista para implementar. El número de iteraciones de la investigación de APIs y de la creación de los documentos es configurable desde el argumento. Claude NUNCA debe lanzar esta skill por decisión propia: se ejecuta solo cuando el usuario, otra skill o algún otro recurso la invoca de forma explícita."
argument-hint: descripción detallada de la funcionalidad a planificar (alcance, comportamiento esperado, complejidad); opcionalmente precedida por una primera frase que fije cuántas iteraciones quieres — 1 a 3 para la investigación de APIs y 1 a 3 para la creación de los documentos (sin esa frase se usan 3 y 3)
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

1. **Frase de iteraciones + descripción.** La **primera frase** fija cuántas iteraciones quiero para los dos procesos iterativos de la skill (p. ej. *«Quiero que realices solo una iteración sobre investigar las APIs y tres sobre la creación de los md»*) y todo lo que sigue es la descripción de la funcionalidad.
2. **Solo la descripción.** No hay frase de iteraciones: aplican los valores por defecto.

Los dos contadores y su semántica — **el número siempre cuenta la pasada inicial**, no solo los repasos:

| Contador | Proceso | Rango | Significado | Default |
|---|---|---|---|---|
| **N_api** | Investigación de APIs externas (paso 2) | 1–3 | Rondas totales de `external-api-docs-researcher` por cada par (API, tema), **contando la ronda de creación**. `1` = solo la ronda de creación; `3` = creación + 2 rondas de revisión. | 3 |
| **N_md** | Creación de los documentos (pasos 5 y 6) | 1–3 | Pasadas totales sobre los tres `.md` de la feature, **contando la redacción inicial**. `1` = solo la redacción (el paso 6 se omite); `3` = redacción + 2 iteraciones de revisión. | 3 |

Reglas de interpretación:

- Nunca recibirás `0`: equivaldría a no crear nada.
- Si la frase solo fija uno de los dos contadores, el otro usa su default.
- Si la feature no requiere APIs externas, `N_api` simplemente no aplica (aunque la frase lo mencione).
- Si la frase de iteraciones resulta ambigua o trae un valor fuera de rango, pregúntame con `AskUserQuestion` antes de arrancar.

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
- **Actualización de una funcionalidad existente.** Si lo que se pide no es una implementación nueva sino la revisión/actualización de una funcionalidad ya existente —porque lo diga el argumento o porque lo deduzca tu análisis del código—, los **tres** documentos deben dejarlo bien claro con un aviso destacado al inicio (p. ej. *«⚠️ Actualización de la funcionalidad existente \<nombre\> — no es una implementación nueva»*). La fase de implementación posterior cuenta con esa marca: su paso de documentación espera que el `…-general-description.md` le aclare si la feature es nueva o una modificación de una anterior. Si es una implementación nueva no añadas nada — es el caso por defecto y no necesita declararse.

Estos tres documentos son el input de la fase de implementación posterior. La calidad de la implementación posterior depende de forma directa de lo completos y correctos que sean, así que deben cubrir el máximo de detalle y de casos extremos posible.

## Regla dura — esta skill solo planifica

Hasta producir y revisar los tres `.md`, **no edites código de la aplicación, ni tests, ni ningún otro `.md` del repositorio**. Quedan fuera de esta prohibición los artefactos que el propio flujo genera: los `.md` de investigación de APIs en `externalAPIinformation/` que escriben los subagentes del paso 2, y las entradas en `.gitignore` necesarias para `externalAPIinformation/` y `nueva-implementacion-en-curso/`. El trabajo aquí es investigar, decidir y documentar. La implementación real ocurre después, en una fase aparte que se basará en estos documentos y en su propio análisis del código para refinar lo que el plan haya podido pasar por alto.

## Flujo

### 1. Análisis inicial del código afectado

Antes de decidir nada sobre APIs externas, **lee directamente el código** que la descripción sugiere que la feature va a tocar: capas implicadas, contratos existentes y —sobre todo— si la implementación llega hasta los clientes de proveedores externos (Gmail, Outlook, etc.). El objetivo de esta primera pasada **no** es el análisis exhaustivo (eso llega en el paso 3), sino reunir en **tu** contexto el criterio suficiente para responder dos preguntas: ¿la feature depende de APIs externas? y, si sí, ¿qué pares (API, tema concreto) hay que investigar y con qué task prompt? Puedes apoyarte en subagentes de exploración, pero el conocimiento que sustenta esa decisión debe acabar en tu contexto, no solo en el de un subagente.

### 2. Decisión e investigación de APIs externas (solo si la feature las requiere)

Con el análisis inicial hecho, decide si la implementación depende de APIs externas. Si **no**, salta directamente al paso 3 y olvídate de esta vertiente. Si **sí**, determina los pares **(API, tema concreto)** y arranca la investigación:

**Paso previo obligatorio.** **Lee primero** el cuerpo del agente en `C:\Users\MiniPC\.claude\agents\external-api-docs-researcher.md`. Es **obligatorio y fundamental**: de ahí sacas (i) el formato de task prompt que espera, (ii) que el propio agente ya hace una doble pasada interna y persiste el resultado en `externalAPIinformation/{tema}-{api}.md`, y (iii) que cuando ese archivo ya existe lo **refresca y amplía conservando lo previo** —el comportamiento exacto en el que se apoyan las rondas de revisión—. Sin leerlo no podrás lanzar el número correcto de agentes ni redactar los task prompts de cada ronda.

**Task prompts informados por el código.** Gracias al paso 1 puedes afinar el tema concreto de cada par con lo que el código te ha enseñado: qué operaciones hace ya nuestra integración, qué endpoint o método concreto necesita la feature, qué comportamiento hay que confirmar en la documentación oficial. Un tema afinado por el código produce una investigación mucho más útil que el enunciado genérico de la feature.

El proceso son **N_api rondas secuenciales** por par (API, tema). La razón de que el default sea 3: un subagente que arranca con el contexto en blanco puede equivocarse u omitir detalles críticos, y cada ronda caza lo que la anterior dejó fuera, así que reduce las rondas solo cuando la frase de iteraciones lo pida. Lanzarás **un subagente por par en cada ronda**. Dentro de una misma ronda los subagentes corren **en paralelo** —cada uno escribe en el `.md` de su propio par (API, tema), así que no se pisan—; las rondas son **estrictamente secuenciales** entre sí, porque cada una parte del `.md` que dejó la anterior. **Espera a que toda una ronda termine antes de lanzar la siguiente.**

1. **Ronda 1 — creación.** Lanza, en paralelo, un `external-api-docs-researcher` por cada par, con el task prompt en el formato que el agente espera (API objetivo + tema concreto afinado por el código). Cada uno crea el `.md` de su par (API, tema). **Anota la ruta del `.md` que cada subagente devuelve al terminar**: si hay más rondas, la necesitarás para apuntar a ella.
2. **Rondas 2 y 3 — revisión y enriquecimiento** (la 2 solo si `N_api ≥ 2`; la 3 solo si `N_api = 3`). Terminada por completo la ronda anterior, relanza un subagente por par sobre la **misma** API y el **mismo** tema, pero con el task prompt reorientado: su misión principal **ya no es crear, sino revisar el `.md` existente**. Indícale **de forma literal la ruta absoluta de ese `.md`** y encárgale reinvestigar la API para **detectar lo importante que las rondas anteriores omitieron**, corregir imprecisiones y rellenar huecos, manteniendo el archivo centrado en lo relevante para la feature. La última ronda que ejecutes es la pasada final: su encargo es cazar cualquier información crítica, relevante o sutil que aún falte, hasta dejar el documento lo más completo, preciso y útil posible para implementar la feature.

**Solapamiento con el paso 3.** El agente `external-api-docs-researcher` corre en segundo plano: tras lanzar una ronda, continúa con el análisis profundo del paso 3 mientras esperas, y vuelve a esta vertiente solo para lanzar la ronda siguiente cuando la anterior haya terminado por completo. La única condición dura es que la vertiente entera (todas las rondas de todos los pares) esté cerrada antes de entrar al paso 4.

El producto de las rondas es, por cada par (API, tema), un único `.md` de investigación de la máxima calidad que entra como contexto en la fase de resolución de dudas.

### 3. Análisis profundo del resto del código

Completa la investigación de cada parte del código que la feature vaya a tocar: capas implicadas, convenciones aplicables y contratos existentes. Elige el método que consideres más eficiente —normalmente varios subagentes de exploración que se reparten el repositorio— con el objetivo de tener en contexto todas las zonas afectadas antes de planificar.

Si este análisis profundo revela que la feature necesita un par (API, tema) que el paso 2 no detectó, lánzalo en cuanto lo descubras con sus propias `N_api` rondas, con la misma mecánica del paso 2.

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
Ten muy en cuenta lo siguiente: cuando termine este proceso de planificación, se llevará a cabo un proceso de implementación **independiente** que se basará **solo y exclusivamente en los tres documentos que aquí se generan**. Ese proceso **nunca leerá** los `.md` de investigación de APIs externas producidos en el paso 2 del flujo. Por eso, todo el conocimiento sobre cómo funcionan y cómo se usan esas APIs externas debe quedar **volcado y bien explicado** en `implementation-<feature-slug>-backend.md`: es la única fuente sobre las APIs que tendrá la fase de implementación. Sé muy realista y detallado al describir cómo usar dichas APIs para la feature; ahí tiene que estar toda la información necesaria, sin que falte nada.

### 6. Revisión final con subagentes (N_md − 1 iteraciones secuenciales)

Si `N_md = 1`, omite este paso: la planificación queda cerrada con la redacción del paso 5. En otro caso, refuerza la calidad de los documentos con **N_md − 1 iteraciones secuenciales** (con el default, dos).

Cada iteración lanza **dos subagentes en orden secuencial** —primero backend, después frontend— que tú creas y diriges en el momento (no son agentes preexistentes). Como cada subagente arranca con el **contexto en blanco**, en su task prompt **menciona de forma literal las rutas absolutas de los documentos que debe leer**; sin esas rutas explícitas no puede localizar los archivos:

1. **Primero el subagente de BACKEND.** Lee el documento de backend y el de `general-description`, y **corrige, completa y cura el `.md` de backend**. Espera a que termine antes de lanzar el siguiente.
2. **Después el subagente de FRONTEND.** Lee el documento de frontend, el de `general-description` **y además el de backend como contexto de SOLO lectura**, para contrastar la **sección de endpoints** del documento de frontend contra el contrato que el revisor de backend acaba de dejar revisado. **Corrige, completa y cura únicamente el `.md` de frontend.**

El orden backend → frontend dentro de cada iteración existe para que el contrato de endpoints nunca se desincronice entre capas: el revisor de frontend valida siempre contra la versión final del documento de backend de esa iteración, nunca contra una versión a medio editar.

Como mínimo, cada subagente debe: contrastar el plan contra el **código real** de la aplicación y verificar que lo propuesto encaja con él y con el análisis de archivos ya existente; detectar lo que la planificación **dejó fuera** (casos extremos, archivos olvidados, pasos omitidos); corregir **imprecisiones** (contratos, nombres, rutas, tipos); y asegurar la **coherencia con la `general-description`**, de modo que el plan de capa cumpla de verdad lo que la feature promete a nivel de usuario. El de `general-description` es contexto de **solo lectura** para ambos: es la referencia estable de lo que la feature promete al usuario y ningún revisor de capa debe redefinirla.

Completa una iteración entera (backend y después frontend) antes de lanzar la siguiente: cada iteración adicional es una pasada de calidad que caza las omisiones e imprecisiones que la anterior aún haya dejado.

Al cerrar la última iteración, la planificación queda lista para la fase de implementación.
