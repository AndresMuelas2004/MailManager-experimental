---
description: Ciclo autónomo que detecta, valida, corrige y testea bugs usando 4 subagentes (bug-detector, bug-validador, bug-correccion, bug-tester) coordinados desde la conversación principal. El bucle progresa solo por finalización de tareas (no por tiempo). Lanzar manualmente con /cycle-autofix-bugs. La app debe estar corriendo en localhost antes de invocar.
argument-hint: (sin argumentos)
disable-model-invocation: true
user-invocable: true
model: opus
effort: max
allowed-tools: Agent(bug-detector, bug-validador, bug-correccion, bug-tester), Read, Write, Edit, Glob, Grep, Bash, PowerShell
shell: powershell
hooks:
  Stop:
    - hooks:
        - type: command
          shell: powershell
          command: |
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
            $raw = [Console]::In.ReadToEnd()
            try { $data = $raw | ConvertFrom-Json } catch { exit 0 }
            if ($data.stop_hook_active -eq $true) { exit 0 }
            $reason = @'
            RECORDATORIO CRITICO DEL CICLO /cycle-autofix-bugs.

            Este ciclo esta disenado para ser INFINITO hasta una de estas 7 paradas LEGITIMAS:

            1. PARADA MANUAL: el usuario pidio explicitamente parar (mensaje "stop" / "para" / "termina" entre transiciones de fase, o un mensaje libre equivalente).
            2. NO_BUGS_FOUND: el bug-detector devolvio EXACTAMENTE esa cadena tras barrido honesto.
            3. TESTER_COMMIT_FAILED: el bug-tester reporto fallo de commit no recuperable (hooks pre-commit bloqueando, conflictos, working tree dirty insalvable).
            4. WORKING TREE DIRTY INESPERADO al iniciar nueva iteracion (FASE 1 detecto cambios sin commit no atribuibles al ciclo).
            5. MCP PLAYWRIGHT INALCANZABLE: detector o tester lo reportaron en su payload.
            6. CONTRATO NO CUMPLIDO: un subagente devolvio un mensaje que no encaja en ninguna rama del contrato esperado.
            7. ERROR DE API DE CLAUDE / SATURACION TECNICA: imposible continuar (rate limit, error 5xx persistente del provider, etc.).

            EVALUA HONESTAMENTE tu estado actual: paraste por una de esas 7 razones?

            - Si SI: vuelve a parar inmediatamente. Tu siguiente mensaje debe ser breve y explicito sobre cual de las 7 causas aplico (ej. "Parada justificada: NO_BUGS_FOUND tras barrido honesto"). El hook detectara la segunda parada consecutiva (stop_hook_active=true) y te dejara terminar.

            - Si NO (paraste porque "ya esta", "el ciclo lleva rato", "ya he arreglado varios bugs", fatiga implicita, sensacion de cierre, o cualquier motivo distinto a las 7 anteriores): NO PARES. Continua el ciclo desde la fase apropiada. Si el ultimo subagente activo fue el tester con exito, vuelve a FASE 1 (lanza el detector). Si estabas entre fases, retoma donde quedaste. Si la sesion ha durado tanto que has perdido el hilo, vuelve a FASE 1.

            No confirmes con texto ANTES de continuar; simplemente continua lanzando el subagente correspondiente. El texto solo aparece si vas a parar (caso SI).
            '@
            $payload = @{ decision = "block"; reason = $reason } | ConvertTo-Json -Compress
            Write-Output $payload
            exit 0
---

# /cycle-autofix-bugs — Ciclo autónomo de detección, corrección y validación de bugs

**Piensa profundamente y analiza con rigor cada transición del ciclo. Ultrathink.**

## Misión
Ejecutar indefinidamente el siguiente ciclo:

```
bug-detector → bug-validador → bug-correccion → bug-tester → (commit) → bug-detector → ...
```

hasta que:
- el usuario pulse **Esc** (parada manual), o
- bug-detector devuelva `NO_BUGS_FOUND` (fin natural del ciclo), o
- ocurra una **condición excepcional** documentada (ver § Paradas).

Un hook `Stop` en el frontmatter evita paradas espurias: si terminas el turno sin haber alcanzado ninguna de las 7 causas legítimas, el hook te re-inyecta una instrucción para continuar el ciclo (ver el bloque YAML del hook arriba).

## Preparación inicial (haz esto UNA VEZ al arrancar el ciclo)

