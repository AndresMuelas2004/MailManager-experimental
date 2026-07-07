# /implementar-feature-completa — README de diseño

**Fecha de creación: 2026-07-07.** Este documento registra qué se construyó, qué se modificó, por qué, y cada decisión tomada (con su alternativa descartada) durante el diseño del pipeline. El diagrama del flujo completo está en [`flow-diagram.svg`](./flow-diagram.svg).

## Qué es

Skill orquestadora que automatiza el flujo manual que hasta ahora se ejecutaba skill a skill para implementar una feature:

```
/planear-implementacion-funcionalidad → /implementar-funcionalidad → /reviewDiffsBeforeCommitAll → /validar-mejoras → (aplicar mejoras a mano) → (commit a mano)
```

Con una sola invocación — `/implementar-feature-completa "descripción [+ frase de iteraciones]"` — se ejecutan las seis fases en cadena, cada una en **contexto aislado** (el contexto de la orquestadora nunca se llena con el trabajo de las fases: ~500k de planificación, ~150k de implementación, ~200k de review y ~200k de validación quedan fuera de la sesión principal), y **todos los artefactos quedan persistidos** en `nueva-implementacion-en-curso/<slug>/`.

## Restricciones técnicas que condicionaron el diseño (doc oficial, verificada 2026-07-07)

1. **`AskUserQuestion` está vetada en subagentes sin excepción** (aunque se liste en `tools`). Consecuencia directa: una skill forkeada no puede preguntar al usuario → nació el **relay de preguntas** de la fase 1.
2. **`context: fork` + `agent:`** en el frontmatter de una skill la ejecuta en un subagente aislado: la SKILL.md se convierte en el task prompt; el agente aporta system prompt, tools, model y hooks. El fork carga los `CLAUDE.md` del proyecto.
3. **Anidamiento de subagentes: máximo 5 niveles** (v2.1.172). El pipeline usa 2–3 (orquestadora → fase → subagentes internos de la fase).
4. **La herramienta `Skill` SÍ está disponible dentro de subagentes** → `reviewDiffsBeforeCommitAll` forkeada puede seguir cargando sus dos skills hijas, y el runner puede ejecutar skills por task prompt.
5. **Subagentes custom/general-purpose son reanudables**: devuelven `agentId` y aceptan `SendMessage` conservando su contexto — la pieza que hace posible el relay multi-ronda. (`Explore`/`Plan` no son reanudables.)
6. **Prioridad de skills en colisión de nombre: personales (`~/.claude/skills/`) > proyecto (`.claude/skills/`)** → no se podía crear una `validar-mejoras` de proyecto homónima (quedaría muerta).
7. **`disable-model-invocation: true` bloquea la invocación vía herramienta Skill** (solo el usuario tecleándola) → hubo que retirarlo de `commit-push-estructurados`.
8. **El stacking de slash commands (`/a /b /c`) no sirve para encadenar skills forkeadas** (la expansión se corta en el primer fork) → la orquestación vive en el cuerpo de esta skill.
9. **Hook `SubagentStop` con `decision: block`** obliga al subagente a reemitir su respuesta final (anti-bucle vía `stop_hook_active`) — patrón ya probado en esta máquina por `consulta-doc-claude-code`, del que este pipeline es un calco ampliado.

## Decisiones tomadas (2026-07-07)

