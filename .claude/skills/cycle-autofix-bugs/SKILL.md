---
description: Ciclo autónomo que valida funcionalidad a funcionalidad (guiado por Docs/features/ + Docs/limits/) detectando, validando, corrigiendo y testeando bugs con 4 subagentes (bug-detector, bug-validador, bug-correccion, bug-tester) coordinados desde la conversación principal. Navega la app real en localhost con el navegador de amuelas11 (MCP chrome-a11) entrando por Dev login. El bucle progresa solo por finalización de tareas (no por tiempo). Lanzar manualmente con /cycle-autofix-bugs. La app debe estar corriendo en localhost y el navegador chrome-a11 enganchado antes de invocar.
argument-hint: (sin argumentos)
disable-model-invocation: true
user-invocable: true
model: opus
effort: max
allowed-tools: Agent(bug-detector, bug-validador, bug-correccion, bug-tester), Read, Write, Edit, Glob, Grep, Bash, PowerShell, ToolSearch, mcp__chrome-a11__list_pages
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

            Este ciclo recorre las funcionalidades de Docs/features/ una a una y esta disenado para ser INFINITO hasta una de estas 7 paradas LEGITIMAS:

            1. PARADA MANUAL: el usuario pidio explicitamente parar (mensaje "stop" / "para" / "termina" entre transiciones de fase, o un mensaje libre equivalente).
            2. TODAS LAS FUNCIONALIDADES CUBIERTAS: no queda ninguna funcionalidad de Docs/features/ sin una entrada en bug-analisis/funcionalidades-cubiertas.md tras un barrido honesto. (FEATURE_CLEAN sobre una funcionalidad NO es parada: se anota la funcionalidad y se pasa a la siguiente.)
            3. TESTER_COMMIT_FAILED: el bug-tester reporto fallo de commit no recuperable (hooks pre-commit bloqueando, conflictos, working tree dirty insalvable).
            4. WORKING TREE DIRTY INESPERADO al iniciar nueva iteracion (FASE 1 detecto cambios sin commit no atribuibles al ciclo).
            5. NAVEGADOR INALCANZABLE: el detector/validador/tester reporto FEATURE_BLOCKED, o la app en localhost o el MCP chrome-a11 dejaron de responder.
            6. CONTRATO NO CUMPLIDO: un subagente devolvio un mensaje que no encaja en ninguna rama del contrato esperado.
            7. ERROR DE API DE CLAUDE / SATURACION TECNICA: imposible continuar (rate limit, error 5xx persistente del provider, etc.).

            EVALUA HONESTAMENTE tu estado actual: paraste por una de esas 7 razones?

            - Si SI: vuelve a parar inmediatamente. Tu siguiente mensaje debe ser breve y explicito sobre cual de las 7 causas aplico (ej. "Parada justificada: TODAS LAS FUNCIONALIDADES CUBIERTAS"). El hook detectara la segunda parada consecutiva (stop_hook_active=true) y te dejara terminar.

            - Si NO (paraste porque "ya esta", "el ciclo lleva rato", "ya he arreglado varios bugs", "ya he cubierto varias funcionalidades", fatiga implicita, sensacion de cierre, o cualquier motivo distinto a las 7 anteriores): NO PARES. Continua el ciclo desde la fase apropiada. Si acabas de cerrar una funcionalidad (FEATURE_CLEAN anotada), pasa a SELECCION DE FUNCIONALIDAD y elige la siguiente. Si el ultimo subagente activo fue el tester con exito, vuelve a FASE 1 sobre la MISMA funcionalidad en curso. Si estabas entre fases, retoma donde quedaste. Si la sesion ha durado tanto que has perdido el hilo, reconstruye la funcionalidad en curso leyendo bug-analisis/funcionalidades-cubiertas.md (la primera funcionalidad de Docs/features/ sin entrada) y vuelve a FASE 1.

            No confirmes con texto ANTES de continuar; simplemente continua lanzando el subagente correspondiente. El texto solo aparece si vas a parar (caso SI).
            '@
            $payload = @{ decision = "block"; reason = $reason } | ConvertTo-Json -Compress
            Write-Output $payload
            exit 0
---

# /cycle-autofix-bugs — Validación por funcionalidad con detección, corrección y testeo de bugs

