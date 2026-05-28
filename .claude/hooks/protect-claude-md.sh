#!/bin/bash
# PreToolUse hook entry point. The real, target-aware logic lives in the sibling
# Python file; this wrapper exists only because .claude/settings.json registers
# the .sh filename. The hook payload on stdin is inherited by the Python process
# through exec, so no piping is needed.
exec python "$(dirname "$0")/protect-claude-md.py"