| # | Decisión | Elección | Por qué / alternativa descartada |
|---|---|---|---|
| 1 | Preguntas de la planificación | **Relay vía orquestadora**: planear corre en un `pipeline-skill-runner` reanudable; devuelve bloques `PREGUNTAS-PENDIENTES`, la orquestadora los presenta con `AskUserQuestion` reales y reenvía `RESPUESTAS` vía `SendMessage` (rondas ilimitadas) | Descartado correr planear inline en la orquestadora: habría metido ~500k tokens en la sesión principal. `AskUserQuestion` es imposible dentro del subagente (restricción 1) |
| 2 | Modo de salida de review/validar/aplicar | **Persistir el informe completo en `.md` y responder una única línea, SIEMPRE** (también invocadas sueltas) | Descartado el modo dual por argumento: dos ramas que mantener; además el informe en disco no se pierde al cerrar la sesión |
| 3 | Adaptación de `validar-mejoras` | **Nueva skill de proyecto con otro nombre**: `validar-mejoras-implementacion`, con `user-invocable: false` (no aparece en el menú `/`) y descripción «Claude NUNCA debe lanzarla por decisión propia» | La personal `~/.claude/skills/validar-mejoras` queda **intacta** para el resto de usos/proyectos. Homónima de proyecto imposible (restricción 6). No lleva `disable-model-invocation` porque entonces nadie podría invocarla (ni la orquestadora) |
| 4 | Carpeta de artefactos | **`nueva-implementacion-en-curso/<slug>/`**, gitignored, permanece al terminar | Descartados: archivarla a un histórico al completar, y versionarla en git |
| 5 | Checkpoints entre fases | **Sin paradas**: tras la última ronda de preguntas del plan, todo corre solo hasta el final (solo detiene un bloqueo) | Descartado el checkpoint post-plan: el usuario prefirió automatización máxima; las preguntas del relay ya le dan el control de las decisiones |
| 6 | Alcance de la aplicadora | **Aplicar + coherencia mínima**: NECESARIAS siempre; CONDICIONALES evaluando condiciones contra el código y tomando la opción senior-recomendada; ajusta tests/docs **directamente** rotos por cada mejora | Descartados: no tocar nunca tests/docs (deja el repo en rojo) y re-verificación final con otra review (+100-200k tokens) |
| 7 | Cierre del pipeline | **Fase 6 = `/commit-push-estructurados`** (commits Conventional agrupados por responsabilidad + push), ejecutada vía runner | El usuario la pidió explícitamente como cierre. Para hacerla invocable hubo que retirarle `disable-model-invocation: true` (restricción 7), compensado con frase disuasoria en su description |
| 8 | Reanudación | **`continuar <slug> desde <fase>`**: comprueba los artefactos prerequisito en la carpeta y salta lo ya hecho | Descartado el pipeline lineal puro: si review casca tras horas de planear+implementar, relanzar entero es carísimo |
| 9 | Agentes runner | **1 runner común** (`pipeline-skill-runner`, opus + effort max, hereda todas las tools, hook `SubagentStop` genérico de formato) para las 6 fases | Descartados: 2 runners lector/escritor (el "lector" necesitaba Write igualmente para persistir su informe, perdía el sentido) y 5 por fase (más archivos que mantener). Las reglas read-only de review/validar siguen siendo instrucciones de cada skill, como siempre fueron |
| 10 | Modelo de las fases | **Opus + effort max pineados en el runner** (con `context: fork` es el agente quien lo garantiza; el `model` del frontmatter de la skill no es fiable en fork) | Descartados: heredar de la sesión (riesgo de correr el pipeline con un modelo menor sin querer) y pinear Fable 5. Excepción consciente: la fase 6 ejecuta `commit-push-estructurados` inline dentro del runner y ahí el `model: sonnet` del frontmatter de esa skill sí aplica el resto del turno — aceptado, el trabajo de git no necesita opus |
| 11 | Visibilidad de las skills | **`aplicar-mejoras-validadas` visible en el menú** (no hay equivalente personal); **`implementar-funcionalidad` pasa a `user-invocable: false`** (a petición del usuario: solo debe verse la orquestadora); `validar-mejoras-implementacion` oculta (la personal sigue visible) | — |
| 12 | Nombres | **`implementar-feature-completa`** (orquestadora) y **`aplicar-mejoras-validadas`** (aplicadora) | Descartados: pipeline-funcionalidad/aplicar-mejoras y ciclo-feature/ejecutar-mejoras |
| 13 | Decisiones condicionales | Archivo propio **`decisiones-condicionales.md`** (pregunta que se te habría hecho + opciones + elegida + razón). **Sustituida por la decisión 19** (2026-07-07): en pipeline las decisiones van a `informe-final.md`; el archivo propio queda solo para el uso suelto | Más localizable que una sección dentro de `mejoras-aplicadas.md` (convención, sin pregunta) |
| 14 | Investigación de APIs | **`externalAPIinformation/` se queda donde está** (fuera de la carpeta de la feature) | El researcher refresca y reutiliza esos `.md` entre features; moverlos rompería la reutilización (convención, sin pregunta) |
| 15 | Review sin hallazgos | `validables: 0` → **se saltan las fases 4 y 5** con nota en el resumen; `necesarias+condicionales = 0` → se salta la 5 | Convención, sin pregunta |
| 16 | Gate previo al commit (añadido 2026-07-07, revisión post-diseño) | La fase 5 reporta **`necesarias-sin-aplicar: <k>`** (NECESARIAS acabadas DESCARTADAS por decisión no deducible o FALLIDAS revertidas; las CONDICIONALES no aplicadas no cuentan) y la orquestadora **detiene el pipeline antes de la fase 6** si `k > 0`, recordando `continuar <slug> desde commit`. Matiza la decisión 5: el gate es la segunda parada legítima además del bloqueo | Sin el gate, un problema confirmado por la validación pero no aplicable sin decisión del usuario se commiteaba y **pusheaba en silencio** (solo se descubría leyendo `mejoras-aplicadas.md` tras el push). Descartado el gate solo-FALLIDA: dejaba fuera la vía más probable (DESCARTADA por decisión de producto/contrato no deducible) |
| 17 | Hook de formato v2 (post-auditoría 2026-07-07) | **Validación determinista de TODOS los formatos + fail-open**: el hook deja pasar cualquier mensaje que cumpla el protocolo (líneas `OK \| …`/`FALLO:`/`BLOQUEO:` y también los bloques `PREGUNTAS-PENDIENTES`/`PLAN-COMPLETADO`, con chequeo estructural) y bloquea solo lo no conforme; si `last_assistant_message` no llega en el stdin (cambio de esquema del harness — el campo no está en la doc actual), deja pasar SIN validar | Descartado el híbrido v1: bloqueaba SIEMPRE los bloques multilínea legítimos de la fase 1 (una reemisión de coste por cada ronda de preguntas) y, sin `stop_hook_active`, un cambio de esquema habría producido un **bucle infinito de bloqueo**. Fail-open: perder la validación de formato es aceptable; colgar el pipeline no |
| 18 | Gate de reviewers caídos (post-auditoría 2026-07-07) | La línea de la fase 3 gana **`reviewers-caidos: <m>`** y la orquestadora **para antes de la fase 4** si `m > 0`, ofreciendo `desde review` (relanzar el review completo) o `desde validar` (aceptar la cobertura parcial ya persistida) | Sin el gate, "todos los reviewers crasheados" degradaba a `REVIEW BEFORE COMMIT` con `validables: 0` → se saltaban las fases 4-5 y se **commiteaba y pusheaba sin revisar**; el crash solo constaba en un `.md` que nadie lee antes del push |
| 19 | Informe final consolidado (post-auditoría 2026-07-07) | Nuevo artefacto **`informe-final.md`**: la orquestadora lo crea (FASE 1) y le añade interrupciones + resumen ejecutivo; las fases 3 y 4 añaden sus subagentes lanzados/caídos (nombre, objetivo, estado) vía `--informe`; la fase 5 añade las decisiones condicionales y las NECESARIAS sin aplicar — ahí y ya NO en `decisiones-condicionales.md`, que queda solo para el uso suelto sin `--informe`. Todo por APPEND en UTF-8; nadie del pipeline lo lee (la fase 5 solo hace un Grep anti-duplicado en reanudación) | El usuario revisa UN único documento con lo que le importa: subagentes caídos, errores que interrumpieron el flujo con su causa, y decisiones tomadas en su nombre (pregunta + opciones + elegida + razón). Descartado repartir esa información entre 3-4 `.md` técnicos |
| 20 | Registro incremental reanudable de la fase 5 (post-auditoría 2026-07-07) | `mejoras-aplicadas.md` se escribe **a medida**: entrada `EN CURSO` justo antes de tocar código, actualización al estado final justo después; al reanudar, lo finalizado NO se reprocesa, lo `EN CURSO` se completa, lo ausente se procesa, y los recuentos de la línea final son los ACUMULADOS | Cierra el hueco de la reanudación `desde aplicar`: sin registro, una mejora ya aplicada se re-evaluaba como "falso positivo sobrevenido" → DESCARTADA → `k > 0` → gate espurio antes del commit |

