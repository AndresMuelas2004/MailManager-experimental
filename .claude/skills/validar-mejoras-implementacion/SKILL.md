---
name: validar-mejoras-implementacion
description: "Validación crítica de las mejoras propuestas por un informe de review persistido en un .md, mediante subagentes escépticos aislados (uno por hallazgo o por grupo). Variante interna del pipeline /implementar-feature-completa de la skill personal validar-mejoras: la fuente es SIEMPRE un archivo .md pasado como argumento, el informe de validación se persiste en otro .md autocontenido y la respuesta al invocador es una única línea. Claude NUNCA debe lanzar esta skill por decisión propia: se ejecuta solo cuando el usuario u otra skill (típicamente /implementar-feature-completa) la invocan de forma explícita."
argument-hint: ruta al .md con el informe de review cuyas mejoras hay que validar; opcionalmente '--out <ruta.md>' con el destino del informe de validación y '--informe <ruta.md>' con el informe final del pipeline al que añadir la sección de subagentes lanzados/caídos
user-invocable: false
allowed-tools: Agent, Read, Grep, Glob, Write, Bash
model: opus
effort: max
---

# Validar Mejoras (pipeline) — Validación crítica con subagentes aislados

Variante del validador de mejoras integrada en el pipeline `/implementar-feature-completa`. Corres en contexto aislado (subagente `pipeline-skill-runner` lanzado por el orquestador con la herramienta Agent): **no ves ninguna conversación previa**, así que la única fuente de hallazgos es el archivo `.md` recibido como argumento, y el único canal de salida de valor es el `.md` de validación que persistes. No analizas los hallazgos tú mismo en línea: cada hallazgo lo valida un **subagente independiente que corre en su propio contexto aislado**, de modo que cada juicio se emite en fresco contra el código real.

## Tu rol — orquestador de la validación

1. **Recoger** los hallazgos a validar desde el archivo fuente.
2. **Decidir** cómo repartir el trabajo (un subagente por hallazgo, o uno por grupo).
3. **Lanzar** subagentes aislados con prompts autocontenidos.
4. **Persistir** el informe agregado en el `.md` de salida y responder una única línea.

El análisis profundo y escéptico ocurre **dentro de los subagentes**, nunca en línea.

---

## Paso 1 — Recoger los hallazgos a validar

`$ARGUMENTS` contiene la ruta del `.md` fuente (el informe de review) y, opcionalmente, `--out <ruta.md>` y `--informe <ruta.md>` (el informe final del pipeline, destino de la sección de subagentes del Paso 4.bis). Si la ruta fuente falta o el archivo no existe en disco, responde `FALLO: <motivo>` y detente.

Lee el archivo fuente completo y recoge los hallazgos así:

- **Informe con estructura de `/reviewDiffsBeforeCommitAll`** (distingue *Group A — New Code* y *Group B — Pre-Existing*): recoge los hallazgos de **Group A** y los de los **informes de arquitectura** (backend y frontend) con severidad **BLOCKER / MAJOR / MINOR**. Excluye Group B entero (código pre-existente, informativo por definición) y las severidades Suggestion / nit.
- **Cualquier otro formato de informe**: recoge todo hallazgo etiquetado **MINOR / MAJOR / BLOCKER** (sin distinguir mayúsculas; reconoce formatos como `MINOR`, `[MAJOR]`, `Severity: Blocker`, `🔴 BLOCKER`).
- **Si no hay ningún hallazgo validable**: no lances ningún subagente. Persiste un informe mínimo que lo constate (misma resolución de destino del Paso 4) y responde `OK | validacion: <ruta> | necesarias: 0 | condicionales: 0 | falsos-positivos: 0`.

Después, lista para ti todos los hallazgos recogidos, numerados: `#`, severidad, título breve, ruta de archivo + línea/símbolo, y el arreglo o afirmación propuestos. **Fusiona duplicados exactos** (el mismo hallazgo señalado por varios reviewers) en uno, anotando las fuentes.

---

## Paso 2 — Elegir la estrategia de reparto (esta elección es tuya)

