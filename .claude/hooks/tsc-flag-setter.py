import json
import os
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
frontend_dir = os.path.normcase(os.path.join(project_dir, "frontend"))
file_path_norm = os.path.normcase(file_path)

if file_path_norm.startswith(frontend_dir + os.sep) or file_path_norm == frontend_dir:
    flag_path = os.path.join(project_dir, ".claude", ".tsc-check-needed")
    if not os.path.exists(flag_path):
        open(flag_path, "w").close()

sys.exit(0)
