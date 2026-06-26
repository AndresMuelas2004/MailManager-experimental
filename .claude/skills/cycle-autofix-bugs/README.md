# cycle-autofix-bugs — dependencias, requisitos y notas de diseño

`SKILL.md` orquesta un ciclo autónomo que **valida la app funcionalidad a funcionalidad**: recorre los documentos de `Docs/features/` uno a uno y, para cada uno, ejecuta **detector → validador → corrección → tester → commit** hasta que la funcionalidad queda limpia de bugs. Este README documenta **todo lo que la skill necesita para funcionar** y las decisiones de diseño no obvias, para que sea portable. La lógica operativa vive en `SKILL.md`; no se duplica aquí.

## Modelo del ciclo (qué cambió respecto a la búsqueda ciega)

El ciclo ya **no busca bugs a ciegas por toda la app**. Recorre `Docs/features/` (una funcionalidad = un doc, p. ej. `lupa`, `favoritos`, `composicion-y-envio`) en el orden de su `README`. Para la funcionalidad en curso:

1. El **detector** explora SOLO esa funcionalidad, usando `Docs/features/<slug>.md` + `Docs/limits/<slug>.md` como checklist exhaustivo (todo el flujo, opciones, botones, casos límite y "lo que NO soporta").
2. Si encuentra un bug → validador → corrección → tester → commit, y **se vuelve al mismo detector sobre la misma funcionalidad** (arreglar un bug NO cierra la funcionalidad: puede haber más).
3. Cuando una pasada completa del checklist no encuentra ni un solo bug nuevo, el detector devuelve `FEATURE_CLEAN` y el orquestador **anota la funcionalidad** en `bug-analisis/funcionalidades-cubiertas.md` (con fecha/hora, bugs arreglados durante la cobertura y salvedades) y pasa a la siguiente.

El ciclo termina cuando **todas** las funcionalidades tienen entrada en el ledger de cubiertas, cuando el usuario pulsa Esc, o por condición excepcional.

## El ledger de funcionalidades cubiertas

`bug-analisis/funcionalidades-cubiertas.md` es la **fuente de verdad persistente del progreso**: lista las funcionalidades ya validadas para que el ciclo NO las vuelva a explorar (no repetir trabajo). Lo primero que hace la skill es leerlo y elegir la primera funcionalidad de `Docs/features/` sin entrada. Es estado mutable que el ciclo reescribe, por eso **NO se llama `CLAUDE.md`**: un `CLAUDE.md` en el repo está protegido por el hook `protect-claude-md` (regla §7 del root `CLAUDE.md`, inmutabilidad) y Edit/Write sobre él se deniegan — sería imposible que el ciclo lo actualizara. Vive en `bug-analisis/` (gitignored); para re-validar una funcionalidad, basta borrar su entrada. **Limitación aceptada**: una regresión dentro de una funcionalidad ya cubierta no se vuelve a detectar hasta que se borre su entrada del ledger.

## Dependencias (OBLIGATORIAS — no viajan dentro de este directorio)

La skill invoca 4 subagentes que **no** están en este directorio: viven en `.claude/agents/` (o `~/.claude/agents/`). Al copiar la skill hay que copiar también estos archivos:

| Subagente | Archivo | Rol |
|-----------|---------|-----|
| `bug-detector`   | `.claude/agents/bug-detector.md`   | FASE 1 — explora UNA funcionalidad; devuelve un bug nuevo, `FEATURE_CLEAN` o `FEATURE_BLOCKED` |
| `bug-validador`  | `.claude/agents/bug-validador.md`  | FASE 2 — re-confirma real vs falso positivo |
| `bug-correccion` | `.claude/agents/bug-correccion.md` | FASE 3 — aplica el fix mínimo (sin navegador, sin commits) |
| `bug-tester`     | `.claude/agents/bug-tester.md`     | FASE 4 — valida con el navegador, mueve el md al historial, commit local |

El frontmatter de `SKILL.md` restringe `allowed-tools: Agent(bug-detector, bug-validador, bug-correccion, bug-tester), …`; si falta cualquiera de los 4, el ciclo no puede completar una iteración.

Además, la verificación del navegador en la preparación inicial usa **`~/.claude/skills/navegador-amuelas11/ensure-chrome.ps1`** (ruta absoluta en `SKILL.md`). Esa skill provee el carril del navegador (perfil, puerto, MCP) — ver abajo.

## Requisitos del entorno