Hay **exactamente dos** modos. Elige el que **optimice la calidad de la revisión frente a los tokens gastados**:

- **Un subagente por hallazgo** — por defecto cuando los hallazgos son **pocos y/o independientes** (archivos/asuntos distintos). Máximo aislamiento y foco.
- **Un subagente por grupo** — cuando hay **muchos** hallazgos, o varios están **muy relacionados** (mismo archivo/función/símbolo, causa raíz compartida, o interdependientes). Agrúpalos y asigna un subagente por grupo.

Guía:
- Inclínate por **agrupar** cuando sin agrupar lanzarías un número grande de subagentes, o cuando los hallazgos estén acoplados.
- Mantén cada grupo **coherente** — agrupa solo hallazgos que de verdad comparten contexto; nunca mezcles hallazgos no relacionados solo para bajar el recuento.

---

## Paso 3 — Lanzar los subagentes aislados

Lanza con la herramienta **Agent**, `subagent_type: general-purpose`. Cuando las asignaciones sean independientes, **lánzalas en paralelo** (varias llamadas a Agent en un mismo mensaje).

**Modelo — siempre Opus.** Lanza **cada** subagente con `model: opus`, sin excepción, independientemente de la severidad o complejidad del hallazgo. Nunca uses otro modelo para validar.

**Crítico — el subagente no ve NADA de tu contexto.** Arranca aislado. El prompt de lanzamiento **debe ser totalmente autocontenido**. Construye cada prompt a partir de esta plantilla:

```
Eres un validador RIGUROSO y ESCÉPTICO de hallazgos de revisión de código. No eres
un sello de goma: un hallazgo debe ganarse su sitio. Verifica contra el código REAL
del working tree, no contra la afirmación. Por defecto, desconfía. NO modifiques
ningún archivo y NO implementes nada — esto es validación de solo lectura.

Contexto de trabajo: <nota del repo / working tree que el subagente necesita>

Valida el/los siguiente(s) hallazgo(s). Para CADA uno:

--- HALLAZGO <id> ---
Severidad: <MINOR|MAJOR|BLOCKER>
Título: <título breve>
Ubicación: <ruta de archivo : línea(s) / símbolo(s)>
Afirmación / cambio propuesto: <la propuesta exacta y su justificación declarada>
Código referenciado (si lo hay): <pega el fragmento corto, si es relevante>
---

Para cada hallazgo:
1. Abre y LEE el/los archivo(s) real(es) en la ubicación dada para confirmar qué
   hace de verdad el código.
2. Cuestiónalo honestamente:
   - ¿Problema real o FALSO POSITIVO? (malinterpreta el código, protege un caso
     imposible, ya está cubierto en otra parte, supuesto erróneo sobre el
     comportamiento.)
   - ¿El arreglo es necesario y proporcionado al beneficio?
   - ¿Efectos secundarios, roturas o conflicto con patrones existentes?
   - ¿Cosmético disfrazado de sustancial? ¿Problema real o hipotético?
   - Cuestiona tu propia primera reacción — si tu instinto es "sí, vale", busca
     por qué podría no serlo.
3. Emite un veredicto — exactamente uno de:
   - FALSO POSITIVO — no es un problema real / la afirmación es errónea / no
     requiere acción.
   - CONDICIONAL — válido solo bajo condiciones concretas (decláralas explícitamente).
   - NECESARIO DE ARREGLAR — un problema genuino que debe corregirse.

Devuelve UN bloque conciso por hallazgo (sin preámbulo), cada uno como:
  Hallazgo <id> — <VEREDICTO> — confianza: <ALTA|MEDIA|BAJA>
  Razón: <2-4 líneas ancladas al código real, citando archivo:línea>
  Condiciones: <solo si CONDICIONAL>
```

Rellena cada `<...>` con el hallazgo recogido en el Paso 1 —incluido cualquier fragmento corto de código— para que el subagente nunca necesite información que no pueda alcanzar. Da a cada llamada a Agent una `description` breve (3-5 palabras).