## Piezas creadas (2026-07-07)

| Pieza | Ruta | Rol |
|---|---|---|
| Skill orquestadora | `.claude/skills/implementar-feature-completa/SKILL.md` | Corre inline; encadena las 6 fases; relay de preguntas; reanudación |
| Skill aplicadora | `.claude/skills/aplicar-mejoras-validadas/SKILL.md` | Fase 5. `context: fork`. Visible en menú |
| Skill validadora interna | `.claude/skills/validar-mejoras-implementacion/SKILL.md` | Fase 4. `context: fork`. Oculta (`user-invocable: false`) |
| Agente runner | `.claude/agents/pipeline-skill-runner.md` | Ejecuta/aloja todas las fases aisladas; opus + max; hook de formato |
| Hook de formato | `.claude/hooks/validate-pipeline-runner-output.py` | `SubagentStop` determinista v2 (2026-07-07): valida `last_assistant_message` contra TODOS los formatos del protocolo — líneas `OK \| …`/`FALLO:`/`BLOQUEO:` y bloques `PREGUNTAS-PENDIENTES`/`PLAN-COMPLETADO` (chequeo estructural) — y bloquea solo lo no conforme; **fail-open** si el campo no llega (jamás bloquear a ciegas) y anti-bucle con `stop_hook_active` (decisión 17) |