**Piensa profundamente y analiza con rigor cada transición del ciclo. Ultrathink.**

## Misión
Recorrer las funcionalidades documentadas en `Docs/features/` **una a una**. Para cada funcionalidad, ejecutar este ciclo hasta que esté limpia, y solo entonces pasar a la siguiente:

```
seleccionar funcionalidad NO cubierta
        │
        ▼
bug-detector (acotado a esa funcionalidad)
        │
        ├─ BUG_FOUND ──▶ bug-validador ─▶ bug-correccion ─▶ bug-tester ─▶ (commit) ─┐
        │                                                                            │
        │   ◀──────────────────────  vuelve al MISMO detector  ◀────────────────────┘
        │
        └─ FEATURE_CLEAN ─▶ anotar la funcionalidad como cubierta ─▶ siguiente funcionalidad
```

El detector ya no busca a ciegas: explora **una funcionalidad concreta** usando `Docs/features/<slug>.md` + `Docs/limits/<slug>.md` como checklist exhaustivo (todo el flujo, opciones, casos límite y "lo que NO soporta"). Arregla cada bug que aparezca y **sigue cubriendo la misma funcionalidad** hasta que una pasada completa no encuentre ni un solo bug nuevo; solo entonces la funcionalidad se da por cubierta y se anota en `bug-analisis/funcionalidades-cubiertas.md`.

El ciclo termina cuando:
- el usuario pulse **Esc** (parada manual), o
- **todas** las funcionalidades de `Docs/features/` tengan entrada en el ledger de cubiertas, o
- ocurra una **condición excepcional** documentada (ver § Paradas).

Un hook `Stop` en el frontmatter evita paradas espurias: si terminas el turno sin haber alcanzado ninguna de las 7 causas legítimas, el hook re-inyecta una instrucción para continuar (ver el bloque YAML del hook arriba).

## Navegación: navegador de amuelas11 (MCP chrome-a11) + Dev login

Los 3 subagentes que navegan (detector, validador, tester) usan el **MCP `chrome-a11`** (Chrome real del perfil `C:\chrome-mcp-a11`, puerto 9223 — el carril de la skill `/navegador-amuelas11`). NO usan Playwright. Entran a la app por **`http://localhost:5173/login`** pulsando el botón **"Dev login"**, que crea la sesión del usuario de prueba **amuelas14@gmail.com** (4 cuentas de correo conectadas: `amuelas14@gmail.com`, `amuelas11`, `amuelas30`, `amuelas32`). El Chrome es **persistente y compartido** entre subagentes: la sesión sobrevive entre fases y **no se cierra el navegador** al terminar cada subagente.

## Preparación inicial (haz esto UNA VEZ al arrancar el ciclo)

1. **Asegúrate de que existe la carpeta `bug-analisis/`** y sus **3 subdirectorios** del ledger de bugs:
   ```powershell
   if (-not (Test-Path "bug-analisis")) { New-Item -ItemType Directory -Path "bug-analisis" | Out-Null }
   foreach ($sub in @("bugs-pendientes-arreglar", "bugs-imposibles-arreglar", "bugs-arreglados-historial")) {
       $path = Join-Path "bug-analisis" $sub
       if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
   }
   ```
   `bugs-pendientes-arreglar/` es el ledger de bugs en curso; `bugs-imposibles-arreglar/` guarda bugs que se rindieron tras 3 intentos; `bugs-arreglados-historial/` guarda los arreglados con su fecha. Los mds se MUEVEN entre subdirs (preservando el slug); nunca se renombran ni se borran salvo en FALSE_POSITIVE.

2. **Asegúrate de que existe el ledger de funcionalidades cubiertas** `bug-analisis/funcionalidades-cubiertas.md`. Es la fuente de verdad persistente del progreso por funcionalidad (qué funcionalidades NO hay que volver a explorar). Si no existe, créalo con cabecera:
   ```powershell
   $ledger = "bug-analisis/funcionalidades-cubiertas.md"
   if (-not (Test-Path $ledger)) {
       $header = @'
   # Funcionalidades cubiertas por /cycle-autofix-bugs

   Cada entrada = una funcionalidad de Docs/features/ validada de extremo a extremo (todo su
   flujo, opciones, casos límite y "lo que NO soporta") sin bugs nuevos. El ciclo NO vuelve a
   explorar las funcionalidades listadas aquí. Para re-validar una, borra su entrada.

   '@
       Set-Content -Path $ledger -Value $header -Encoding utf8
   }
   ```
   No es un `CLAUDE.md` (a propósito: este archivo es estado mutable que el ciclo reescribe; un `CLAUDE.md` estaría protegido por el hook de inmutabilidad). Vive en `bug-analisis/` (gitignored); nunca se commitea.

