#!/usr/bin/env python3
"""PreToolUse hook: protect every CLAUDE.md in this repository from modification.

Registered in ``.claude/settings.json`` for the Edit|Write|Bash tools via the
thin wrapper ``protect-claude-md.sh`` (kept as ``.sh`` only because settings.json
references that filename).

Two protections:

* Edit / Write whose target is a ``CLAUDE.md`` file inside this git repository
  is denied. Targets outside the repo, or non-CLAUDE.md files, pass through.
* Bash commands that actually *modify* a ``CLAUDE.md`` file are denied. The check
  is TARGET-AWARE: a command that merely reads or mentions CLAUDE.md (e.g.
  ``grep foo CLAUDE.md > out.txt`` or ``cp CLAUDE.md backup.md``) is allowed;
  only commands whose redirection target / destructive operand is a CLAUDE.md
  are blocked. git commands are allowed (committing is fine; destructive git is
  handled by a separate hook).

Why target-aware: the previous implementation denied any Bash command that
contained the substring "CLAUDE.md" together with any write indicator
(``' > '``, ``'mv '``, ``'rm '`` ...) anywhere in the command, which produced
false positives on commands that only read CLAUDE.md and redirected the output
to a different file.

Conservative bias of a protective hook: the redirection scan does NOT strip
shell comments, so ``echo "... > CLAUDE.md"`` inside a comment is still denied.
Erring toward a (rare) false deny is preferred over letting a real write slip
through. Destructive-verb operand checks DO stop at an unquoted ``#`` so a
trailing comment that merely mentions CLAUDE.md after ``rm``/``cp``/... does not
trigger a false positive.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# A redirection target: >, >>, 1>, 2>, &>, &>>  optionally followed by spaces,
# capturing the destination path token.
_REDIRECT = re.compile(r"(?:\d+|&)?>>?\s*([^\s;|&<>]+)")

# Verbs that destroy / overwrite their file operand(s).
_DESTRUCTIVE_VERBS = {"rm", "del", "unlink", "shred", "truncate"}
_COPY_MOVE_VERBS = {"cp", "mv", "install"}

# Shell control operators that separate simple commands.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n|&]")


def _is_claude_md(token: str) -> bool:
    """True when *token* is a path whose final component is CLAUDE.md."""
    t = token.strip().strip("'\"")
    if not t:
        return False
    base = re.split(r"[\\/]", t)[-1]
    return base.lower() == "claude.md"


def _tokenize(segment: str) -> list[str]:
    """Whitespace-split a simple command, dropping a trailing shell comment."""
    tokens: list[str] = []
    for tok in segment.split():
        if tok.startswith("#"):
            break
        tokens.append(tok)
    return tokens


def _sed_in_place(args: list[str]) -> bool:
    return any(
        tok == "--in-place"
        or tok.startswith("--in-place=")
        or re.match(r"^-[a-zA-Z]*i", tok)
        for tok in args
    )


def _segment_modifies_claude_md(tokens: list[str]) -> bool:
    if not tokens:
        return False
    verb = tokens[0].lstrip("!").split("/")[-1]
    args = tokens[1:]
    operands = [a for a in args if not a.startswith("-")]

    if verb == "git":
        return False  # git operations are allowed (handled by another hook)
    if verb in _DESTRUCTIVE_VERBS:
        return any(_is_claude_md(a) for a in operands)
    if verb == "tee":
        return any(_is_claude_md(a) for a in operands)
    if verb == "sed":
        return _sed_in_place(args) and any(_is_claude_md(a) for a in operands)
    if verb == "dd":
        return any(a.startswith("of=") and _is_claude_md(a[3:]) for a in args)
    if verb in _COPY_MOVE_VERBS:
        if not operands:
            return False
        if _is_claude_md(operands[-1]):  # overwriting CLAUDE.md (destination)
            return True
        if verb == "mv":  # renaming CLAUDE.md away removes it
            return any(_is_claude_md(s) for s in operands[:-1])
    return False


def _command_modifies_claude_md(command: str) -> bool:
    # 1) Output redirection into a CLAUDE.md file (scanned over the whole command).
    for match in _REDIRECT.finditer(command):
        if _is_claude_md(match.group(1)):
            return True
    # 2) Per simple-command write/destroy verbs whose target is a CLAUDE.md.
    for segment in _SEGMENT_SPLIT.split(command):
        if _segment_modifies_claude_md(_tokenize(segment)):
            return True
    return False


def _repo_root() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    return out.decode().strip().replace("\\", "/")


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        if command and _command_modifies_claude_md(command):
            _deny(
                "Blocked: this Bash command would modify a CLAUDE.md file, which "
                "is protected in this project. Reading or copying FROM CLAUDE.md "
                "is fine; writing to, deleting, or overwriting it is not."
            )
        return 0

    if tool_name in ("Edit", "Write"):
        file_path = str(tool_input.get("file_path") or "").replace("\\", "/")
        base = file_path.rstrip("/").split("/")[-1]
        if base.lower() != "claude.md":
            return 0
        repo_root = _repo_root()
        if repo_root:
            root_norm = repo_root.rstrip("/").lower() + "/"
            if not file_path.lower().startswith(root_norm):
                return 0  # CLAUDE.md outside this repository — not our concern
        _deny(
            "All CLAUDE.md files in this project are protected and must not be "
            "modified directly. Edit the corresponding *_guide.md instead."
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