1. **Asegúrate de que existe la carpeta `bug-analisis/`** en el repo actual. Si no, créala con PowerShell:
   ```powershell
   if (-not (Test-Path "bug-analisis")) { New-Item -ItemType Directory -Path "bug-analisis" | Out-Null }
   ```

2. **Asegúrate de que existen los 3 subdirectorios del ledger** dentro de `bug-analisis/`:
   ```powershell
   foreach ($sub in @("bugs-pendientes-arreglar", "bugs-imposibles-arreglar", "bugs-arreglados-historial")) {
       $path = Join-Path "bug-analisis" $sub
       if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
   }
   ```
   `bugs-pendientes-arreglar/` es el ledger en curso; `bugs-imposibles-arreglar/` guarda bugs que se rindieron tras 3 intentos; `bugs-arreglados-historial/` guarda los arreglados con su fecha. NO se renombran archivos al moverlos entre subdirs (el slug se preserva).

3. **Asegúrate de que `.gitignore` contiene la línea `bug-analisis/`** (la línea cubre los 3 subdirectorios). Si no, añádela:
   ```powershell
   if (-not (Test-Path ".gitignore")) { New-Item -ItemType File -Path ".gitignore" | Out-Null }
   if (-not (Select-String -Path ".gitignore" -Pattern "^bug-analisis/?$" -Quiet)) { Add-Content -Path ".gitignore" -Value "bug-analisis/" }
   ```

4. **Limpia screenshots residuales de Playwright en la raíz** — si un ciclo anterior dejó archivos `.png` untracked en la raíz del repo (capturas de `browser_take_screenshot` no barridas por interrupción manual, abort de Esc, o por venir de antes del parche del bug-tester), elimínalos ahora para que la verificación del working tree en el paso siguiente no aborte el ciclo falsamente:
   ```powershell
   git status --porcelain | ForEach-Object {
       if ($_ -match '^\?\? ([^/\\]+\.png)$') {
           Remove-Item -LiteralPath $matches[1] -Force -ErrorAction SilentlyContinue
       }
   }
   ```
   Restricción: borra **únicamente** `.png` **untracked en la raíz** (línea `?? <nombre>.png` sin barras). Nunca toca `.png` versionados ni `.png` dentro de subdirectorios (p. ej. `frontend/public/`, `frontend/src/assets/`).

5. **Verifica que el working tree NO tiene cambios sin commit relacionados con la app** (`git status --porcelain` excluyendo `bug-analisis/`). Si los hay → para con condición excepcional informando al usuario; un working tree dirty contamina el primer commit del ciclo.

6. **Informa al usuario**: "Ciclo autofix iniciado. La app debe estar corriendo en localhost. Pulsa Esc en cualquier momento para parar."

## Estado que mantienes durante el ciclo

Mantén en tu razonamiento (no en archivo):
- `current_slug` — slug del bug en curso.
- `current_payload` — payload devuelto por bug-detector (URL, selector, descripción, pasos).
- `fix_attempts` — contador 0..3. Se incrementa al lanzar bug-correccion; se resetea a 0 cuando bug-tester valida éxito O cuando se anota un bug como `unfixable`.
- `previous_fix_summary` — último `FIX_APPLIED:` recibido (se inyecta en retries).
- `tester_findings` — último `BUG_STILL_BROKEN:` recibido (se inyecta en retries).

## Bucle principal — sigue estas fases en orden

### FASE 1 — Detección

Invoca el subagente `bug-detector` (vía tool `Agent`) con este `task_prompt`:

```
Busca UN bug nuevo en la app corriendo en localhost. Sigue exactamente el procedimiento de tu system prompt:
1. Lee TODOS los mds dentro de `bug-analisis/bugs-pendientes-arreglar/` y `bug-analisis/bugs-imposibles-arreglar/` para evitar duplicados (slug + comparación semántica) y para evitar reportar bugs ya marcados como imposibles. NO leas `bug-analisis/bugs-arreglados-historial/` (las regresiones SÍ deben reportarse como nuevos bugs).
2. Navega con Playwright meticulosamente como un USUARIO experimentado. NO leas código fuente del repo más allá de los .md de documentación general (CLAUDE.md, *_guide.md, README.md, docs/features/). NO especules sobre la causa raíz.
3. Cuando confirmes un bug nuevo, persiste `bug-analisis/bugs-pendientes-arreglar/<slug>.md` con status:pending y sin campo fix_attempts (lo gestiona el orquestador en memoria).
4. Devuelve EXACTAMENTE:
   - `BUG_FOUND: <slug>` seguido de un bloque YAML con url, selector, descripcion_corta, pasos.
   - o `NO_BUGS_FOUND` si tras barrido exhaustivo no encuentras nada nuevo.
```