- App (frontend + backend) **corriendo en localhost** (frontend en **:5173**) antes de invocar.
- **Navegador de amuelas11 enganchado**: el MCP **`chrome-a11`** (Chrome real del perfil `C:\chrome-mcp-a11`, puerto 9223) debe estar activo en la sesión de Claude Code **antes** de lanzar el ciclo. Es el carril de la skill `/navegador-amuelas11`. Los 3 subagentes que navegan (detector, validador, tester) usan `mcp__chrome-a11__*` — **ya no Playwright**. Si el MCP no engancha, no se puede reconectar en caliente (haría falta reiniciar Claude Code con `claude --continue`); el ciclo lo verifica en la prep y, si falla, para avisando.
- **Dev login**: los subagentes entran a la app por `http://localhost:5173/login` pulsando el botón **"Dev login"**, que crea la sesión del usuario de prueba **amuelas14@gmail.com** con 4 cuentas de correo conectadas: `amuelas14@gmail.com`, `amuelas11`, `amuelas30`, `amuelas32`. El Dev login debe estar habilitado en el entorno (`VITE_DEV_AUTO_LOGIN` / `DEV_LOGIN_TRUSTED_HOSTS`).
- **Acciones con efecto real** permitidas SOLO sobre esas 4 cuentas de prueba; los envíos se dirigen únicamente entre ellas. El borrado definitivo de la app es no-op en el proveedor (recuperable re-sincronizando), así que es ejercitable sin daño irreversible.
- **Partes no ejercitables vía Dev login** (login real Google/Microsoft, conectar/desconectar cuentas por OAuth): el detector las marca como `salvedad:` "fuera de alcance del entorno" y cierra la funcionalidad como cubierta-parcial.
- **PowerShell** (los snippets del ciclo son PowerShell; `shell: powershell` en el frontmatter).
- Repo **git** con working tree limpio al arrancar.
- El ledger de bugs (`bug-analisis/`, 3 subdirectorios) y el ledger de funcionalidades (`bug-analisis/funcionalidades-cubiertas.md`) están **gitignored**; nunca se commitean.

## Notas de diseño no obvias (gotchas resueltos)

### 1. Navegador persistente y compartido (chrome-a11), no efímero (Playwright)
- El MCP `chrome-a11` controla un **único Chrome real persistente**, no un navegador aislado por subagente. Implicaciones de diseño:
  - La **sesión de Dev login sobrevive entre fases**: el validador/tester reutilizan la sesión que dejó el detector. Cada subagente entra por Dev login de forma **idempotente** (si ya hay sesión, la app va directa a la bandeja y no hay botón que pulsar).
  - **No se cierra el navegador** al terminar cada subagente (cerrarlo afectaría al Chrome real del usuario). A lo sumo se cierran pestañas extra con `close_page`, nunca la última.
  - El ciclo es secuencial (un subagente a la vez), así que no hay contención sobre el único puerto 9223.

### 2. Volcados `.md`/`.png` parásitos en la raíz (red de seguridad heredada)
- Con Playwright, `browser_take_screenshot` volcaba `.png` a la raíz y detector/validador a veces volcaban snapshots `.md`. El `take_screenshot` de chrome-devtools devuelve la imagen **inline**, así que el problema casi desaparece, pero se mantiene la red de seguridad:
  - *Prevención*: `bug-detector.md` y `bug-validador.md` prohíben volcar snapshots a disco fuera de `bug-analisis/`.
  - *Barrido*: `bug-tester.md` (pasos 3a.2 y 3a.6) y `SKILL.md` (prep paso 4) barren `.png` y `.md` con firma de snapshot (`[ref=e` / `- generic`) untracked en la raíz, sin tocar nada versionado ni en subdirectorios.

### 3. Enrutado del orquestador vs realidad del harness
- Las reglas de "matching estricto" de `SKILL.md` (igualdad exacta / `startswith`) son inaplicables tal cual: (a) los subagentes anteponen un preámbulo antes de su token; (b) la tool `Agent` añade una coletilla `...agentId: <id>` al final.
- Se resuelve enrutando por **presencia inequívoca del token** dentro del mensaje (`BUG_FOUND: `, `FEATURE_CLEAN: `, `FEATURE_BLOCKED: `, `REAL_BUG: `, …) y, para el éxito del tester, **verificando con git** (`git log -1`, `git show --stat HEAD`, `git status --porcelain`) en vez de comparar strings. Ver `SKILL.md` § Matching de cadenas.

## Hook `Stop` (parada del ciclo)

El frontmatter de `SKILL.md` define un hook `Stop` que **bloquea paradas espurias**: si el orquestador intenta parar sin una de las 7 causas legítimas, el hook re-inyecta la instrucción de continuar. Para parar de verdad hay que (a) alcanzar una de las 7 causas y (b) confirmar la parada en un segundo turno (el hook permite la 2ª parada consecutiva vía `stop_hook_active=true`). Las 7 causas (enumeradas en el hook y en `SKILL.md § Paradas`): parada manual, **todas las funcionalidades cubiertas**, `TESTER_COMMIT_FAILED`, working tree dirty inesperado, **navegador/app inalcanzable (`FEATURE_BLOCKED` / MCP chrome-a11 caído)**, contrato no cumplido, error de API/saturación. Nota: `FEATURE_CLEAN` (cierre de una funcionalidad) NO es parada — anota la funcionalidad y pasa a la siguiente.
