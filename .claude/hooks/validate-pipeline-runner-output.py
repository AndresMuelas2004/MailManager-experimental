"""
SubagentStop hook for the `pipeline-skill-runner` agent
(pipeline /implementar-feature-completa).

Deterministic validation of the runner's final message, read from the
`last_assistant_message` field of the SubagentStop stdin input. The
stop passes untouched when the message matches ANY format of the
pipeline protocol:

- The single-line replies: the per-phase `OK | ...` lines,
  `FALLO: <motivo>`, `BLOQUEO: <motivo>`.
- The multi-line phase-1 blocks: `PLAN-COMPLETADO` (header + `slug:` +
  `dir:` lines) and `PREGUNTAS-PENDIENTES` (structural check: header
  line plus the `id:` / `pregunta:` / `opciones:` field markers the
  orchestrator's relay needs).

Anything else gets `decision: block` with a generic reason forcing the
runner to re-emit its final message in the exact protocol format.

Fail-open by design: if the stdin payload cannot be parsed, or the
`last_assistant_message` field is absent or not a string (e.g. a CLI
update changed the SubagentStop input schema — neither this field nor
`stop_hook_active` appears in the current official hooks doc; both are
relied on because they work empirically on this machine), the stop
passes through with NO validation. A schema change must degrade to
"no format enforcement", never to a block loop.

Anti-loop: if the hook already blocked once (stop_hook_active truthy),
the retry passes through unchanged. Worst case we accept an imperfect
final message.
"""

from __future__ import annotations

import json
import re
import sys

# Ensure stdout carries UTF-8 on Windows (default is cp1252 and would
# mangle special characters in `reason`), and decode stdin as UTF-8 too
# ("utf-8-sig" additionally swallows a BOM if the caller prepends one).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8-sig")
except Exception:
    pass


# Single-line protocol formats, full-match.
_SINGLE_LINE_FORMATS = [
    r"OK \| resumen: .+",  # fase 2
    r"OK \| sin-diffs",  # fase 3, working tree sin cambios
    r"OK \| informe: .+ \| overall: (BLOCK COMMIT|REVIEW BEFORE COMMIT|SAFE TO COMMIT) \| validables: \d+ \| reviewers-caidos: \d+",  # fase 3
    r"OK \| validacion: .+ \| necesarias: \d+ \| condicionales: \d+ \| falsos-positivos: \d+",  # fase 4
    r"OK \| aplicadas: \d+ \| descartadas: \d+ \| necesarias-sin-aplicar: \d+ \| detalle: .+ \| decisiones: .+",  # fase 5
    r"OK \| commits: \d+ \| push: (ok|fallo) \| detalle: .+",  # fase 6
    r"(FALLO|BLOQUEO): .+",  # fallo/bloqueo de cualquier fase
]

_SINGLE_LINE_RE = re.compile(
    "^(?:" + "|".join(f"(?:{p})" for p in _SINGLE_LINE_FORMATS) + ")$"
)

# PLAN-COMPLETADO: exactly three lines (`.` does not match newlines, so
# each `.+` is confined to its own line).
_PLAN_COMPLETADO_RE = re.compile(r"PLAN-COMPLETADO\nslug: .+\ndir: .+")


def _is_valid_multiline_block(candidate: str) -> bool:
    if _PLAN_COMPLETADO_RE.fullmatch(candidate):
        return True
    if candidate.startswith("PREGUNTAS-PENDIENTES\n"):
        # Structural check only: the inner text is free-form, so just
        # require the field markers the orchestrator's relay needs to
        # build its AskUserQuestion calls.
        lines = candidate.splitlines()
        return all(
            any(line.startswith(marker) for line in lines)
            for marker in ("id: ", "pregunta: ", "opciones:")
        )
    return False


SELF_REVIEW_REASON = (
    "Antes de cerrar, auto-revisa tu mensaje final contra el protocolo del pipeline: "
    "debe ser EXACTAMENTE el formato corto que definen la skill que acabas de ejecutar "
    "o tu task prompt (una linea 'OK | ...' con sus campos exactos, 'BLOQUEO: <motivo>', "
    "'FALLO: <motivo>', o uno de los bloques 'PREGUNTAS-PENDIENTES' / 'PLAN-COMPLETADO' "
    "con su estructura literal). Nada mas: sin preambulos, sin resumen del trabajo "
    "realizado, sin lista de archivos tocados, sin despedidas; todo el detalle pertenece "
    "a los .md persistidos, nunca a tu respuesta. Reemite ahora unicamente el mensaje "
    "en el formato correcto."
)


def main() -> None:
    try:
        # lstrip("\ufeff"): tolerate a UTF-8 BOM in case the harness or
        # shell prepends one (observed with PowerShell 5.1 pipes).
        payload = json.loads(sys.stdin.read().lstrip("\ufeff"))
    except Exception:
        # If we cannot parse the input, let the stop proceed.
        sys.exit(0)

    # Anti-loop: only force a review once. If we already blocked, accept
    # whatever the runner produced on the retry, even if still imperfect.
    if payload.get("stop_hook_active"):
        sys.exit(0)

    last_message = payload.get("last_assistant_message")
    if not isinstance(last_message, str):
        # Fail open: unknown input schema. Losing format enforcement is
        # acceptable; blocking blind (and risking a block loop) is not.
        sys.exit(0)

    candidate = last_message.strip()
    if _SINGLE_LINE_RE.fullmatch(candidate) or _is_valid_multiline_block(candidate):
        sys.exit(0)

    print(json.dumps({"decision": "block", "reason": SELF_REVIEW_REASON}))
    sys.exit(0)


if __name__ == "__main__":
    main()
