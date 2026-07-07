---
name: implementar-feature-completa
description: "Pipeline completo de implementación de una feature: planifica (haciéndote todas las preguntas de diseño), implementa, revisa los diffs, valida las mejoras propuestas, aplica las validadas y cierra con commits estructurados + push — cada fase en contexto aislado y todos los artefactos persistidos en nueva-implementacion-en-curso/<slug>/. Claude NUNCA debe lanzar esta skill por decisión propia: se ejecuta solo cuando el usuario la invoca de forma explícita."
argument-hint: "descripción detallada de la feature a implementar, opcionalmente precedida por la frase de iteraciones de la planificación (igual que /planear-implementacion-funcionalidad); o 'continuar <slug> desde <fase>' para reanudar un pipeline interrumpido (fases: implementar, review, validar, aplicar, commit)"
model: opus
effort: max
---

# Implementar una feature completa — orquestador del pipeline

Encadenas de principio a fin las seis fases de implementación de una feature. **Corres inline en la sesión principal** — eres la única pieza del pipeline con acceso a `AskUserQuestion` — y tu único trabajo es **orquestar**: lanzar cada fase en su contexto aislado, esperar su resultado corto, relevar al usuario las preguntas de la planificación y decidir la transición a la fase siguiente. Sin checkpoints intermedios: tras la última ronda de preguntas del plan, el pipeline corre solo hasta el final (solo te detienes ante un bloqueo o ante uno de los dos gates de seguridad: reviewers caídos en la FASE 3, o mejoras NECESARIAS que quedaron sin aplicar en la FASE 5).

## Reglas del orquestador (innegociables)

1. **Contexto mínimo.** Nunca leas el contenido de los artefactos `.md` de las fases (planes, informes, validaciones): tu sesión solo debe contener lanzamientos, respuestas cortas del protocolo, el relay de preguntas y tu resumen final. Para la reanudación compruebas la **existencia** de archivos (Glob / Test-Path), jamás su contenido. Única excepción de escritura: `INFORME` — lo creas y le **añades** secciones al final (append: PowerShell `Add-Content -Encoding utf8` o Bash `cat >>`), pero jamás lo lees ni lo reescribes.
2. **Estrictamente secuencial.** Lanza una fase, espera en silencio su resultado (sin ejecutar ninguna otra herramienta ni hacer trabajo paralelo mientras tanto) y solo entonces decide la siguiente. Nunca dos fases a la vez: todas operan sobre el mismo working tree.
3. **Parada ante bloqueo.** Si una fase devuelve `BLOQUEO: …` / `FALLO: …` — o un mensaje que no encaje en ningún formato del protocolo (trátalo como fallo) — detén el pipeline, reporta la fase, el motivo **literal**, y recuérdale al usuario el comando exacto de reanudación: `/implementar-feature-completa continuar <slug> desde <fase>`. Además, si `DIR` ya existe, **persiste la interrupción en `INFORME`** (créalo antes con la cabecera de la FASE 1 si faltara): añade al final una sección `## Interrupción — FASE <n> (<nombre>)` con la fecha, el motivo literal y el comando de reanudación. Esto aplica a TODA parada: bloqueos, fallos y los dos gates de seguridad.
4. **No repitas trabajo.** Una fase que respondió `OK` no se relanza jamás dentro de la misma ejecución.

## Constantes

- `DIR_BASE` = `nueva-implementacion-en-curso/` en la raíz del proyecto (gitignored).
- `DIR` = `DIR_BASE/<slug>/` — la carpeta de la feature, creada por la fase 1 (o preexistente en reanudación). Usa siempre rutas absolutas al pasarla a las fases.
- `INFORME` = `DIR/informe-final.md` — el informe consolidado para el usuario: subagentes lanzados/caídos (fases 3 y 4), interrupciones con su causa, decisiones condicionales y mejoras NECESARIAS sin aplicar (fase 5), y resumen ejecutivo. Lo creas tú al cerrar la FASE 1; las fases 3, 4 y 5 le añaden sus secciones al recibir su ruta vía `--informe`; tú solo añades al final y jamás lo lees (regla 1).

## Interpretación del argumento

- **Modo normal**: `$ARGUMENTS` es la descripción de la feature, opcionalmente precedida por la frase de iteraciones que entiende `/planear-implementacion-funcionalidad`. Se arranca en la fase 1.
- **Modo reanudación**: `$ARGUMENTS` tiene la forma `continuar <slug> desde <fase>` con `<fase>` ∈ `implementar | review | validar | aplicar | commit`. Comprueba que `DIR_BASE/<slug>/` existe (si no, detente y lista los slugs disponibles) y que están presentes los artefactos prerequisito de esa fase (tabla de abajo); arranca directamente en ella. Si `INFORME` no existe en la carpeta, créalo con la cabecera de la FASE 1 antes de lanzar la fase. Reanudar «desde planear» no existe: eso es simplemente relanzar el pipeline en modo normal.

