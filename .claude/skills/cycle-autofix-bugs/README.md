# cycle-autofix-bugs — dependencias, requisitos y notas de diseño

`SKILL.md` orquesta un ciclo autónomo **detector → validador → corrección → tester → commit**. Este README documenta **todo lo que la skill necesita para funcionar** y las decisiones de diseño no obvias, para que sea portable: si copias la skill a otra máquina o se la pasas a alguien, aquí está el panorama completo (la lógica operativa vive en `SKILL.md`; no se duplica aquí).

## Dependencias (OBLIGATORIAS — no viajan dentro de este directorio)

La skill invoca 4 subagentes que **no** están en este directorio: viven en `.claude/agents/` (o `~/.claude/agents/`). Al copiar la skill hay que copiar también estos archivos:

| Subagente | Archivo | Rol |
|-----------|---------|-----|
| `bug-detector`   | `.claude/agents/bug-detector.md`   | FASE 1 — encuentra UN bug nuevo navegando con Playwright |
| `bug-validador`  | `.claude/agents/bug-validador.md`  | FASE 2 — re-confirma real vs falso positivo |
| `bug-correccion` | `.claude/agents/bug-correccion.md` | FASE 3 — aplica el fix mínimo (sin Playwright, sin commits) |
| `bug-tester`     | `.claude/agents/bug-tester.md`     | FASE 4 — valida con Playwright, mueve el md al historial, commit local |

El frontmatter de `SKILL.md` ya restringe `allowed-tools: Agent(bug-detector, bug-validador, bug-correccion, bug-tester)`; si falta cualquiera de los 4, el ciclo no puede completar una iteración.

## Requisitos del entorno

- App (frontend + backend) **corriendo en localhost** antes de invocar.
- **MCP de Playwright** alcanzable (3 de los 4 subagentes navegan). Si está caído, detector/tester lo reportan y el ciclo para (causa 5).
- **PowerShell** (los snippets del ciclo son PowerShell; `shell: powershell` en el frontmatter).
- Repo **git** con working tree limpio al arrancar.
- El ledger vive en `bug-analisis/` (3 subdirectorios) y está **gitignored**; nunca se commitea.

## Notas de diseño no obvias (gotchas resueltos)

Estos dos problemas se descubrieron ejecutando el ciclo. **Antes vivían como notas sueltas en la auto-memory del proyecto (`~/.claude/projects/<proyecto>/memory/MEMORY.md`); se han plegado a la propia skill y a los subagentes** para que el comportamiento viaje con ellos — la `MEMORY.md` no es portable, es del proyecto/máquina, y un comportamiento determinista que un agente debe ejecutar siempre pertenece a su system prompt, no a una memoria truncable.

### 1. Volcados `.md` parásitos de Playwright en la raíz
- **Síntoma**: aparecían `.md` untracked en la raíz (p. ej. `drafts.md`, `accounts-page.md`) con la primera línea en formato de árbol de accesibilidad (`- generic [ref=e2]:`). Ensucian el working tree y disparan falsamente las paradas por "árbol sucio" (prep paso 5; condición excepcional #3).
- **Causa**: solo `bug-detector` y `bug-validador` tienen tool `Write` y volcaban snapshots como scratch a la raíz. (`bug-tester` no tiene `Write`: no puede crearlos, pero sí barrerlos vía PowerShell.)
- **Dónde se resuelve ahora**:
  - *Prevención (causa)*: restricción en `bug-detector.md` y `bug-validador.md` — prohibido volcar snapshots a disco fuera de `bug-analisis/`.
  - *Red de seguridad*: `bug-tester.md` pasos 3a.2 y 3a.6 barren `.md` con firma de snapshot además de `.png`.
  - *Auto-cura al arrancar*: `SKILL.md` prep paso 4 hace el mismo barrido (limpia residuos de sesiones antiguas).
  - El barrido de `.md` borra **solo** untracked en la raíz cuya primera línea tenga firma de snapshot (`[ref=e` o `- generic`); nunca toca otros `.md`, ni rutas versionadas, ni subdirectorios, ni `bug-analisis/`.

### 2. Enrutado del orquestador vs realidad del harness
- **Síntoma**: las reglas de "matching estricto" de `SKILL.md` (igualdad exacta / `startswith` del mensaje completo) son inaplicables tal cual.
- **Causa**: (a) los subagentes anteponen un preámbulo antes de su token de contrato; (b) la tool `Agent` **siempre** añade una coletilla `...agentId: <id> (use SendMessage with to: '<id>' to continue this agent)` al final → la igualdad literal es imposible.
- **Dónde se resuelve ahora**: `SKILL.md` § Matching de cadenas, bloque "Realidad del harness" — enrutar por **presencia inequívoca del token** dentro del mensaje y, para el éxito del tester, **verificar con git** (`git log -1`, `git show --stat HEAD`, `git status --porcelain`) en vez de comparar strings.

## Hook `Stop` (parada del ciclo)

El frontmatter de `SKILL.md` define un hook `Stop` que **bloquea paradas espurias**: si el orquestador intenta parar sin una de las 7 causas legítimas, el hook re-inyecta la instrucción de continuar. Para parar de verdad hay que (a) alcanzar una de las 7 causas y (b) confirmar la parada en un segundo turno (el hook permite la 2ª parada consecutiva vía `stop_hook_active=true`). Las 7 causas están enumeradas en el hook y en `SKILL.md § Paradas`.