**Enrutamiento del mensaje devuelto** (matching estricto):

- Si el mensaje (trimmed) **es exactamente** `NO_BUGS_FOUND` → **FIN DEL CICLO**. Informa al usuario "Ciclo terminado. El detector no encuentra bugs nuevos." y termina.
- Si el mensaje **empieza por** `BUG_FOUND: ` → extrae:
  - `current_slug` = la palabra que sigue al prefijo, hasta el primer salto de línea.
  - `current_payload` = todo el bloque YAML que viene después.
  - Resetea `fix_attempts = 0`, `previous_fix_summary = ""`, `tester_findings = ""`.
  - Continúa a FASE 2.
- Cualquier otro mensaje → **CONDICIÓN EXCEPCIONAL** (contrato no cumplido). Informa al usuario el mensaje literal recibido y termina.

### FASE 2 — Validación

Invoca el subagente `bug-validador` con este `task_prompt`:

```
Re-confirma con Playwright si este bug reportado es real o un falso positivo, y si es real, enriquece el md con detalles adicionales observables.
Slug: <current_slug>
Payload del detector:
<current_payload>
Lee `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` para todo el detalle.

Procedimiento:
1. Re-navega con Playwright los pasos de reproducción del md.
2. Si tras la re-navegación dudas si es intencional, consulta CLAUDE.md, *_guide.md, docs/features/ y, si necesitas confirmar/descartar intencionalidad, lee código (solo para eso, NO para diagnosticar la causa raíz ni proponer fix).
3. Si confirmas REAL_BUG: ENRIQUECE el md (con Edit) añadiendo cualquier detalle observable adicional (pasos intermedios omitidos, condiciones, evidencia visual/consola/network, variantes del síntoma). NO menciones archivos/funciones del repo, NO sugieras solución técnica. Solo describes MEJOR el síntoma.
4. Devuelve:
   - `REAL_BUG: <current_slug>` si confirmas.
   - `FALSE_POSITIVE: <current_slug>: <razón breve>` y BORRA el archivo `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` antes de devolver.
```

**Enrutamiento**:

- Si empieza por `REAL_BUG: ` → continúa a FASE 3.
- Si empieza por `FALSE_POSITIVE: ` → informa al usuario brevemente "Bug `<slug>` descartado como falso positivo: <razón>." y **vuelve a FASE 1** (relanza detector).
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### FASE 3 — Corrección

Incrementa `fix_attempts += 1`. Si `fix_attempts > 3` (no debería suceder porque el manejo de retry corta antes; safety net): salta directo al manejo de **reintentos agotados** en FASE 4.

Invoca el subagente `bug-correccion` con este `task_prompt`:

```
Arregla el bug indicado. Intento <fix_attempts> de 3.
Slug: <current_slug>
Payload del detector:
<current_payload>
{Solo si fix_attempts > 1, añade textualmente este bloque:}
Intento anterior (NO repitas el mismo cambio, cambia de enfoque):
<previous_fix_summary>
Hallazgos del tester en el intento previo:
<tester_findings>
{fin del bloque condicional}
Procedimiento:
1. Lee `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md`. El md puede tener una sección `## Detalles adicionales (validador)` con info de la re-confirmación.
2. Identifica la causa raíz y aplica la edición mínima.
3. NO uses Playwright. NO hagas commits. NO modifiques bug-analisis/.
4. Devuelve `FIX_APPLIED: <current_slug>: <resumen 1-2 frases>` o `FIX_FAILED: <current_slug>: <razón>`.
```

**Enrutamiento**:

- Si empieza por `FIX_APPLIED: <slug>: ` → guarda `previous_fix_summary = <resumen>` (todo lo que sigue al segundo `: `). Continúa a FASE 4.
- Si empieza por `FIX_FAILED: <slug>: ` → guarda `tester_findings = "Corrector se rindió: <razón>"`. Aplica el **manejo de retry** al final de FASE 4 (sin pasar por el tester, equivale a un fallo del intento).
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### FASE 4 — Testing + commit

Invoca el subagente `bug-tester` con este `task_prompt`:

```
Valida si el bug está arreglado y, si lo está, mueve el md al historial y haz commit local (sin push).
Slug: <current_slug>
Payload del detector (incluye pasos de reproducción):
<current_payload>
Resumen del fix aplicado por bug-correccion:
<previous_fix_summary>
Lee `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` para todo el contexto.