3. **Asegúrate de que `.gitignore` contiene la línea `bug-analisis/`** (cubre los subdirectorios y el ledger). Si no, añádela:
   ```powershell
   if (-not (Test-Path ".gitignore")) { New-Item -ItemType File -Path ".gitignore" | Out-Null }
   if (-not (Select-String -Path ".gitignore" -Pattern "^bug-analisis/?$" -Quiet)) { Add-Content -Path ".gitignore" -Value "bug-analisis/" }
   ```

4. **Limpia residuos en la raíz (`.png` y volcados `.md` de snapshot)** de sesiones anteriores, para que la verificación del working tree no aborte el ciclo falsamente:
   ```powershell
   git status --porcelain | ForEach-Object {
       if ($_ -match '^\?\? ([^/\\]+\.png)$') {
           Remove-Item -LiteralPath $matches[1] -Force -ErrorAction SilentlyContinue
       } elseif ($_ -match '^\?\? ([^/\\]+\.md)$') {
           $f = $matches[1]
           $firstLine = (Get-Content -LiteralPath $f -TotalCount 1 -ErrorAction SilentlyContinue)
           if ($firstLine -match '\[ref=e' -or $firstLine -match '^\s*- generic') {
               Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
           }
       }
   }
   ```
   Restricción: borra **únicamente** archivos **untracked en la raíz** (línea `?? <nombre>` sin barras). Los `.png` sin condición; los `.md` **solo** si su primera línea tiene firma de snapshot (`[ref=e` o `- generic`). Nunca toca archivos versionados, ni subdirectorios, ni `bug-analisis/`.

5. **Verifica que el working tree NO tiene cambios sin commit relacionados con la app** (`git status --porcelain` excluyendo `bug-analisis/`). Si los hay → para con condición excepcional informando al usuario; un working tree dirty contamina el primer commit del ciclo.

6. **Garantiza el navegador (MCP chrome-a11 enganchado).** Es el equivalente a "la app debe estar corriendo": el usuario debe dejar el navegador de amuelas11 conectado antes de lanzar. El ciclo no puede reiniciar Claude Code por su cuenta, así que aquí solo lo verifica:
   1. Carga la tool del MCP: `ToolSearch` → `select:mcp__chrome-a11__list_pages`.
   2. Garantiza que Chrome está levantado en el puerto 9223 con el perfil de amuelas11:
      ```powershell
      & "C:\Users\MiniPC\.claude\skills\navegador-amuelas11\ensure-chrome.ps1"
      ```
      `ALREADY_UP` / `LAUNCHED_UP` → puerto OK. `CHROME_NOT_FOUND` / `LAUNCH_TIMEOUT` → para con condición excepcional (avisa al usuario; puede necesitar cerrar Chrome del perfil o lanzarlo a mano).
   3. Verifica el **enganche del MCP** llamando `mcp__chrome-a11__list_pages`. Si devuelve páginas → listo. Si da error de conexión → el MCP no enganchó al arrancar Claude Code y **no se puede reconectar en caliente**: para con condición excepcional pidiendo al usuario que (re)arranque el navegador y reinicie Claude Code (`claude --continue`) antes de relanzar el ciclo.

7. **Informa al usuario**: "Ciclo autofix por funcionalidad iniciado. App en localhost:5173 + navegador amuelas11 (Dev login). Recorreré las funcionalidades de Docs/features/ una a una. Pulsa Esc para parar."

## Estado que mantienes durante el ciclo

