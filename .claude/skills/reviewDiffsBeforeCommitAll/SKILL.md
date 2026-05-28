---
name: reviewDiffsBeforeCommitAll
description: "Full pre-commit review covering both backend and frontend in parallel. Runs the backend architectural review (spawning background subagents across layers, tests, docs, and queries) and the frontend CLAUDE.md compliance review at the same time, and additionally launches one architecture-compliance-reviewer subagent per side that has diffs, then merges everything into one consolidated report."
disable-model-invocation: true
---

Orchestrates the two existing pre-commit skills so backend and frontend are reviewed concurrently, and additionally launches a dedicated `architecture-compliance-reviewer` subagent for each side that has diffs. All results are merged into a single report.

The two child skills have very different shapes:

- **`/reviewDiffsBeforeCommitBackend`** is asynchronous: it spawns many background subagents (`run_in_background: true`) across the affected backend layers, tests, docs, and queries, then waits for their automatic completion notifications before consolidating.
- **`/reviewDiffsBeforeCommitFrontend`** is synchronous and read-only: it just reads every `frontend/**/CLAUDE.md` and the frontend diffs, then emits a compliance report inline.

On top of those two child skills, this skill launches, for each side that changed, an `architecture-compliance-reviewer` subagent scoped to that side's modified files. Each reviewer audits its scope against the full `CLAUDE.md` hierarchy of that layer (root `CLAUDE.md`, layer `CLAUDE.md` files, and any `*_guide.md` they reference). These are an addition to — not a replacement for — the two child reviews.

Because of this asymmetry, do not literally call both child skills as if they were symmetric. Instead, follow the phased workflow below: kick off the backend's background agents AND both architecture reviewers first (so they run while you do other work), then do the frontend review inline, then wait for all background agents and merge.

Usage: `/reviewDiffsBeforeCommitAll` (same optional `$ARGUMENTS` as the backend skill: `--tests dir1 dir2 ...` and `--md path1 path2 ...`).

---

## PHASE 1 — Detect scope

Run in parallel:
- `git diff --cached --name-only`
- `git diff --name-only`

Combine and deduplicate. If empty → "No diffs found (staged or unstaged). Nothing to review." and STOP without launching anything.

Split the changed files into:
- **Backend files**: paths starting with `backend/`.
- **Frontend files**: paths starting with `frontend/`.
- **Other**: everything else (report them as "out of scope" at the end; neither child skill nor either architecture reviewer reviews them).

Print a short summary to the user: total changed files, backend count, frontend count, other count.

Decide which reviews to run:
- If there are backend files → run the backend phase (child skill background agents + backend architecture reviewer).
- If there are frontend files → run the frontend phase (inline child review + frontend architecture reviewer).
- If only one side has changes, run only that side and skip the other entirely.
- If neither side has changes (only "other" files) → report "No backend or frontend files to review." and STOP.

---

## PHASE 2 — Kick off all background work (backend review + architecture reviewers)

Launch everything that can run in the background in this phase, so it all progresses while you do the inline frontend review in Phase 3.

### 2a — Backend child review (background) — only if backend files exist

Read `.claude/skills/reviewDiffsBeforeCommitBackend/SKILL.md` and execute its **PHASE 1** (diff analysis) and **PHASE 2** (launch all agents) inline, passing through any `--tests` / `--md` values from `$ARGUMENTS`. Every agent the backend skill launches uses `run_in_background: true`, so after this phase you have a fleet of backend subagents running asynchronously.

Do NOT execute the backend skill's PHASE 3 (consolidation) yet — leave the agents running and move on. This is the whole reason for running "All": the frontend review fills the time the backend agents take.

### 2b — Architecture compliance reviewers (background) — one per side that changed

In the SAME message that you use to dispatch background work, also launch the relevant `architecture-compliance-reviewer` subagent(s). Use `run_in_background: true` for each (the agent definition marks itself as `background: true`). Launch the backend child agents (2a) and these reviewers concurrently so nothing is serialised.

**Backend architecture reviewer** (only if backend files exist):

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

**Frontend architecture reviewer** (only if frontend files exist):

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

Pass to each architecture reviewer ONLY the resolved paths under its own layer prefix — never mix backend and frontend paths into the same reviewer run; their `CLAUDE.md` hierarchies are different and combining them would dilute the rule set on both sides.

Print a brief note listing the backend agents and the architecture reviewer(s) launched, then continue immediately to Phase 3 without polling them.

---

## PHASE 3 — Run the frontend review (foreground, while background agents work)

Only if there are frontend files.

