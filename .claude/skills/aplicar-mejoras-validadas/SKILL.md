---
name: aplicar-mejoras-validadas
description: "Aplica al código las mejoras ya validadas de un informe de validación .md: todas las NECESARIO DE ARREGLAR y las CONDICIONAL (evaluando sus condiciones contra el código real y tomando la opción que un ingeniero senior recomendaría), con coherencia mínima sobre los tests y docs directamente afectados. Mantiene mejoras-aplicadas.md como registro incremental reanudable (EN CURSO → estado final; una reanudación no repite lo ya hecho), persiste las decisiones condicionales y las NECESARIAS sin aplicar en el informe final del pipeline (--informe) o en decisiones-condicionales.md en uso suelto, y responde una única línea. Claude NUNCA debe lanzar esta skill por decisión propia: se ejecuta solo cuando el usuario u otra skill (típicamente /implementar-feature-completa) la invocan de forma explícita."
argument-hint: ruta al .md de validación de mejoras (salida de /validar-mejoras-implementacion o de /validar-mejoras); opcionalmente '--dir <carpeta>' donde persistir los artefactos (por defecto, la carpeta del propio .md de validación) y '--informe <ruta.md>' con el informe final del pipeline donde persistir decisiones condicionales y NECESARIAS sin aplicar (sin él van a decisiones-condicionales.md)
context: fork
agent: pipeline-skill-runner
model: opus
effort: max
---

# Aplicar mejoras validadas

Última fase de trabajo sobre el código del pipeline `/implementar-feature-completa` (también invocable suelta sobre cualquier informe de validación). Corres en contexto aislado (`context: fork`): **no ves ninguna conversación previa**; tu única entrada es el `.md` de validación recibido como argumento (más, en reanudación, el registro `mejoras-aplicadas.md` de la pasada interrumpida), y tu salida de valor son los cambios en el código más los artefactos que persistes.

**Modo de razonamiento — ultrathink.** Opera con el presupuesto máximo de extended thinking: cada mejora aplicada sin criterio puede introducir una regresión justo después de que la review ya pasó.

## Paso 0 — Entrada

`$ARGUMENTS` contiene la ruta del `.md` de validación y, opcionalmente: `--dir <carpeta>` como destino de los artefactos (por defecto: la carpeta donde vive el propio `.md` de validación) y `--informe <ruta.md>` — el informe final del pipeline. Con `--informe`, las decisiones condicionales y las NECESARIAS sin aplicar se persisten AHÍ (Paso 4.2) y NO se crea `decisiones-condicionales.md`; sin él (uso suelto), se crea `decisiones-condicionales.md` como siempre. Si la ruta del `.md` de validación falta o el archivo no existe, responde `FALLO: <motivo>` y detente.

Lee el informe completo y extrae los hallazgos con veredicto **NECESARIO DE ARREGLAR** y **CONDICIONAL** (con sus condiciones). Ignora los **FALSO POSITIVO** (ya quedaron documentados en la validación). Si no hay nada que aplicar, persiste igualmente los artefactos del Paso 4 (vacíos salvo cabecera/línea explicativa) y responde la línea final con `aplicadas: 0`.

## Paso 0.5 — ¿Reanudación? El registro incremental manda

Si `mejoras-aplicadas.md` YA existe en la carpeta destino, esta ejecución es la reanudación de una pasada interrumpida. Léelo antes de tocar nada y clasifica cada hallazgo del informe de validación:

- Con entrada en el registro y estado final (`APLICADA` / `DESCARTADA` / `FALLIDA`) → **no lo reproceses**: su resultado vale y cuenta en los recuentos finales.
- Con entrada `EN CURSO` → quedó a medias: lee el código afectado, determina qué parte del cambio llegó a aplicarse, complétalo (o reviértelo y aplícalo entero) y actualiza su entrada al estado final.
- Sin entrada en el registro → pendiente: procésalo normal en el Paso 2.

Si el registro no existe, es una ejecución nueva: lo crearás al procesar la primera mejora (Paso 2).

## Paso 1 — Contexto rector del repo