Mantén en tu razonamiento (no en archivo, salvo el ledger):
- `current_feature` — slug de la funcionalidad en curso (un doc de `Docs/features/`, sin extensión).
- `feature_fixed_bugs` — lista de slugs de bugs arreglados+commiteados durante la cobertura de `current_feature` (para el resumen del ledger). Se vacía al empezar cada funcionalidad.
- `feature_salvedades` — lista de salvedades de `current_feature`: bugs marcados `unfixable` durante su cobertura (slug + razón) y partes "fuera de alcance" reportadas por el detector en `FEATURE_CLEAN`. Se vacía al empezar cada funcionalidad.
- `current_slug` — slug del bug en curso.
- `current_payload` — payload devuelto por bug-detector (URL, selector, descripción, pasos).
- `fix_attempts` — contador 0..3. Se incrementa al lanzar bug-correccion; se resetea a 0 cuando bug-tester valida éxito O cuando se anota un bug como `unfixable`.
- `previous_fix_summary` — último `FIX_APPLIED:` recibido (se inyecta en retries).
- `tester_findings` — último `BUG_STILL_BROKEN:` recibido (se inyecta en retries).

## Selección de funcionalidad (al arrancar y cada vez que se cierra una funcionalidad)

1. Lista las funcionalidades candidatas: usa Glob `Docs/features/*.md` y **excluye** `CLAUDE.md` y `README.md`. El slug es el nombre del archivo sin `.md`. El orden de exploración es el del índice de `Docs/features/README.md` (por áreas); si dudas, el orden alfabético de los archivos sirve.
2. Lee `bug-analisis/funcionalidades-cubiertas.md` y extrae el conjunto de slugs ya cubiertos (las cabeceras `## <slug>`).
3. `current_feature` = la **primera funcionalidad candidata sin entrada** en el ledger.
   - Si **todas** las candidatas tienen entrada → **FIN DEL CICLO** (causa 2). Informa "Todas las funcionalidades de Docs/features/ están cubiertas. Ciclo terminado." y termina.
4. Resetea `feature_fixed_bugs = []`, `feature_salvedades = []`, y el estado de bug (`current_slug=""`, `current_payload=""`, `fix_attempts=0`, `previous_fix_summary=""`, `tester_findings=""`).
5. Informa brevemente: "Cubriendo funcionalidad: `<current_feature>`." y continúa a FASE 1.

## Bucle principal — sigue estas fases en orden

### FASE 1 — Detección (acotada a `current_feature`)

Invoca el subagente `bug-detector` (vía tool `Agent`) con este `task_prompt`:

```
Explora a fondo UNA funcionalidad de la app corriendo en localhost:5173 y devuelve un bug nuevo o decláralo limpia. Sigue exactamente el procedimiento de tu system prompt.
Funcionalidad a cubrir (slug): <current_feature>
Documentación de referencia (tu checklist): Docs/features/<current_feature>.md y Docs/limits/<current_feature>.md

1. Entra a la app por Dev login (http://localhost:5173/login → botón "Dev login"; usuario amuelas14 con 4 cuentas de prueba). Si la app, el Dev login o el MCP chrome-a11 no responden, devuelve FEATURE_BLOCKED.
2. Lee Docs/features/<current_feature>.md + Docs/limits/<current_feature>.md y deriva un checklist exhaustivo (todo el flujo, opciones, botones, casos límite y "lo que NO soporta").
3. Lee TODOS los mds de bug-analisis/bugs-pendientes-arreglar/ y bug-analisis/bugs-imposibles-arreglar/ para evitar duplicados e imposibles. NO leas bug-analisis/bugs-arreglados-historial/.
4. Recorre el checklist navegando con chrome-a11 como un USUARIO experimentado. NO leas código fuente del repo más allá de la documentación (.md). NO especules sobre la causa raíz. Acciones con efecto real permitidas SOLO sobre las 4 cuentas de prueba; envíos dirigidos solo entre ellas.
5. Si confirmas un bug nuevo, persiste bug-analisis/bugs-pendientes-arreglar/<bug-slug>.md (status:pending, con campo feature:<current_feature>) y devuelve BUG_FOUND.
6. Devuelve EXACTAMENTE uno de:
   - BUG_FOUND: <bug-slug> + bloque YAML (url, selector, descripcion_corta, pasos).
   - FEATURE_CLEAN: <current_feature>  (+ líneas opcionales `salvedad:` para lo no ejercitable vía Dev login, p. ej. OAuth real).
   - FEATURE_BLOCKED: <current_feature>: <razón> (entorno/navegador caído).
```

