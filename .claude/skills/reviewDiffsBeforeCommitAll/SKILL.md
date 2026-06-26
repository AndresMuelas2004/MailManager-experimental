---
name: reviewDiffsBeforeCommitAll
description: "Full pre-commit review orchestrator: runs the two child skills /reviewDiffsBeforeCommitBackend and /reviewDiffsBeforeCommitFrontend concurrently (all their reviewers run as background subagents launched in one go), waits in silence, then closes with a brief executive summary and a single overall verdict. NEVER invoke this skill on your own initiative — it runs only when the user invokes /reviewDiffsBeforeCommitAll directly, or when another skill or resource explicitly invokes it."
---

Orchestrates the two child pre-commit skills so backend and frontend are reviewed concurrently. All heavy work runs in background subagents launched by the children; this skill only coordinates the combined launch, waits in silence, and merges the verdicts at the end.

Usage: `/reviewDiffsBeforeCommitAll` (same optional `$ARGUMENTS` as the backend child: `--tests dir1 dir2 ...` and `--md path1 path2 ...` — passed through to the backend child only; the frontend child takes no arguments).

---

## Workflow

### 1 — Load both child skills

Invoke the Skill tool for `reviewDiffsBeforeCommitBackend` (forwarding any `--tests` / `--md` from `$ARGUMENTS`) and for `reviewDiffsBeforeCommitFrontend`. This loads their instructions into the conversation; the steps below only coordinate how they execute together — each child's own workflow is the source of truth for its side.

### 2 — Diff analysis (both sides, no agents yet)

Execute the backend child's PHASE 1 and the frontend child's Step 1, but do NOT launch any agent yet — hold both launches for the single combined message of step 3.

- If one side has no diffs, that child stops by itself; continue with the other side alone.
- If neither side has diffs, report "No backend or frontend files to review." and STOP.
- If the diffs contain files outside `backend/` and `frontend/`, set them aside: nobody reviews them and they must be listed in the final summary.

### 3 — Single combined launch

Launch ALL background agents from both children in ONE single message: the backend child's entire fleet (its PHASE 2, including its architecture-compliance-reviewer) plus the frontend child's architecture-compliance-reviewer (its Step 2). This combined launch is the only deviation from running each child standalone — it exists so no agent waits on another.

Print one brief message listing everything launched.

### 4 — Wait in silence

STOP. Do NOT call any tool, do NOT poll, and do NOT do any other work in the main conversation. Wait for the automatic completion notifications of every launched agent.

### 5 — Consolidate

Only when ALL launched agents have reported back:

1. Produce the backend child's consolidated report exactly as its PHASE 3 defines.
2. Produce the frontend child's report exactly as its Step 4 defines.
3. Close with:

```
## Executive Summary (Backend + Frontend)
- Backend verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT / skipped (no backend diffs)
- Frontend verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT / skipped (no frontend diffs)
- Out-of-scope files (reviewed by neither side): <paths or "none">
- Overall verdict: <worst verdict among the sides that ran>
```

Severity order: BLOCK COMMIT > REVIEW BEFORE COMMIT > SAFE TO COMMIT. Do not re-print the children's full reports inside the summary.

## Important rules

- This skill and its children are read-only: never modify any file.
- If any child phase or any agent crashes or returns nothing, report it explicitly in the Executive Summary and still present whatever the others produced; a failed reviewer downgrades the overall verdict to at least **REVIEW BEFORE COMMIT**.