Antes de tocar código, lee la raíz: `CLAUDE.md`, `repository_guide.md`, `common_mistakes.md` (cada entrada es regla dura). Antes de tocar una capa, lee SU `CLAUDE.md` y SU `*_guide.md`. La jerarquía documental manda: `CLAUDE.md` raíz > `CLAUDE.md` de capa > `*_guide.md` / `docs/` > código.

## Paso 2 — Aplicar cada mejora, en orden

**Registro incremental obligatorio — la base de la reanudación.** `mejoras-aplicadas.md` se escribe A MEDIDA que trabajas, nunca al final: (1) justo ANTES de empezar una mejora, añade su entrada con `**Estado**: EN CURSO` (id, título, severidad, veredicto de validación; si el archivo no existe aún, créalo con su cabecera — Paso 4.1); (2) justo DESPUÉS de terminarla, actualiza esa entrada al estado final con todos los campos del Paso 4.1. Nunca empieces a editar código de una mejora sin haber persistido antes su `EN CURSO`: si el proceso muere, ese registro es lo que permite reanudar sin repetir ni duplicar nada.

Procesa los hallazgos **secuencialmente** (todos operan sobre el mismo working tree):

- **NECESARIO DE ARREGLAR** → se aplica siempre. Lee primero el código real afectado: la propuesta del informe puede ser imprecisa en el *cómo*; tu obligación es corregir el **problema señalado** con criterio senior, no transcribir la propuesta literalmente. Si el arreglo correcto difiere del propuesto, aplícalo y explica la diferencia en el registro.
- **CONDICIONAL** → evalúa sus **condiciones contra el código y el repo reales**:
  - Si las condiciones se cumplen (o la condición es una elección entre opciones razonables), aplica la opción que un ingeniero senior recomendaría.
  - Si las condiciones NO se cumplen, **no la apliques**.
  - En **ambos** casos, registra el bloque de decisión completo (pregunta, opciones, elegida, razón) en la entrada del hallazgo del registro incremental; el Paso 4.2 lo vuelca al destino de decisiones (`--informe` o `decisiones-condicionales.md`) — estas son las decisiones que en un flujo interactivo se habrían preguntado al usuario.
  - Los hallazgos con la condición «validación automática fallida: evaluar contra el código antes de aplicar» los evalúas tú íntegramente contra el código, con el mismo escepticismo que un validador: si concluyes que es un falso positivo, se registra como DESCARTADA con esa razón.
- **Escalado imposible = descarte registrado.** Si aplicar una mejora exige una decisión de producto/diseño/contrato que no está en el código ni en las reglas ni en el informe, **no inventes**: márcala DESCARTADA con el motivo exacto en `mejoras-aplicadas.md`.

Reglas al editar:

- **Cambios quirúrgicos**: toca solo lo que la mejora pide. Nada de refactors vecinos, ni "aprovechar para", ni scope nuevo.
- **Coherencia mínima obligatoria**: si tu cambio rompe o desactualiza **directamente** un test existente o una afirmación concreta de un `*_guide.md` / `docs/`, ajústalos como parte de esa misma mejora — no dejes el repo en rojo ni la documentación mintiendo. No vayas más allá: nada de re-documentar la feature entera ni escribir suites nuevas.
- **Prohibiciones duras**: ningún `CLAUDE.md` (protegidos por hook; si uno debiera cambiar, regístralo en el artefacto final), nada bajo `backend/Scripts/`, ningún commit (el commit lo hace la fase siguiente del pipeline o el usuario).

## Paso 3 — Verificación ligera

- Backend: puedes ejecutar **puntualmente** los tests unitarios de los archivos que tocaste (`.\.venv\Scripts\python.exe -m pytest <ruta_o_nodo> -q`). **Nunca** suites completas ni integration/E2E (regla § 11 del `CLAUDE.md` raíz).
- Frontend: no ejecutes tests ni builds; verifica de forma estática y anota en el registro cualquier test que convenga revisar.

## Paso 4 — Artefactos persistidos

En la carpeta destino (`--dir` o la del `.md` de validación), en español:

1. **`mejoras-aplicadas.md`** — el registro incremental que has ido escribiendo durante el Paso 2:
   - Cabecera al crearlo: informe de validación fuente y fecha (sin recuentos — van al final).
   - Un bloque por hallazgo procesado: `id`, título, severidad, veredicto de validación; **Estado**: `EN CURSO` → luego `APLICADA` / `DESCARTADA` (condición no cumplida, falso positivo sobrevenido o decisión no deducible — con el motivo) / `FALLIDA` (intentada y revertida — con el motivo); archivos tocados y descripción concisa del cambio real (y de la diferencia con la propuesta, si la hubo); ajustes de coherencia mínima realizados (tests/docs) o pendientes de revisión manual; y, si el hallazgo es CONDICIONAL, su **bloque de decisión** (los cuatro campos del punto 2 — de ahí se reconstruyen las decisiones en una reanudación).
   - Al cerrar el último hallazgo: sección final `## Recuentos finales` con los totales ACUMULADOS de todo el registro (pasadas interrumpidas anteriores incluidas): aplicadas, descartadas, fallidas y **NECESARIAS sin aplicar** (hallazgos NECESARIO DE ARREGLAR con estado final DESCARTADA o FALLIDA).
2. **Decisiones condicionales y NECESARIAS sin aplicar** — el destino depende del modo:
   - **Con `--informe` (pipeline)**: AÑADE al final de ese archivo (append — PowerShell `Add-Content -Encoding utf8` / Bash `cat >>`; nunca lo reescribas) las secciones de abajo. Antes de añadir, comprueba con Grep si el informe ya contiene una sección `## Fase 5` de una pasada interrumpida anterior: si existe, titula las nuevas con el sufijo ` (reanudación)` y no dupliques bloques ya escritos.
     - `## Fase 5 — Decisiones condicionales`: SOLO los hallazgos CONDICIONAL (también los de pasadas anteriores aún no volcados, reconstruidos desde el registro), un bloque por hallazgo con el formato de una pregunta que se te habría hecho: **Pregunta** (la decisión formulada como pregunta al usuario) · **Opciones consideradas** (cada camino posible con su implicación, 2-4) · **Opción elegida** (cuál y si se aplicó o no) · **Razón** (por qué un ingeniero senior la recomendaría, anclada al código real, archivo:línea). Si no hubo ninguna CONDICIONAL, la sección con la única línea `Sin decisiones condicionales en esta ejecución.`
     - `## Fase 5 — Mejoras NECESARIAS sin aplicar` (solo si hay alguna): un bloque por hallazgo con `id`, título, estado final (`DESCARTADA`/`FALLIDA`) y el **motivo exacto** — es lo que el usuario leerá para decidir cómo desbloquear el commit tras el gate del pipeline.
   - **Sin `--informe` (uso suelto)**: escribe `decisiones-condicionales.md` en la carpeta destino con los mismos bloques de decisión de arriba (solo la sección de decisiones).

## Paso 5 — Responder una única línea

Tu respuesta es un dato para el invocador; el detalle vive en los `.md`. Responde EXACTAMENTE:

`OK | aplicadas: <n> | descartadas: <n> | necesarias-sin-aplicar: <n> | detalle: <ruta absoluta de mejoras-aplicadas.md> | decisiones: <ruta absoluta del archivo con las decisiones — el --informe si llegó, decisiones-condicionales.md si no>`

Todos los recuentos son los ACUMULADOS del registro completo (una reanudación cuenta también lo resuelto en pasadas anteriores). `necesarias-sin-aplicar` cuenta SOLO los hallazgos con veredicto **NECESARIO DE ARREGLAR** cuyo estado final fue `DESCARTADA` o `FALLIDA` (las CONDICIONALES no aplicadas no cuentan: no aplicarlas es un resultado legítimo de evaluar su condición). Es el dato con el que el orquestador decide si puede commitear — inclúyelo siempre, también cuando valga `0`.

Ante un error irrecuperable: `FALLO: <motivo>`. Nada más: sin resumen en pantalla, sin próximos pasos, sin recomendaciones.
