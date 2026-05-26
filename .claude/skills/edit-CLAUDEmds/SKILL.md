---
description: Procedimiento manual para editar cualquier CLAUDE.md del repositorio saltándose el hook de protección. SOLO debe ejecutarse cuando el usuario invoca explícitamente /edit-CLAUDEmds. Desactiva el hook protect-claude-md.sh en .claude/settings.json, realiza los cambios solicitados en los CLAUDE.md, y restaura el hook exactamente como estaba.
disable-model-invocation: true
---

# edit-CLAUDEmds — Editar CLAUDE.md saltándose el hook de protección

El root `CLAUDE.md` declara en § 7 que todos los `CLAUDE.md` del repo son inmutables y protegidos por hook. Esta skill es la **única excepción autorizada por el usuario**: el usuario pide explícitamente editar uno o varios `CLAUDE.md`, y se realiza el cambio con un procedimiento controlado de desactivar/reactivar el hook.

## Cuándo aplica

- El usuario invoca `/edit-CLAUDEmds` y describe qué cambio quiere en qué archivo(s) `CLAUDE.md`.

## Procedimiento — pasos en orden estricto

### Paso 1 — Identificar el cambio

Confirmar con el usuario (si hay ambigüedad) qué archivo(s) `CLAUDE.md` se van a tocar y qué edición concreta se va a hacer. El procedimiento es el mismo tanto si es un solo archivo como si son varios: el hook se desactiva una vez, se hacen todas las ediciones, y se reactiva al final.

### Paso 2 — Desactivar el hook `protect-claude-md.sh` en `.claude/settings.json`

Editar `C:\Users\amuel\Desktop\proyectos\MailManager-experimental\.claude\settings.json` y eliminar el primer entry del array `hooks.PreToolUse` (el que ejecuta `protect-claude-md.sh`). El resto del archivo NO se toca.

**Bloque exacto a eliminar** (incluida la coma que lo separa del siguiente entry):

```json
      {
        "matcher": "Edit|Write|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-claude-md.sh"
          }
        ]
      },
```

Usar la herramienta Edit con `old_string`/`new_string` exactos para que la modificación sea precisa y no toque ningún otro hook del archivo.

### Paso 3 — Realizar las ediciones en los `CLAUDE.md`

Aplicar los cambios pedidos por el usuario con Edit (preferido) o Write. Cualquier `CLAUDE.md` del proyecto es editable mientras el hook esté desactivado.

Reglas durante este paso:

- Tocar **solo** lo que el usuario ha pedido. No aprovechar para reescribir secciones contiguas ni reformatear.
- Si la edición afecta a la estructura (numeración de secciones, referencias internas), avisar antes de aplicar y confirmar.
- No editar `*_guide.md` ni otros `.md` desde esta skill: para esos no hace falta el procedimiento del hook; se editan directamente.

### Paso 4 — Restaurar el hook en `.claude/settings.json`

Volver a insertar el entry eliminado en el Paso 2, en la **misma posición** (primer entry de `PreToolUse`, antes del de `protect-settings-local.py`).

**Estado final esperado** del bloque `hooks.PreToolUse` (idéntico al estado original):

```json
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-claude-md.sh"
          }
        ]
      }
    ],
```
**No tocar el resto de hooks que no sean el mencionado**
### Paso 5 — Verificación final

1. Releer la sección `hooks.PreToolUse` de `.claude/settings.json` para confirmar que el entry de `protect-claude-md.sh` está de vuelta en la primera posición y que el matcher es exactamente `"Edit|Write|Bash"`.
2. Resumir al usuario qué se editó en cada `CLAUDE.md` y confirmar que el hook está restaurado.

## Reglas inquebrantables

- **Nunca dejar el hook desactivado al terminar.** Si algo falla a mitad de proceso (la edición del `CLAUDE.md` se cancela, hay un conflicto, el usuario interrumpe), el Paso 4 debe ejecutarse igualmente antes de cerrar la skill.
- **No tocar otros hooks** del archivo `settings.json`. Solo el de `protect-claude-md.sh`. Los demás  se quedan tal cual.
- **No usar `replace_all: true`** en la edición de `settings.json`: el bloque a quitar y restaurar es único, pero la regla evita sorpresas si el archivo crece en el futuro.
- **No usar `git stash` ni operaciones destructivas** para “limpiar” cambios fallidos: revertir las ediciones a mano con Edit.
- **Ante cualquier duda sobre lo que hacer no dudar en decírmelo e interrumpir el proceso** 