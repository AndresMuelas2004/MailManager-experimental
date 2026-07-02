import os
import subprocess
import sys

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
flag_path = os.path.join(project_dir, ".claude", ".tsc-check-needed")

if not os.path.exists(flag_path):
    sys.exit(0)

os.remove(flag_path)

frontend_dir = os.path.join(project_dir, "frontend")
result = subprocess.run(
    "npx tsc --project tsconfig.app.json",
    cwd=frontend_dir,
    shell=True,
    capture_output=True,
    text=True,
    encoding="utf-8",
)

if result.returncode != 0:
    output = (result.stdout + result.stderr).strip()
    print(f"TypeScript check failed:\n{output}", file=sys.stderr)
    sys.exit(2)

sys.exit(0)