| Reanudar desde | Debe existir en `DIR` |
|---|---|
| `implementar` | los tres `implementation-<slug>-{backend,frontend,general-description}.md` |
| `review` | lo anterior + `implementacion-resumen.md` |
| `validar` | `review-pre-commit.md` |
| `aplicar` | `validacion-mejoras.md` — si además existe `mejoras-aplicadas.md`, la fase retoma sola lo ya hecho sin repetirlo |
| `commit` | nada adicional (solo cambios sin commitear en el working tree) |

## FASE 1 — Planificación (subagente reanudable con relay de preguntas)

Lanza **un** subagente con la herramienta `Agent`, `subagent_type: pipeline-skill-runner`, y este task prompt (sustituye el placeholder por el argumento original completo, iteraciones incluidas):

```
Ejecuta con la herramienta Skill la skill `planear-implementacion-funcionalidad` pasando como args EXACTAMENTE:
[MODO-ORQUESTADO] <argumento original completo del usuario>
Sigue sus instrucciones al pie de la letra. Su sección «Modo orquestado» define tu protocolo de comunicación: tus únicos mensajes finales válidos son el bloque PREGUNTAS-PENDIENTES (cuando necesites respuestas del usuario; recibirás un bloque RESPUESTAS como mensaje de continuación), el bloque PLAN-COMPLETADO (slug + dir) al terminar del todo, o `FALLO: <motivo>` ante bloqueo irresoluble.
```

**Guarda el `agentId`** que devuelve la herramienta Agent: lo necesitas para reanudarlo. Después entra en el **bucle de relay**:

1. Espera el resultado del subagente.
2. **Si es `PREGUNTAS-PENDIENTES`**: preséntame cada pregunta con `AskUserQuestion` — máximo 4 preguntas por llamada (trocea en varias llamadas si hay más); por pregunta: incorpora el `contexto` al enunciado, `header` corto (≤12 caracteres), las opciones del bloque como opciones (la marcada `[RECOMENDADA]` va primera con el sufijo «(Recomendado)»); el usuario siempre puede responder texto libre vía «Other». Con todas las respuestas, reanuda el subagente con `SendMessage` (destino: el `agentId` guardado) enviando el bloque:

   ```
   RESPUESTAS
   ---
   id: <n>
   respuesta: <texto literal de la opción elegida, o el texto libre del usuario tal cual>
   ---
   ...
   ```

   Vuelve al punto 1. El bucle admite tantas rondas como el planificador necesite.
3. **Si es `PLAN-COMPLETADO`**: extrae `slug` y `dir` → fija `DIR`, crea `INFORME` con la herramienta Write y exactamente esta cabecera, y continúa con la FASE 2. No leas los tres `.md`.

   ```markdown
   # Informe final del pipeline — <slug>

   > Documento consolidado para revisión rápida: subagentes lanzados/caídos, interrupciones
   > y su causa, y decisiones condicionales tomadas en nombre del usuario. Secciones en orden
   > cronológico; el resumen ejecutivo se añade AL FINAL al cerrar el pipeline.
   ```
4. **Si es `FALLO: …`** (o cualquier otra cosa): parada ante bloqueo (regla 3).

## FASE 2 — Implementación

Invoca con la herramienta `Skill` la skill `implementar-funcionalidad` con args = la ruta absoluta de `DIR` (contiene exactamente un trío de documentos). Corre forkeada por su propio frontmatter; espera su línea:

- `OK | resumen: <ruta>` → FASE 3.
- `BLOQUEO: …` → parada ante bloqueo.

## FASE 3 — Review de diffs

Invoca con `Skill` la skill `reviewDiffsBeforeCommitAll` con args: `--out <DIR>/review-pre-commit.md --informe <INFORME>`. Espera su línea:

- `OK | informe: <ruta> | overall: <verdict> | validables: <n> | reviewers-caidos: <m>`:
  - Si `m > 0` → **gate de seguridad: detén el pipeline SIN ejecutar la fase 4.** La cobertura del review quedó incompleta (`m` reviewers crashearon; cuáles eran y qué revisaban está en la sección de la fase 3 de `INFORME` — no lo leas tú). Persiste la interrupción en `INFORME` (regla 3) y recuérdame los dos cierres posibles: `/implementar-feature-completa continuar <slug> desde review` (relanza el review completo) o `… desde validar` (acepto la cobertura parcial del informe ya persistido).
  - Si `m = 0` y `validables: 0` → **salta las fases 4 y 5** (anótalo para el resumen final) y ve a la FASE 6.
  - Si `m = 0` y `validables > 0` → FASE 4.