**Subagente fallido o sin respuesta**: relánzalo **una vez**. Si vuelve a fallar, registra su(s) hallazgo(s) con veredicto `CONDICIONAL` y la condición literal «validación automática fallida: evaluar contra el código antes de aplicar» — nunca descartes un hallazgo en silencio.

**Registro de lanzamientos**: lleva tu propia lista de cada subagente lanzado — identificador (`validador 1`, `validador 2`, …), hallazgos asignados y estado final (`OK` / `CAÍDO tras reintento`). La necesitas para el Paso 4.bis.

---

## Paso 4 — Persistir el informe de validación

Resuelve el destino: `--out` si se pasó; en su defecto `nueva-implementacion-en-curso/validacion-suelta-<YYYYMMDD-HHmmss>.md` en la raíz del repo (crea el directorio si no existe).

**El informe debe ser AUTOCONTENIDO**: la skill que aplicará las mejoras trabajará SOLO con este archivo, sin acceso al informe de review original ni a tu contexto. Escríbelo en español con esta estructura:

1. **Cabecera**: ruta del informe fuente, fecha, recuentos totales por veredicto.
2. **Tabla resumen**: `# | Hallazgo | Severidad | Veredicto | Confianza | Razón clave`.
3. **Un bloque por hallazgo** con TODO lo necesario para aplicarlo sin leer nada más:
   - `id`, severidad original, título, ubicación exacta (archivo:línea/símbolo).
   - **Descripción completa del problema y del arreglo propuesto** (condensada del informe fuente, sin perder detalle operativo).
   - Veredicto, confianza y razón del subagente validador.
   - Si `CONDICIONAL`: las **condiciones exactas** declaradas.
4. **Notas finales**: veredictos de baja confianza, subagentes que fallaron (y qué hallazgos quedaron marcados con la condición de fallo), y cualquier limitación del reparto elegido.

---

## Paso 4.bis — Sección de subagentes en el informe del pipeline

Solo si se pasó `--informe`: AÑADE al final de ese archivo — nunca lo leas ni lo reescribas; usa un append (PowerShell `Add-Content -Encoding utf8` / Bash `cat >>`) — esta sección en español:

```
## Fase 4 — Validación: subagentes lanzados

| Subagente | Hallazgos asignados | Estado |
|---|---|---|
| validador 1 | #1, #3 | OK |
| validador 2 | #2 | CAÍDO tras reintento — sus hallazgos quedan CONDICIONAL |
```

Una fila por subagente de tu registro del Paso 3. Si ninguno cayó, añade bajo la tabla la línea `Los <N> validadores completaron sin fallos.` Sin `--informe`, este paso no existe.

---

## Paso 5 — Responder una única línea

Tu respuesta es un dato para el invocador; el valor vive en el `.md`. Responde EXACTAMENTE:

`OK | validacion: <ruta absoluta del .md> | necesarias: <n> | condicionales: <n> | falsos-positivos: <n>`

(los hallazgos marcados con la condición de «validación automática fallida» cuentan como condicionales). Ante un error irrecuperable: `FALLO: <motivo>`. Nada más: sin tabla en pantalla, sin resumen, sin próximos pasos.

---

## Reglas

- El análisis profundo pertenece a los **subagentes aislados**, no a ti. Tú recoges, repartes, lanzas, persistes — nada más.
- Los subagentes son de **solo lectura**: nunca editan ni implementan.
- **Tus únicas escrituras permitidas son el `.md` de validación** (destino del Paso 4) **y el append de tu sección al `--informe`** (Paso 4.bis). Nunca toques ningún otro archivo.
- Cada hallazgo recibe **exactamente uno** de los tres veredictos (o la marca de fallo convertida en `CONDICIONAL`).
- **No** inventes mejoras nuevas tuyas — valida solo lo que el informe fuente ya propuso.
- Sé honesto. Ante la duda, inclínate por `FALSO POSITIVO` / mayor escrutinio antes que por un aprobado blando.
- Sin truncado silencioso: si limitas o agrupas, dilo en el informe y explica por qué.