## Piezas modificadas (2026-07-07)

| Pieza | Cambios |
|---|---|
| `.claude/skills/planear-implementacion-funcionalidad/SKILL.md` | (a) Los 3 `.md` pasan a escribirse en subcarpeta `nueva-implementacion-en-curso/<slug>/` (antes: directorio plano); (b) nueva sección **«Modo orquestado»** activada por la marca `[MODO-ORQUESTADO]` en `$ARGUMENTS`: sustituye `AskUserQuestion` por el protocolo `PREGUNTAS-PENDIENTES`/`RESPUESTAS`/`PLAN-COMPLETADO`. **El uso manual directo no cambia en nada** |
| `.claude/skills/implementar-funcionalidad/SKILL.md` | Frontmatter: `user-invocable: false` + `context: fork` + `agent: pipeline-skill-runner`; los dos casos de «detente y pregúntame/dímelo» pasan a devolver `BLOQUEO: …` (en fork no se puede preguntar); el Cierre persiste el informe completo en `implementacion-resumen.md` (informes de los 4 agentes de cierre incluidos) y responde una única línea |
| `.claude/skills/reviewDiffsBeforeCommitAll/SKILL.md` | Frontmatter: `argument-hint` + `context: fork` + `agent: pipeline-skill-runner`; nuevo argumento `--out <ruta.md>`; el paso 5 escribe el informe consolidado completo al `.md` (default sin `--out`: `nueva-implementacion-en-curso/review-suelta-<timestamp>.md`) y responde una única línea con `overall` y `validables`; «sin diffs» responde `OK \| sin-diffs`. Las dos skills hijas: **cero cambios** |
| `~/.claude/skills/commit-push-estructurados/SKILL.md` (personal) | Solo: retirado `disable-model-invocation: true` + frase disuasoria añadida a la description. Comportamiento intacto |

Sin cambios en: `~/.claude/skills/validar-mejoras` (personal), las skills hijas de review, los 6 agentes de `/implementar-funcionalidad`, `.gitignore` (ya cubría la carpeta), permisos (`Skill` en allow global de usuario + `Agent(*)` de proyecto ya lo cubren).

## Contratos del protocolo (respuestas cortas por fase)

| Fase | Respuesta OK | Fallo |
|---|---|---|
| 1 Planear | `PREGUNTAS-PENDIENTES` (×N rondas) → `PLAN-COMPLETADO` + `slug:` + `dir:` | `FALLO: <motivo>` |
| 2 Implementar | `OK \| resumen: <ruta>` | `BLOQUEO: <motivo>` |
| 3 Review | `OK \| informe: <ruta> \| overall: <verdict> \| validables: <n> \| reviewers-caidos: <m>` (si `m > 0`, la orquestadora detiene el pipeline antes de la fase 4 — decisión 18) · `OK \| sin-diffs` | `FALLO: <motivo>` |
| 4 Validar | `OK \| validacion: <ruta> \| necesarias: <n> \| condicionales: <n> \| falsos-positivos: <n>` | `FALLO: <motivo>` |
| 5 Aplicar | `OK \| aplicadas: <n> \| descartadas: <m> \| necesarias-sin-aplicar: <k> \| detalle: <ruta> \| decisiones: <ruta>` — en pipeline `decisiones` apunta a `informe-final.md`; recuentos acumulados del registro (si `k > 0`, la orquestadora detiene el pipeline antes de la fase 6 — decisión 16) | `FALLO: <motivo>` |
| 6 Commit+push | `OK \| commits: <n> \| push: <ok\|fallo> \| detalle: <ruta>` | `FALLO: <motivo>` |