Procedimiento:
1. Reproduce EXACTAMENTE los pasos del detector con Playwright (misma configuración que él usó).
2. Si el bug ya muestra el comportamiento esperado:
   a. Mueve el md de `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` a `bug-analisis/bugs-arreglados-historial/<current_slug>.md`, cambiando en el frontmatter `status: pending` → `status: fixed` y añadiendo `fixed_at: <ISO8601 UTC>`. Usa el snippet PowerShell de tu system prompt (paso 3a.1).
   b. Limpia screenshots .png residuales en la raíz (pre-commit).
   c. `git status --porcelain` para inspeccionar el diff (debe mostrar solo cambios del fix; nada de bug-analisis/ porque está gitignored).
   d. `git add <rutas explícitas>`. NUNCA -A. NUNCA -a. NUNCA -u.
   e. `git commit -m "<mensaje natural de programador, ~70 chars, sin marca de flujo>"`. NUNCA --no-verify. NO push.
   f. Limpia screenshots .png residuales (post-commit, defensa en profundidad).
   g. Devuelve LITERAL EXACTO: `El bug ha sido solucionado correctamente. Vuelve a empezar el ciclo lanzando el subagente detector.`
3. Si el bug sigue roto:
   a. NO modifiques nada.
   b. Devuelve `BUG_STILL_BROKEN: <current_slug>: <findings detallados>`.
4. Si el commit falla por cualquier motivo (hooks, conflictos, dirty inesperado):
   a. NO uses flags destructivos.
   b. NO deshagas el move del md al historial (si el move ya ocurrió, déjalo; el orquestador informará al usuario).
   c. Devuelve `TESTER_COMMIT_FAILED: <current_slug>: <error literal>`.
