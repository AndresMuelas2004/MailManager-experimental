"""Force Playwright MCP screenshots/PDFs into <repo>/.playwright-mcp/.

The Playwright MCP resolves an explicit `filename` against `clientWorkspace`
(= the process cwd, which is the repo root in Claude Code), NOT against the
configured `outputDir`. A bare `filename="x.png"` therefore lands in the repo
root and the MCP's own guardrail permits it because the root is the workspace.

This PreToolUse hook rewrites `tool_input.filename` via `updatedInput` so it
always begins with `.playwright-mcp/` and contains no path traversal segments.
If the input is already safe (empty / unset / already prefixed), the hook
returns silently and the original input passes through.

Targets:
    mcp__playwright__browser_take_screenshot
    mcp__playwright__browser_pdf_save
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath, PureWindowsPath

TARGET_TOOLS = {
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_pdf_save",
}
SAFE_PREFIX = ".playwright-mcp/"
_INVALID_CHAR = re.compile(r'[<>:"|?*\x00-\x1f]')


def _is_absolute(name: str) -> bool:
    return PureWindowsPath(name).is_absolute() or PurePosixPath(name).is_absolute()


def _safe_basename(name: str) -> str:
    base = PureWindowsPath(name).name or PurePosixPath(name).name
    base = _INVALID_CHAR.sub("_", base).strip(" .")
    return base or "screenshot.png"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if payload.get("tool_name") not in TARGET_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or {}
    filename = tool_input.get("filename")
    if not filename:
        return 0

    normalised = filename.replace("\\", "/")
    if (
        normalised.startswith(SAFE_PREFIX)
        and ".." not in PurePosixPath(normalised).parts
        and not _is_absolute(filename)
    ):
        return 0

    rewritten = SAFE_PREFIX + _safe_basename(filename)
    new_input = {**tool_input, "filename": rewritten}

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    f"Rewrote filename '{filename}' -> '{rewritten}' so the "
                    f"output lands under .playwright-mcp/"
                ),
                "updatedInput": new_input,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