Artefactos en `nueva-implementacion-en-curso/<slug>/`: `implementation-<slug>-{backend,frontend,general-description}.md`, `implementacion-resumen.md`, `review-pre-commit.md`, `validacion-mejoras.md`, `mejoras-aplicadas.md` (registro incremental reanudable — decisión 20), `commits-realizados.md` e `informe-final.md` (consolidado para el usuario — decisión 19: subagentes lanzados/caídos de las fases 3-4, interrupciones con su causa, decisiones condicionales, NECESARIAS sin aplicar y resumen ejecutivo al final). `decisiones-condicionales.md` ya NO se genera en el pipeline: solo existe en el uso suelto de `/aplicar-mejoras-validadas` sin `--informe`.

## Riesgos conocidos — a validar en la primera ejecución real (con una feature trivial)

1. **Relay `SendMessage` multi-ronda** (fase 1): documentado oficialmente, nunca ejercitado en esta máquina. Es la pieza más nueva.
2. **Flota de reviewers en background dentro de un fork** (fase 3): el anidamiento está soportado (profundidad 2 de 5), a confirmar en la práctica.
3. **Campos `last_assistant_message` y `stop_hook_active` del hook**: NO figuran en la doc oficial actual de hooks (verificado 2026-07-07); funcionan empíricamente en esta máquina. El hook v2 es **fail-open**: si un update del CLI los retirara, se pierde la validación de formato pero jamás se bloquea (decisión 17). El riesgo antiguo de `${CLAUDE_PROJECT_DIR}` en frontmatter quedó retirado: la doc confirma la expansión.
4. El resto del patrón (fork síncrono, hook `SubagentStop` con block+anti-bucle, respuesta corta) ya está probado en esta máquina vía `consulta-doc-claude-code`.

## Revisión post-auditoría (2026-07-07)

Auditoría de la skill con verificación contra la doc oficial → cambios aplicados el mismo día (decisiones 17-20):

| Pieza | Cambio |
|---|---|
| Hook `validate-pipeline-runner-output.py` | v2 determinista + fail-open (decisión 17), verificado con batería de 20 casos (formatos válidos, no conformes, fail-open, anti-bucle) |
| Orquestadora | Constante `INFORME` (`informe-final.md`, creado al cerrar la FASE 1), gate `reviewers-caidos > 0` tras la fase 3 (decisión 18), persistencia de TODA interrupción y del resumen ejecutivo en el informe, cierre sin `decisiones-condicionales.md` |
| `reviewDiffsBeforeCommitAll` | Nuevo arg `--informe`; roster de agentes lanzados y sección «subagentes lanzados» (nombre/lado/objetivo/estado); línea con `reviewers-caidos: <m>` |
| `validar-mejoras-implementacion` | Nuevo arg `--informe`; registro de lanzamientos y Paso 4.bis con la sección «subagentes de validación» |
| `aplicar-mejoras-validadas` | Registro incremental reanudable `EN CURSO → estado final` con Paso 0.5 de reanudación (decisión 20); nuevo arg `--informe`: decisiones condicionales y NECESARIAS sin aplicar van al informe final (sin `--informe` — uso suelto — sigue creando `decisiones-condicionales.md`) |

Confirmaciones de doc obtenidas en la auditoría: `${CLAUDE_PROJECT_DIR}` se expande en hooks de frontmatter (riesgo original retirado); la tabla `user-invocable`/`disable-model-invocation`, la semántica fork+agent (incl. «un fork no puede engendrar otro fork» — las skills hijas de review NO están forkeadas, correcto), la reanudación por `SendMessage` y el veto de `AskUserQuestion` en subagentes funcionan como asumía el diseño. NO documentados hoy: `last_assistant_message` y `stop_hook_active` (→ fail-open del hook v2).