```

**Enrutamiento (matching estricto, ver § Matching de cadenas)**:

- **Igualdad exacta** (tras trim de whitespace en bordes) con la cadena `El bug ha sido solucionado correctamente. Vuelve a empezar el ciclo lanzando el subagente detector.` → informa al usuario "Bug `<current_slug>` arreglado y commiteado.", resetea state (`current_slug`, `current_payload`, `fix_attempts`, `previous_fix_summary`, `tester_findings`) y **vuelve a FASE 1**.
- Si empieza por `BUG_STILL_BROKEN: ` → guarda `tester_findings = <findings>` y aplica el **manejo de retry** (sub-sección).
- Si empieza por `TESTER_COMMIT_FAILED: ` → **CONDICIÓN EXCEPCIONAL**. Informa al usuario con el error literal y termina.
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### Manejo de retry (cuando tester reporta `BUG_STILL_BROKEN`, o corrector reporta `FIX_FAILED`)

- Si `fix_attempts < 3` → **vuelve a FASE 3** (relanza bug-correccion con `previous_fix_summary` y `tester_findings` actualizados; el contador `fix_attempts` se incrementará allí al entrar).
- Si `fix_attempts == 3` → **reintentos agotados**:
  - Mueve el md de `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` a `bug-analisis/bugs-imposibles-arreglar/<current_slug>.md`, modificando frontmatter (`status: pending` → `status: unfixable`, añadir `marked_unfixable_at: <ISO8601 UTC>`) y añadiendo al final una sección `## Razón de unfixable` con el último `tester_findings`. Ejecuta el siguiente bloque PowerShell (tú, desde el main):
    ```powershell
    $slug = "<current_slug>"
    $findings = @"
    <tester_findings textual>
    "@
    $src = "bug-analisis/bugs-pendientes-arreglar/$slug.md"
    $dst = "bug-analisis/bugs-imposibles-arreglar/$slug.md"
    $now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $content = Get-Content $src -Raw
    $replacement = "status: unfixable`nmarked_unfixable_at: $now"
    $content = $content -replace '(?m)^status:\s*pending\s*$', $replacement
    $footer = "`n`n## Razón de unfixable`n`n$findings`n"
    Set-Content -Path $dst -Value ($content + $footer) -Encoding utf8 -NoNewline
    Remove-Item -LiteralPath $src -Force
    ```
  - Informa al usuario: "Bug `<current_slug>` marcado como **unfixable** tras 3 intentos. Movido a `bugs-imposibles-arreglar/`. Pasamos al siguiente."
  - Resetea state: `current_slug = ""`, `current_payload = ""`, `fix_attempts = 0`, `previous_fix_summary = ""`, `tester_findings = ""`.
  - **Vuelve a FASE 1**.

## Matching de cadenas (regla del orquestador)

- **Igualdad exacta tras trim**: la frase de éxito del tester debe igualar carácter por carácter, ignorando solo whitespace en bordes (espacios, saltos de línea, tabs). Sin trim intermedio. Sin regex laxa. Es la única vía de éxito y por eso se valida con la regla más estricta.
- **Prefijo tras trim**: para todas las demás cadenas del contrato (`BUG_FOUND: `, `NO_BUGS_FOUND`, `REAL_BUG: `, `FALSE_POSITIVE: `, `FIX_APPLIED: `, `FIX_FAILED: `, `BUG_STILL_BROKEN: `, `TESTER_COMMIT_FAILED: `), se comprueba `startswith` tras trim de bordes.
- `NO_BUGS_FOUND` se comprueba como **igualdad exacta tras trim** (sin payload).
- Si dudas, **categoriza como CONDICIÓN EXCEPCIONAL** que continuar con un mensaje ambiguo.

## Paradas (formales)

### Parada manual
- El usuario pulsa **Esc** en la sesión. Esto interrumpe el turno actual; si hay un subagente en foreground, se cancela también. El hook `Stop` NO se dispara en interrupciones — el ciclo termina inmediatamente.
- Si el usuario escribe en libre "para el ciclo" / "stop" / similar entre transiciones, también detecta y termina limpio. En este caso el hook `Stop` SÍ se dispara: tu primera parada se bloquea con el reason, evalúas y confirmas la parada en el siguiente turno citando la causa 1 (PARADA MANUAL); la segunda parada consecutiva (`stop_hook_active=true`) se permite.

### Parada por fin natural
- bug-detector devuelve `NO_BUGS_FOUND` → informa "Detector no encuentra bugs nuevos. Ciclo terminado." y para. El hook `Stop` bloquea la primera parada; confirmas con la causa 2 (NO_BUGS_FOUND) y la segunda parada se permite.

### Condiciones excepcionales (paradas automáticas con informe)

Termina inmediatamente y muestra al usuario el contexto exacto cuando se cumpla cualquiera de:

1. **Contrato no cumplido**: un subagente devuelve un mensaje que no encaja en ninguna rama del contrato esperado.
2. **Tester reporta `TESTER_COMMIT_FAILED`**: commit fallido por hooks/conflictos/working tree dirty. NO intentes resolverlo.
3. **Working tree dirty inesperado al iniciar nueva iteración** (FASE 1): tras volver del tester, si `git status --porcelain` muestra cambios no commiteados (que deberían haber sido commiteados por el tester o no existir), para; un nuevo bug arrastraría diff residual.
4. **El subagente detector o tester reporta que el MCP de Playwright es inalcanzable** (en su payload de NO_BUGS_FOUND o BUG_STILL_BROKEN): informa al usuario y para; es responsabilidad del usuario reactivar el MCP.

En todos los casos, el hook `Stop` se dispara: bloquea la primera parada, confirmas la causa (3, 2, 4 o 5 según corresponda) y la segunda parada se permite.

**Nota: los reintentos agotados (3 fallos sobre el mismo bug) NO son parada excepcional** — se mueve el bug a `bugs-imposibles-arreglar/` y el ciclo sigue con el siguiente. Si todos los bugs detectables son `unfixable`, eventualmente el detector devolverá `NO_BUGS_FOUND` (porque los `unfixable` se filtran de su búsqueda) y el ciclo termina natural.

## Restricciones (no negociables)

- **Sin sleeps, sin timers, sin polling**. El bucle solo progresa cuando un subagente devuelve algo.
- **Sin push automático**. Solo commits locales.
- **Sin flags destructivos** en ninguna operación git (ni del tester ni desde el orquestador).
- **Cuatro subagentes y solo cuatro**. NO inventes subagentes adicionales.
- **Un bug por iteración**. NO paralelices.
- **El ledger vive en `bug-analisis/` (3 subdirectorios) y está gitignored. Nunca se commitea**. Los mds se MUEVEN entre subdirs (preservando el slug en el nombre); nunca se renombran ni se borran salvo en FALSE_POSITIVE.

## Arranque

Tras la preparación inicial, ve directamente a FASE 1 sin esperar input del usuario.
