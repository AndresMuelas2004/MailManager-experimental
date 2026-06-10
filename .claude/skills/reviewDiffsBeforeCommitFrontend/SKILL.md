---
name: reviewDiffsBeforeCommitFrontend
description: "Pre-commit frontend architecture review: launches one background architecture-compliance-reviewer subagent over the modified frontend files, waits in silence, and relays its report. NEVER invoke this skill on your own initiative — it runs only when the user invokes /reviewDiffsBeforeCommitFrontend directly or as part of the /reviewDiffsBeforeCommitAll orchestrator."
---

Pre-commit review of the **frontend** side. A single `architecture-compliance-reviewer` subagent audits the modified frontend files against the frontend documentation hierarchy. This skill does no inline review itself — it launches the reviewer in the background, waits in silence, and relays the report.

Usage: `/reviewDiffsBeforeCommitFrontend`

---

## Step 1 — Detect frontend diffs

Run in parallel via Bash:
- `git diff --cached --name-only -- frontend/`
- `git diff --name-only -- frontend/`

Combine both lists and deduplicate. If the combined list is empty → inform the user "No frontend diffs found (staged or unstaged). Nothing to review." and STOP without launching anything.

## Step 2 — Launch the architecture reviewer (background)

Launch exactly ONE agent:

- `subagent_type`: `architecture-compliance-reviewer`
- `description`: `Frontend architecture compliance review`
- `run_in_background`: `true`
- `prompt` (substitute `<path N>` with the actual resolved frontend paths from Step 1):

```
Audit architectural compliance of the following modified frontend files against the documentation hierarchy.

Scope (resolved paths, all under `frontend/`):
- <path 1>
- <path 2>
- ...

Documentation hierarchy note for the frontend: unlike the backend layers, the nested frontend directories carry NO per-directory `*_guide.md`. The hierarchy is: the repository root docs (root `CLAUDE.md` and the files it imports), `frontend/CLAUDE.md`, the nested `frontend/**/CLAUDE.md` files (e.g. `frontend/src/api/CLAUDE.md`, `frontend/src/features/CLAUDE.md`), and a single layer guide `frontend/frontend_guide.md` at the frontend root — it is not referenced by any frontend `CLAUDE.md`, but it is the frontend's project-specific guide and counts at `*_guide.md` priority.

Goal: verify that EVERY architectural rule declared by that hierarchy is fully respected by these files. Follow the phased workflow defined in your own agent definition strictly — do not skip Phase 2 (Documentation Hierarchy) even if you think you already know the project, because rules may have changed since your last run.

Produce the full report structure defined in your agent definition (sections 1 through 10), with the verdict at section 9.

This is a read-only audit: you must not modify any file. Do not propose edits to any `CLAUDE.md` (they are immutable per root `CLAUDE.md` § 7) — redirect every suggestion either to the code or to `frontend/frontend_guide.md`.
```

Print a one-line note that the reviewer was launched.

## Step 3 — Wait in silence

After launching, STOP. Do NOT call any tool, do NOT poll, and do NOT do any other work in the main conversation. Wait for the agent's automatic completion notification.

## Step 4 — Relay the report

Present the reviewer's full report verbatim (sections 1 through 10), then close with one mapped verdict line:

- Verdict **NON-COMPLIANT**, or at least one **BLOCKER** finding → **BLOCK COMMIT**
- Verdict **MOSTLY COMPLIANT** or **CANNOT ASSESS** → **REVIEW BEFORE COMMIT**
- Verdict **COMPLIANT** → **SAFE TO COMMIT**

If the reviewer crashes or returns nothing, report that explicitly; the verdict is then at least **REVIEW BEFORE COMMIT**.

## Important rules

- Read-only: never modify any file.
- If the reviewer's report suggests editing any `CLAUDE.md`, rewrite that suggestion before presenting the report so it points at `frontend/frontend_guide.md` or at the code instead (root `CLAUDE.md` § 7).