Read `.claude/skills/reviewDiffsBeforeCommitFrontend/SKILL.md` and execute its workflow inline end-to-end: load every `frontend/**/CLAUDE.md`, map each frontend diff against the rules, and produce the frontend compliance report.

Do NOT print the frontend report to the user yet — hold it in working memory. It will be merged with the other reports in Phase 5.

Important: while executing the frontend review, do **not** poll, check, or wait on the background agents (backend fleet or architecture reviewers). They will report back automatically.

---

## PHASE 4 — Wait for all background agents to finish

If any background agents were launched in Phase 2 (backend child fleet and/or either architecture reviewer), wait for all of their automatic completion notifications before continuing. Do not call any tool purely to check on them.

If there were no backend files and no frontend files, the skill already stopped in Phase 1, so this phase never runs in that case.

---

## PHASE 5 — Merge and present a single consolidated report

Once the inline frontend phase is done AND every launched background agent has returned, produce ONE report with this structure:

```
## Pre-Commit Review Report (Backend + Frontend)

### Executive Summary
- Backend (Group A / new code): X blockers, Y majors, Z minors, W suggestions
- Backend (Group B / pre-existing): X blockers, Y majors, Z minors, W suggestions
- Frontend: X violations, Y ambiguities, Z pass
- Architecture compliance — Backend: <verdict: COMPLIANT / MOSTLY COMPLIANT / NON-COMPLIANT / CANNOT ASSESS> — N blockers, M majors, K minors, L nits
- Architecture compliance — Frontend: <same shape>
- Files out of scope (neither backend nor frontend): N — listed at the bottom
- Overall verdict: BLOCK COMMIT / REVIEW BEFORE COMMIT / SAFE TO COMMIT

### Backend
<Backend skill's full consolidated report here — Group A and Group B, per-layer, per-category, exactly as produced by its PHASE 3.>

### Frontend
<Frontend skill's full compliance report here — Scope, CLAUDE.md files consulted, Findings per file, Verdict.>

### Architecture Compliance — Backend
<Full report returned by the backend architecture-compliance-reviewer agent, verbatim — sections 1 through 10.>

### Architecture Compliance — Frontend
<Full report returned by the frontend architecture-compliance-reviewer agent, verbatim — sections 1 through 10.>

### Out-of-scope files (not reviewed)
<bullet list of paths, if any>
```

### Overall verdict rules

Combine all the verdicts that ran:

- If the backend child verdict is `BLOCK COMMIT`, **or** the frontend child verdict is `BLOCKED`, **or** any architecture reviewer returns `NON-COMPLIANT` or contains at least one `BLOCKER` finding → overall **BLOCK COMMIT**.
- Else if the backend child verdict is `REVIEW BEFORE COMMIT`, **or** the frontend child verdict is `NEEDS USER DECISION`, **or** any architecture reviewer returns `MOSTLY COMPLIANT` or `CANNOT ASSESS` → overall **REVIEW BEFORE COMMIT**.
- Else → overall **SAFE TO COMMIT**.

Only Group A backend findings count toward the backend child verdict (Group B is informational, same rule as the backend skill).

If one side was skipped because it had no changes, omit that side's sections (both the child review and its architecture reviewer) and base the verdict on the side that ran.

---

## Important rules

- Do not run the two child skills sequentially in full — that would serialise all the waiting time. The whole point is overlap: backend agents and both architecture reviewers run in the background while you handle the frontend review on the main thread.
- The architecture reviewers are an ADDITION to the two child reviews, not a replacement. A complete run produces, for each side that changed, both the child review and the architecture-compliance review. Never drop the child reviews in favour of the architecture reviewers, or vice versa.
- Do not modify any file. This skill, its two children, and the architecture reviewers are all read-only.
- Never propose edits to any `CLAUDE.md` or `*_guide.md`. If docs seem outdated, that is itself a finding in the report (backend side via md-reviewer, frontend side via the compliance report's notes, architecture side via the reviewer's own report). If any architecture reviewer suggests editing a `CLAUDE.md`, rewrite it before presenting the consolidated report so the suggestion points at the corresponding `*_guide.md` or at the code instead.
- Pass `$ARGUMENTS` (`--tests`, `--md`) through to the backend child phase only. The frontend child skill and the architecture reviewers take no such arguments.
- If any child phase or architecture reviewer crashes or returns nothing, report it explicitly in the Executive Summary and still present whatever the others produced; do not silently drop a side. A failed reviewer downgrades the overall verdict to at least **REVIEW BEFORE COMMIT**.
