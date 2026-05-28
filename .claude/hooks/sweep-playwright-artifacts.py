"""Safety net: relocate Playwright-shaped artefacts from the repo root to .playwright-mcp/.

The PreToolUse hook handles `browser_take_screenshot` and `browser_pdf_save`.
Two tools sidestep it because they execute arbitrary Playwright code:

    mcp__playwright__browser_evaluate
    mcp__playwright__browser_run_code_unsafe

Code passed to either can call `page.screenshot({ path: 'foo.png' })`, which
writes to <cwd>/foo.png — i.e. the repo root — without going through the
MCP's `outputFile` / `workspaceFile` plumbing.

This PostToolUse hook scans the repo root for binary artefacts that appeared
in the last 60 seconds and moves them under .playwright-mcp/. It does NOT
touch files modified earlier (so pre-existing repo assets stay put) and it
restricts itself to a strict extension allow-list, so legitimate `.yml`
configs (compose.yml, etc.) are never relocated.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MOVABLE_EXTENSIONS = {".png", ".jpeg", ".jpg", ".webp", ".pdf"}
TARGET_DIR_NAME = ".playwright-mcp"
RECENCY_SECONDS = 60.0
PLAYWRIGHT_TOOL_PREFIX = "mcp__playwright__"


def _project_root() -> Path | None:
    raw = os.environ.get("CLAUDE_PROJECT_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if root.is_dir() else None


def _resolve_target_path(target_dir: Path, candidate_name: str) -> Path:
    target = target_dir / candidate_name
    if not target.exists():
        return target
    stem, _, suffix = candidate_name.rpartition(".")
    stem = stem or candidate_name
    suffix = f".{suffix}" if suffix else ""
    counter = 1
    while True:
        alt = target_dir / f"{stem}-{counter}{suffix}"
        if not alt.exists():
            return alt
        counter += 1


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = payload.get("tool_name") or ""
    if not tool_name.startswith(PLAYWRIGHT_TOOL_PREFIX):
        return 0

    root = _project_root()
    if root is None:
        return 0

    target_dir = root / TARGET_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)

    cutoff = time.time() - RECENCY_SECONDS
    relocated: list[str] = []

    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in MOVABLE_EXTENSIONS:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue

        destination = _resolve_target_path(target_dir, entry.name)
        try:
            shutil.move(str(entry), str(destination))
        except OSError as exc:
            sys.stderr.write(
                f"[sweep-playwright-artifacts] could not move {entry.name}: {exc}\n"
            )
            continue
        relocated.append(f"{entry.name} -> {destination.relative_to(root)}")

    if relocated:
        sys.stderr.write(
            "[sweep-playwright-artifacts] relocated: " + "; ".join(relocated) + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