**Enrutamiento del mensaje devuelto** (matching por presencia del token, ver § Matching de cadenas):

- Si aparece el token `FEATURE_CLEAN: <current_feature>` → **CIERRE DE FUNCIONALIDAD**:
  - Captura cualquier línea `salvedad:` que el detector haya añadido y añádela a `feature_salvedades`.
  - Anota la funcionalidad en el ledger (ver § Cierre de funcionalidad).
  - Vuelve a **Selección de funcionalidad** (siguiente funcionalidad).
- Si aparece un token `BUG_FOUND: ` → extrae:
  - `current_slug` = la palabra que sigue al prefijo, hasta el primer salto de línea.
  - `current_payload` = el bloque YAML que viene después.
  - Resetea `fix_attempts = 0`, `previous_fix_summary = ""`, `tester_findings = ""`.
  - Continúa a FASE 2.
- Si aparece un token `FEATURE_BLOCKED: ` → **CONDICIÓN EXCEPCIONAL** (navegador/app inalcanzable). Informa al usuario el motivo literal y termina.
- Cualquier otro mensaje → **CONDICIÓN EXCEPCIONAL** (contrato no cumplido). Informa el mensaje literal y termina.

### FASE 2 — Validación

Invoca el subagente `bug-validador` con este `task_prompt`:

```
Re-confirma con el navegador (MCP chrome-a11, vía Dev login en localhost:5173) si este bug reportado es real o un falso positivo, y si es real, enriquece el md con detalles observables.
Slug del bug: <current_slug>
Funcionalidad: <current_feature>
Payload del detector:
<current_payload>
Lee bug-analisis/bugs-pendientes-arreglar/<current_slug>.md para todo el detalle.

Procedimiento:
1. Entra por Dev login y re-navega los pasos de reproducción del md.
2. Si dudas si es intencional, consulta Docs/features/<current_feature>.md, Docs/limits/<current_feature>.md, CLAUDE.md, *_guide.md y, si necesitas confirmar/descartar intencionalidad, lee código (solo para eso, NO para diagnosticar la causa raíz ni proponer fix).
3. Si confirmas REAL_BUG: ENRIQUECE el md (con Edit) con detalle observable adicional. NO menciones archivos/funciones del repo, NO sugieras solución técnica.
4. Devuelve:
   - REAL_BUG: <current_slug> si confirmas.
   - FALSE_POSITIVE: <current_slug>: <razón breve> y BORRA bug-analisis/bugs-pendientes-arreglar/<current_slug>.md antes de devolver.
```

**Enrutamiento**:

- Si aparece `REAL_BUG: ` → continúa a FASE 3.
- Si aparece `FALSE_POSITIVE: ` → informa "Bug `<slug>` descartado como falso positivo: <razón>." y **vuelve a FASE 1** (relanza el detector sobre la MISMA `current_feature`).
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### FASE 3 — Corrección

Incrementa `fix_attempts += 1`. Si `fix_attempts > 3` (no debería suceder; safety net): salta directo al manejo de **reintentos agotados** en FASE 4.

Invoca el subagente `bug-correccion` con este `task_prompt`:

```
Arregla el bug indicado. Intento <fix_attempts> de 3.
Slug del bug: <current_slug>
Funcionalidad: <current_feature>
Payload del detector:
<current_payload>
{Solo si fix_attempts > 1, añade textualmente este bloque:}
Intento anterior (NO repitas el mismo cambio, cambia de enfoque):
<previous_fix_summary>
Hallazgos del tester en el intento previo:
<tester_findings>
{fin del bloque condicional}
Procedimiento:
1. Lee bug-analisis/bugs-pendientes-arreglar/<current_slug>.md. Puede tener una sección `## Detalles adicionales (validador)`. Para entender el comportamiento esperado, puedes leer Docs/features/<current_feature>.md y Docs/limits/<current_feature>.md.
2. Identifica la causa raíz y aplica la edición mínima.
3. NO uses el navegador. NO hagas commits. NO modifiques bug-analisis/.
4. Devuelve FIX_APPLIED: <current_slug>: <resumen 1-2 frases> o FIX_FAILED: <current_slug>: <razón>.
```

**Enrutamiento**:

- Si aparece `FIX_APPLIED: <slug>: ` → guarda `previous_fix_summary = <resumen>` (lo que sigue al segundo `: `). Continúa a FASE 4.
- Si aparece `FIX_FAILED: <slug>: ` → guarda `tester_findings = "Corrector se rindió: <razón>"`. Aplica el **manejo de retry** al final de FASE 4 (sin pasar por el tester; equivale a un fallo del intento).
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### FASE 4 — Testing + commit

Invoca el subagente `bug-tester` con este `task_prompt`:

```
Valida si el bug está arreglado y, si lo está, mueve el md al historial y haz commit local (sin push). Navega con el MCP chrome-a11 (Dev login en localhost:5173).
Slug del bug: <current_slug>
Funcionalidad: <current_feature>
Payload del detector (incluye pasos de reproducción):
<current_payload>
Resumen del fix aplicado por bug-correccion:
<previous_fix_summary>
Lee bug-analisis/bugs-pendientes-arreglar/<current_slug>.md para todo el contexto.

Procedimiento:
1. Entra por Dev login y reproduce EXACTAMENTE los pasos del detector con chrome-a11.
2. Si el bug ya muestra el comportamiento esperado:
   a. Mueve el md de bugs-pendientes-arreglar/ a bugs-arreglados-historial/, cambiando status: pending → status: fixed y añadiendo fixed_at. Usa el snippet de tu system prompt (paso 3a.1).
   b. Limpia residuos .png/.md de snapshot en la raíz (pre-commit).
   c. git status --porcelain para inspeccionar el diff (solo cambios del fix; nada de bug-analisis/).
   d. git add <rutas explícitas>. NUNCA -A. NUNCA -a. NUNCA -u.
   e. git commit -m "<mensaje natural, ~70 chars, sin marca de flujo>". NUNCA --no-verify. NO push.
   f. Limpia residuos .png/.md (post-commit, defensa en profundidad).
   g. NO cierres el navegador (cierra solo pestañas extra con close_page).
   h. Devuelve LITERAL EXACTO: El bug ha sido solucionado correctamente. Vuelve a empezar el ciclo lanzando el subagente detector.
3. Si el bug sigue roto: NO modifiques nada y devuelve BUG_STILL_BROKEN: <current_slug>: <findings detallados>.
4. Si el commit falla (hooks, conflictos, dirty inesperado): NO uses flags destructivos, NO deshagas el move del md, y devuelve TESTER_COMMIT_FAILED: <current_slug>: <error literal>.
```

**Enrutamiento (matching por presencia del token, ver § Matching de cadenas)**:

- Si la frase de éxito `El bug ha sido solucionado correctamente. Vuelve a empezar el ciclo lanzando el subagente detector.` aparece **verbatim** como contenido operativo final **Y** lo confirmas con git (ver § Matching) → informa "Bug `<current_slug>` arreglado y commiteado (funcionalidad `<current_feature>`).", **añade `current_slug` a `feature_fixed_bugs`**, resetea el estado de bug (`current_slug`, `current_payload`, `fix_attempts`, `previous_fix_summary`, `tester_findings`) y **vuelve a FASE 1 sobre la MISMA `current_feature`** (NO cambies de funcionalidad: puede haber más bugs).
- Si aparece `BUG_STILL_BROKEN: ` → guarda `tester_findings = <findings>` y aplica el **manejo de retry**.
- Si aparece `TESTER_COMMIT_FAILED: ` → **CONDICIÓN EXCEPCIONAL**. Informa el error literal y termina.
- Cualquier otro → **CONDICIÓN EXCEPCIONAL**.

### Manejo de retry (cuando tester reporta `BUG_STILL_BROKEN`, o corrector reporta `FIX_FAILED`)

- Si `fix_attempts < 3` → **vuelve a FASE 3** (relanza bug-correccion con `previous_fix_summary` y `tester_findings` actualizados; el contador se incrementará allí al entrar).
- Si `fix_attempts == 3` → **reintentos agotados**:
  - Mueve el md de `bug-analisis/bugs-pendientes-arreglar/<current_slug>.md` a `bug-analisis/bugs-imposibles-arreglar/<current_slug>.md`, modificando frontmatter (`status: pending` → `status: unfixable`, añadir `marked_unfixable_at: <ISO8601 UTC>`) y añadiendo al final una sección `## Razón de unfixable` con el último `tester_findings`:
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
  - Añade `"<current_slug>: unfixable — <resumen de tester_findings>"` a `feature_salvedades` (el bug imposible es una salvedad de la funcionalidad en curso).
  - Informa al usuario: "Bug `<current_slug>` marcado como **unfixable** tras 3 intentos. Movido a `bugs-imposibles-arreglar/`. Sigo cubriendo `<current_feature>`."
  - Resetea el estado de bug (`current_slug=""`, `current_payload=""`, `fix_attempts=0`, `previous_fix_summary=""`, `tester_findings=""`).
  - **Vuelve a FASE 1 sobre la MISMA `current_feature`** (el detector ya evita re-reportar el bug imposible).