- `OK | sin-diffs` → algo fue mal (la implementación no dejó cambios en el working tree): trátalo como bloqueo y repórtalo.
- `FALLO: …` → parada ante bloqueo.

## FASE 4 — Validación de mejoras

Invoca con `Skill` la skill `validar-mejoras-implementacion` con args: `<DIR>/review-pre-commit.md --out <DIR>/validacion-mejoras.md --informe <INFORME>`. Espera su línea:

- `OK | validacion: <ruta> | necesarias: <a> | condicionales: <b> | falsos-positivos: <c>`:
  - Si `a + b = 0` → **salta la fase 5** (anótalo) y ve a la FASE 6.
  - Si `a + b > 0` → FASE 5.
- `FALLO: …` → parada ante bloqueo.

## FASE 5 — Aplicación de mejoras validadas

Invoca con `Skill` la skill `aplicar-mejoras-validadas` con args: `<DIR>/validacion-mejoras.md --dir <DIR> --informe <INFORME>`. Espera su línea:

- `OK | aplicadas: <n> | descartadas: <m> | necesarias-sin-aplicar: <k> | detalle: <ruta> | decisiones: <ruta>` (en el pipeline, `decisiones` apunta a `INFORME`):
  - Si `k = 0` → FASE 6.
  - Si `k > 0` → **gate de seguridad: detén el pipeline SIN ejecutar la fase 6.** No es un fallo de la fase: `k` mejoras NECESARIAS quedaron sin aplicar (DESCARTADAS por decisión no deducible o FALLIDAS revertidas) y commitear/pushearlas taparía un problema confirmado. Persiste la interrupción en `INFORME` (regla 3), reporta el recuento, indica que los motivos exactos por hallazgo están en la sección «Mejoras NECESARIAS sin aplicar» de `INFORME` (no lo leas tú — regla 1), y recuérdame el comando para cerrar el pipeline cuando lo haya resuelto: `/implementar-feature-completa continuar <slug> desde commit`.
- `FALLO: …` → parada ante bloqueo.

## FASE 6 — Commits estructurados + push

Lanza un subagente con `Agent`, `subagent_type: pipeline-skill-runner`, con este task prompt (sustituye `<DIR>`):

```
Ejecuta con la herramienta Skill la skill `commit-push-estructurados` (sin argumentos) y sigue sus instrucciones al pie de la letra: es totalmente autónoma — decide el plan de commits, los ejecuta en orden y hace push sin preguntar nada.
Al terminar, escribe con la herramienta Write su resumen final completo (el plan de commits con títulos y propósito de cada uno, el resultado del push, y los archivos dejados fuera con su motivo) en `<DIR>/commits-realizados.md`.
Tu mensaje final debe ser EXACTAMENTE una línea: `OK | commits: <n> | push: <ok|fallo> | detalle: <ruta absoluta de commits-realizados.md>` — o `FALLO: <motivo>` si algo impidió completar.
```

- `OK | …` → Cierre.
- `FALLO: …` → parada ante bloqueo (los cambios quedan sin commitear o parcialmente commiteados; el detalle estará en `commits-realizados.md` si llegó a escribirse).

## Cierre — resumen final al usuario

1. **Añade al final de `INFORME`** (append — regla 1, sin leerlo) una sección `## Resumen ejecutivo` con la fecha y la misma tabla de abajo.
2. Presenta en pantalla un resumen **breve** (esto sí se imprime — es el informe del pipeline):

```
## Pipeline completado — <slug>
| Fase | Estado | Artefacto |
|---|---|---|
| 1 Planificación | OK (N rondas de preguntas) | implementation-<slug>-*.md |
| 2 Implementación | OK | implementacion-resumen.md |
| 3 Review | OK — overall: <verdict> | review-pre-commit.md |
| 4 Validación | OK / omitida (sin hallazgos) | validacion-mejoras.md |
| 5 Mejoras | OK (a aplicadas, m descartadas) / omitida | mejoras-aplicadas.md |
| 6 Commit+push | OK (n commits, push <estado>) | commits-realizados.md |

Informe final (subagentes caídos · interrupciones · decisiones): <ruta absoluta de INFORME>
Carpeta de la feature: <ruta absoluta de DIR>
```

Si la fase 5 corrió, recuérdame en una línea que las decisiones condicionales tomadas en mi nombre están en el informe final. Nada más: sin recomendaciones, sin próximos pasos.
