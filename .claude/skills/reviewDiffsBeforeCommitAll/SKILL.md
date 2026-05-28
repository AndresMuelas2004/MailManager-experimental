---
name: reviewDiffsBeforeCommitAll
description: "Pre-commit architectural compliance audit covering backend and frontend in parallel. Launches an architecture-compliance-reviewer subagent for whichever side(s) have diffs (staged or unstaged), each one verifying that every rule declared in that side's CLAUDE.md hierarchy is fully respected. Skips a side entirely when it has no diffs."
disable-model-invocation: true
---

Orchestrates parallel architectural compliance audits before a commit. For each side that has uncommitted changes (`backend/` and/or `frontend/`), launches a dedicated `architecture-compliance-reviewer` subagent scoped to that side's modified files. Each subagent audits its scope against the full `CLAUDE.md` hierarchy of that layer (root `CLAUDE.md`, layer `CLAUDE.md` files, and any `*_guide.md` they reference).

The two agents are launched concurrently in a single message so the audits run in parallel. If only one side has diffs, only that side is reviewed; the other is skipped entirely.

Usage: `/reviewDiffsBeforeCommitAll` (no arguments).

---

## PHASE 1 — Detect scope

Run in parallel:
- `git diff --cached --name-only`
- `git diff --name-only`

Combine and deduplicate. If the combined list is empty → output `"No diffs found (staged or unstaged). Nothing to review."` and STOP without launching anything.

Split the changed files into three buckets by path prefix:
- **Backend files**: paths starting with `backend/`.
- **Frontend files**: paths starting with `frontend/`.
- **Other**: everything else (these are reported as "out of scope" at the end; the architecture reviewer does not audit them).

Print a one-line summary to the user: total changed files, backend count, frontend count, other count.

Decide which reviewers to launch:
- If there are backend files → launch the backend reviewer.
- If there are frontend files → launch the frontend reviewer.
- If both → launch both, in parallel, in the same message.
- If neither (only "other" files) → output `"No backend or frontend files to review."` and STOP.

---

## PHASE 2 — Launch architecture reviewers in parallel

Launch the relevant `architecture-compliance-reviewer` subagent(s) in a SINGLE message containing multiple `Agent` tool calls so they run concurrently. Use `run_in_background: true` for each launch (the agent definition marks itself as `background: true`, so this matches its design and lets both audits progress truly in parallel).

### Backend agent (only if backend files exist)

- `subagent_type`: `architecture-compliance-reviewer`
- `description`: `Backend architecture compliance review`
- `run_in_background`: `true`
- `prompt` (substitute `<path N>` with the actual resolved backend paths from Phase 1):

```
Audit architectural compliance of the following modified backend files against the documentation hierarchy.

Scope (resolved paths, all under `backend/`):
- <path 1>
- <path 2>
- ...

Goal: verify that EVERY architectural rule declared by the relevant CLAUDE.md hierarchy (root `CLAUDE.md`, every layer-level `backend/<layer>/CLAUDE.md` that applies to the scope, and any `*_guide.md` they reference) is fully respected by these files. Follow the phased workflow defined in your own agent definition strictly — do not skip Phase 2 (Documentation Hierarchy) even if you think you already know the project, because rules may have changed since your last run.

Produce the full report structure defined in your agent definition (sections 1 through 10), with the verdict at section 9.

This is a read-only audit: you must not modify any file. Do not propose edits to any `CLAUDE.md` (they are immutable per root `CLAUDE.md` § 7) — redirect every suggestion either to the code or to the corresponding `*_guide.md`.
```

### Frontend agent (only if frontend files exist)

- `subagent_type`: `architecture-compliance-reviewer`
- `description`: `Frontend architecture compliance review`
- `run_in_background`: `true`
- `prompt` (substitute `<path N>` with the actual resolved frontend paths from Phase 1):