## Cierre de funcionalidad (cuando FASE 1 devuelve `FEATURE_CLEAN`)

Anota `current_feature` como cubierta añadiendo una entrada al ledger (no es CLAUDE.md, así que Add-Content está permitido):

```powershell
$feature = "<current_feature>"
$now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$fixed = "<lista de feature_fixed_bugs separada por ', ', o 'ninguno'>"
$salv  = "<lista de feature_salvedades separada por '; ', o 'ninguna'>"
$entry = @"

## $feature
- cubierta_at: $now
- bugs_arreglados_durante_la_cobertura: $fixed
- salvedades: $salv
"@
Add-Content -Path "bug-analisis/funcionalidades-cubiertas.md" -Value $entry -Encoding utf8
```

Informa: "Funcionalidad `<current_feature>` cubierta (bugs arreglados: <N>; salvedades: <…>). Paso a la siguiente." y **vuelve a Selección de funcionalidad**.

## Matching de cadenas (regla del orquestador)

> **⚠️ Realidad del harness (esto MANDA sobre la lectura literal de las reglas).** Todo resultado de subagente llega con dos contaminaciones que hacen IMPOSIBLE el match literal del mensaje completo:
> 1. **Preámbulo**: los subagentes anteponen un resumen conversacional antes de su token de contrato.
> 2. **Coletilla del harness**: la tool `Agent` SIEMPRE concatena al final algo como `...agentId: <id> (use SendMessage with to: '<id>' to continue this agent)`.
>
> **Regla operativa real**: enruta por la **presencia inequívoca de UN único token de contrato bien formado** dentro del mensaje, no por igualdad de la cadena completa ni por `startswith` del mensaje crudo. Para los tokens con prefijo, localiza la línea/segmento que empieza por el token (`BUG_FOUND: <slug>` + su YAML, `FEATURE_CLEAN: <feature>`, `FEATURE_BLOCKED: `, `REAL_BUG: `, `FIX_APPLIED: <slug>: `, etc.). Parar como CONDICIÓN EXCEPCIONAL por un preámbulo cosmético o por la coletilla `agentId` haría el ciclo inoperante.
>
> **Éxito del tester (caso crítico, anti-falso-positivo)**: NO lo valides solo por string. (a) Confirma que la frase `El bug ha sido solucionado correctamente. Vuelve a empezar el ciclo lanzando el subagente detector.` aparece **verbatim** como contenido operativo final, **y** (b) **verifica los efectos reales con git**: `git log -1` muestra el commit del fix con mensaje natural, `git show --stat HEAD` muestra solo el/los fichero(s) del fix, `git status --porcelain` está limpio.

Las reglas describen la **intención** del contrato. Aplícalas sobre el token ya localizado dentro del mensaje, nunca sobre el mensaje crudo:

