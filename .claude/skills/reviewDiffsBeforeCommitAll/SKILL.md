---
name: reviewDiffsBeforeCommitAll
description: "Full pre-commit review orchestrator: runs the two child skills /reviewDiffsBeforeCommitBackend and /reviewDiffsBeforeCommitFrontend concurrently (all their reviewers run as foreground subagents launched concurrently in one go and returning their results to this fork's own execution), consolidates them without yielding its turn, persists the full consolidated report to a .md file and replies with a single short line. NEVER invoke this skill on your own initiative — it runs only when the user invokes /reviewDiffsBeforeCommitAll directly, or when another skill or resource explicitly invokes it."
argument-hint: "[opcional] flags del hijo backend (--tests dir1 ... / --md path1 ...), '--out <ruta.md>' con el destino del informe persistido y/o '--informe <ruta.md>' con el informe final del pipeline al que añadir la sección de subagentes lanzados/caídos"
context: fork
agent: pipeline-skill-runner
---

Orchestrates the two child pre-commit skills so backend and frontend are reviewed concurrently. All heavy work runs in foreground subagents launched by the children; this skill only coordinates the combined launch, waits for their results within its own execution (never yielding its turn, because a fork is not re-woken after ceding its turn), merges the verdicts, **persists the full consolidated report to a `.md` file and replies with a single short line** (this skill runs in an isolated forked context: its response is data for the caller, and the report's value lives in the persisted file).

Usage: `/reviewDiffsBeforeCommitAll` (same optional `$ARGUMENTS` as the backend child: `--tests dir1 dir2 ...` and `--md path1 path2 ...` — passed through to the backend child only; the frontend child takes no arguments). Additionally accepts `--out <path.md>`: the destination file for the persisted consolidated report (not forwarded to any child). Also accepts `--informe <path.md>` (typically passed by the /implementar-feature-completa pipeline): the pipeline's user-facing final report, to which this skill appends its launched-subagents section (step 5.5); without it, that section is simply not written anywhere.

---

## Workflow

### 1 — Load both child skills

Invoke the Skill tool for `reviewDiffsBeforeCommitBackend` (forwarding any `--tests` / `--md` from `$ARGUMENTS`) and for `reviewDiffsBeforeCommitFrontend`. This loads their instructions into the conversation; the steps below only coordinate how they execute together — each child's own workflow is the source of truth for its side.

### 2 — Diff analysis (both sides, no agents yet)

Execute the backend child's PHASE 1 and the frontend child's Step 1, but do NOT launch any agent yet — hold both launches for the single combined message of step 3.

- If one side has no diffs, that child stops by itself; continue with the other side alone.
- If neither side has diffs, reply with exactly `OK | sin-diffs` (nothing else, no report file) and STOP.
- If the diffs contain files outside `backend/` and `frontend/`, set them aside: nobody reviews them and they must be listed in the final summary.

### 3 — Single combined launch

Launch ALL agents from both children **in the foreground** (`run_in_background: false`) in ONE single message — they still run concurrently, but their results return to THIS fork's own execution instead of firing background notifications (a fork that cedes its turn is never re-woken to consolidate, which is the bug this fixes): the backend child's entire fleet (its PHASE 2, including its architecture-compliance-reviewer) plus the frontend child's architecture-compliance-reviewer (its Step 2). This combined launch is the only deviation from running each child standalone — it exists so no agent waits on another.

Print one brief message listing everything launched.

Keep your own launch roster — for every agent launched: agent type, side (backend/frontend), and a one-line objective (what it reviews). You will need it in step 5 for the `reviewers-caidos` count and the `--informe` section. If a child skill crashes before launching its agents, its whole side counts as ONE crashed entry in the roster ("review <side> — no llegó a lanzarse").

### 4 — Receive every result inside this fork

Because the agents were launched in the foreground, the harness returns all of their results to THIS fork's own execution as the results of your Agent calls. Do NOT yield your turn, do NOT poll, and do NOT wait for external completion notifications — a fork is not re-woken after ceding its turn (that background-and-wait pattern works only in the main conversation, which is exactly the bug this replaces). As soon as every launched agent's result is in hand, proceed to step 5. Never emit an intermediate "still waiting" message: your only turn-ending output is the final line of step 5.

### 5 — Consolidate and persist

With every launched agent's result already returned to your execution:

1. Resolve the destination file: the `--out <path.md>` argument if provided; otherwise `nueva-implementacion-en-curso/review-suelta-<YYYYMMDD-HHmmss>.md` at the repo root (create the directory if missing).
2. Write to that file — with the Write tool, as one single document, never printed to the conversation — in this order:
   - The backend child's consolidated report exactly as its PHASE 3 defines (or a one-line "skipped (no backend diffs)" note).
   - The frontend child's report exactly as its Step 4 defines (or a one-line "skipped (no frontend diffs)" note).
   - The closing block:

```
## Executive Summary (Backend + Frontend)
- Backend verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT / skipped (no backend diffs)
- Frontend verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT / skipped (no frontend diffs)
- Out-of-scope files (reviewed by neither side): <paths or "none">
- Overall verdict: <worst verdict among the sides that ran>
```

Severity order: BLOCK COMMIT > REVIEW BEFORE COMMIT > SAFE TO COMMIT. Do not re-print the children's full reports inside the summary.

3. Count `validables` — the findings that feed the downstream validation stage: every **Group A** finding of the backend report plus every finding of the two **architecture reports** with severity Blocker, Major or Minor. Group B findings, Suggestions and nits are excluded (Group B is pre-existing code and never blocks; suggestions are optional).
4. Count `reviewers-caidos` — from your step-3 roster: every launched agent that crashed or returned nothing, plus one entry per child skill that crashed before launching (agents that completed normally count 0 here, however severe their findings).
5. If `--informe <path.md>` was provided: APPEND to that file — never read it, never rewrite it; use an append write (PowerShell `Add-Content -Encoding utf8` / Bash `cat >>`) — this section, written IN SPANISH:

```
## Fase 3 — Review: subagentes lanzados

| Subagente | Lado | Objetivo | Estado |
|---|---|---|---|
| <agent type> | backend/frontend | <qué revisaba, una línea> | OK / CRASHEADO — <motivo corto> |
```

One row per roster entry. If none crashed, add under the table the line `Los <N> reviewers completaron sin fallos.`

6. Reply with EXACTLY one line and nothing else — your response is data for the caller; the full report lives in the file:

`OK | informe: <absolute path> | overall: <BLOCK COMMIT|REVIEW BEFORE COMMIT|SAFE TO COMMIT> | validables: <n> | reviewers-caidos: <m>`

If a child phase or an agent crashed (rule below), the report file must state it in its Executive Summary and the verdict downgrades as defined — the one-line reply format does not change.

## Important rules

- This skill and its children are read-only **with respect to the repository**: never modify any project file. The ONLY writes this skill performs are its own report file (the `--out` path or the default under `nueva-implementacion-en-curso/`) and, when `--informe` is given, appending its subagents section to that file.
- If any child phase or any agent crashes or returns nothing, state it explicitly in the persisted Executive Summary and still include whatever the others produced; a failed reviewer downgrades the overall verdict to at least **REVIEW BEFORE COMMIT**.