```
Audit architectural compliance of the following modified frontend files against the documentation hierarchy.

Scope (resolved paths, all under `frontend/`):
- <path 1>
- <path 2>
- ...

Goal: verify that EVERY architectural rule declared by the relevant CLAUDE.md hierarchy (root `CLAUDE.md`, `frontend/CLAUDE.md`, every nested `frontend/**/CLAUDE.md` that applies to the scope, and any `*_guide.md` they reference) is fully respected by these files. Follow the phased workflow defined in your own agent definition strictly — do not skip Phase 2 (Documentation Hierarchy) even if you think you already know the project, because rules may have changed since your last run.

Produce the full report structure defined in your agent definition (sections 1 through 10), with the verdict at section 9.

This is a read-only audit: you must not modify any file. Do not propose edits to any `CLAUDE.md` (they are immutable per root `CLAUDE.md` § 7) — redirect every suggestion either to the code or to the corresponding `*_guide.md`.
```

After dispatching the launch(es), print a brief one-line note listing which agents were launched (e.g. `"Launched backend + frontend architecture reviewers in parallel."`) and proceed to Phase 3 without polling them.

---

## PHASE 3 — Wait for the reviewer(s) to finish

If you launched any agent in Phase 2, wait for all of their automatic completion notifications before continuing. Do not call any tool purely to check on them — the runtime will notify you when each finishes.

If neither side had diffs the skill already stopped in Phase 1, so this phase never runs in that case.

---

## PHASE 4 — Consolidated report

Once every launched agent has returned, produce ONE consolidated report with this exact structure:

```
## Pre-Commit Architectural Compliance Report

### Executive Summary
- Backend: <verdict from backend agent: COMPLIANT / MOSTLY COMPLIANT / NON-COMPLIANT / CANNOT ASSESS> — N blockers, M majors, K minors, L nits
- Frontend: <same shape as Backend>
- Files out of scope (neither backend nor frontend): N — listed at the bottom
- Overall verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT

### Backend
<Full report returned by the backend architecture-compliance-reviewer agent, verbatim — sections 1 through 10 as defined in the agent's own output format.>

### Frontend
<Full report returned by the frontend architecture-compliance-reviewer agent, verbatim — sections 1 through 10.>

### Out-of-scope files (not reviewed)
<bullet list of paths that were neither backend nor frontend, if any; omit the section if the list is empty>
```

If only one side ran (the other had no diffs), omit that side's section in both the Executive Summary and the body, and base the overall verdict on the side that actually ran.

### Overall verdict rules

Combine the agent verdicts as follows:

- If any side returns `NON-COMPLIANT` **or** any side's report contains at least one `BLOCKER` finding → overall **BLOCK COMMIT**.
- Else if any side returns `MOSTLY COMPLIANT` **or** `CANNOT ASSESS` → overall **REVIEW BEFORE COMMIT**.
- Else (every side that ran returned `COMPLIANT` and reported no `BLOCKER`) → overall **SAFE TO COMMIT**.

If an agent crashes or returns an empty / malformed report, state it explicitly in the Executive Summary for that side (e.g. `"Backend: REVIEWER FAILED — no report produced"`), still present whatever the other side produced, and downgrade the overall verdict to at least **REVIEW BEFORE COMMIT**. Never silently drop a side.

---

## Important rules

- This skill is read-only. Neither it nor the launched agents may modify any file under any circumstance.
- Never propose edits to any `CLAUDE.md` — they are immutable per root `CLAUDE.md` § 7. The reviewer agent already knows this; if you see a suggested edit to a `CLAUDE.md` in either agent's report, rewrite it before presenting the consolidated report so the suggestion points at the corresponding `*_guide.md` or at the code instead.
- Pass to each agent ONLY the resolved paths under its own layer prefix — never mix backend and frontend paths into the same agent run; their `CLAUDE.md` hierarchies are different and combining them would dilute the rule set on both sides.
- Launch the two agents in a SINGLE message with two `Agent` tool calls so they run concurrently. Do not launch them sequentially — that would serialise the wait time for no benefit.
- This skill takes no arguments. Ignore any `$ARGUMENTS` if present.