- **Igualdad exacta (intención)**: la frase de éxito del tester es la única vía de éxito de un bug; por eso, además del git-check, su texto debe coincidir **verbatim**.
- **Prefijo / presencia**: para las demás cadenas del contrato (`BUG_FOUND: `, `FEATURE_CLEAN: `, `FEATURE_BLOCKED: `, `REAL_BUG: `, `FALSE_POSITIVE: `, `FIX_APPLIED: `, `FIX_FAILED: `, `BUG_STILL_BROKEN: `, `TESTER_COMMIT_FAILED: `), identifica el token por su prefijo **dentro** del mensaje.
- `FEATURE_CLEAN: <feature>` es fin de funcionalidad (no de ciclo): trátalo como tal solo si aparece como token operativo y NO hay un `BUG_FOUND: ` compitiendo en el mismo mensaje (si ambos aparecen, gana `BUG_FOUND`).
- Si dudas —output **genuinamente ambiguo o contradictorio**, no un simple preámbulo o coletilla—, **categoriza como CONDICIÓN EXCEPCIONAL** antes que continuar con un mensaje confuso.

## Paradas (formales)

### Parada manual
- El usuario pulsa **Esc**: interrumpe el turno; el hook `Stop` NO se dispara en interrupciones — el ciclo termina inmediatamente.
- Si el usuario escribe en libre "para el ciclo" / "stop" / similar entre transiciones, también termina limpio. El hook `Stop` SÍ se dispara: tu primera parada se bloquea con el reason, confirmas la causa 1 (PARADA MANUAL) en el siguiente turno, y la segunda parada consecutiva (`stop_hook_active=true`) se permite.

### Parada por fin natural
- **Todas** las funcionalidades de `Docs/features/` tienen entrada en `bug-analisis/funcionalidades-cubiertas.md` → informa "Todas las funcionalidades cubiertas. Ciclo terminado." y para. El hook bloquea la primera parada; confirmas con la causa 2 y la segunda se permite.

### Condiciones excepcionales (paradas automáticas con informe)

Termina inmediatamente y muestra al usuario el contexto exacto cuando se cumpla cualquiera de:

1. **Contrato no cumplido**: un subagente devuelve un mensaje que no encaja en ninguna rama del contrato esperado.
2. **Tester reporta `TESTER_COMMIT_FAILED`**: commit fallido por hooks/conflictos/working tree dirty. NO intentes resolverlo.
3. **Working tree dirty inesperado al iniciar nueva iteración** (FASE 1): tras volver del tester, si `git status --porcelain` muestra cambios no commiteados que deberían haber sido commiteados o no existir, para.
4. **Navegador/app inalcanzable**: el detector/validador/tester reporta `FEATURE_BLOCKED`, o la app en localhost o el MCP `chrome-a11` dejan de responder. Informa al usuario y para; es responsabilidad del usuario reactivar la app / el navegador.

En todos los casos, el hook `Stop` se dispara: bloquea la primera parada, confirmas la causa del hook que corresponda (contrato no cumplido → 6, `TESTER_COMMIT_FAILED` → 3, working tree dirty → 4, navegador/app inalcanzable → 5) y la segunda parada se permite.

**Nota: los reintentos agotados (3 fallos sobre el mismo bug) NO son parada** — el bug se mueve a `bugs-imposibles-arreglar/`, se anota como salvedad y el ciclo sigue cubriendo la misma funcionalidad. Tampoco lo es `FEATURE_CLEAN` (cierra una funcionalidad y pasa a la siguiente).

## Restricciones (no negociables)

- **Sin sleeps, sin timers, sin polling**. El bucle solo progresa cuando un subagente devuelve algo.
- **Sin push automático**. Solo commits locales.
- **Sin flags destructivos** en ninguna operación git (ni del tester ni desde el orquestador).
- **Cuatro subagentes y solo cuatro**. NO inventes subagentes adicionales.
- **Un bug por iteración**. NO paralelices.
- **No se cierra el navegador del usuario** (Chrome real persistente del perfil amuelas11). Los subagentes reutilizan la sesión entre fases.
- **El ledger de bugs vive en `bug-analisis/` (3 subdirectorios) y el ledger de funcionalidades en `bug-analisis/funcionalidades-cubiertas.md`; todo está gitignored. Nunca se commitea.** Los mds de bug se MUEVEN entre subdirs (preservando el slug); nunca se renombran ni se borran salvo en FALSE_POSITIVE.

## Arranque

Tras la preparación inicial, ve directamente a **Selección de funcionalidad** y luego a FASE 1, sin esperar input del usuario.
