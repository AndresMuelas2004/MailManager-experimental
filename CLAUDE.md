# CLAUDE.md

  This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

  Project-specific details are maintained in @repository_guide.md (auto-imported into context).

  ---

  ## General Architecture 

  This section describes the layered architecture, structural rules, and conventions that apply to any project following this pattern. It is project-agnostic and should not be
  modified for domain-specific changes.

  ### 1 Layer Rules (Auto-Loaded)

  Each layer has its own `CLAUDE.md` that Claude Code loads automatically when reading files in that directory. These layer-level `CLAUDE.md` files are project-agnostic,
  transferable, and **must never be modified**. Each one references internally a `*_guide.md` with project-specific details.

  Layers with their own `CLAUDE.md`:
  - `backend/api/`
  - `backend/auth/`
  - `backend/database/`
  - `backend/core/`
  - `backend/Scripts/`
  - `backend/tests/unit/`
  - `backend/tests/integration/`
  - `backend/tests/e2e/`
  - `frontend/`
  - `docs/` (plus its subdirectories `docs/features/` and `docs/limits/`, each with its own `CLAUDE.md`)

  The `docs/` tree is the exception to the `*_guide.md` reference: its `CLAUDE.md` files reference no `*_guide.md` — the project-specific content is the documents themselves, indexed by each subdirectory's `README.md` (see § 5).

  **Hard rule**: these layer rules are non-negotiable and override any conflicting project-specific guidance.

  ### 2 Monorepo Structure

  - `backend/` — API server organized in layers (FastAPI + Python).
  - `frontend/` — Client application (React + Vite + TypeScript + Tailwind).
  - `docs/` — Narrative project documentation for the team (feature behavior + limits catalogs). See § 5.

  ### 3 Excluded Directories

  - `backend/Scripts/` — personal developer scripts (manual tests, one-off utilities). Claude must **not** read, edit, or reference files in this directory unless the user explicitly requests it. These scripts are unrelated to the application's business logic, is only to try manual executions.

  ### 4 Backend Layers and Relationships

  → API (routers → services → rest of the layers)
  → Auth       (identity verification, session management)
  → Database   (persistence)
  → Core       (domain logic, provider clients)

  Communication rules:

  - Only **Services** (inside API) talk to Auth, Database, and Core.
  - Auth, Database, and Core are **independent** — none imports from another.
  - No lower layer imports from API.

  Each layer defines its own error hierarchy. Services translate lower-layer errors into API-layer errors. For specifics, consult the layer's `CLAUDE.md` (auto-loaded when reading files in that directory).

  ### 5 Two-Level Documentation Pattern

  Each layer has two documentation files:

  - `CLAUDE.md` (in each layer directory) — general, transferable rules. Not modified for project changes. Auto-loaded by Claude Code when reading files in that directory.
  - `*_guide.md` — project-specific details. Claude updates these when the project changes.

  The layer `CLAUDE.md` references its guide. This root `CLAUDE.md` lists the layers that have their own rules (§ 1.1).

  A third documentation home complements this pattern: `docs/` — narrative, project-specific documentation aimed at the engineering team and future maintainers, not at Claude as a source of architectural rules. Its structure: a directory-level `CLAUDE.md` (general rules), and two paired subdirectories — `docs/features/` (behavior-level description of each feature) and `docs/limits/` (exact figures and the exhaustive "what it does NOT support" list) — each with its own `CLAUDE.md` and a `README.md` index. Documents pair 1:1 by slug (`features/<slug>.md` ↔ `limits/<slug>.md`); never create or keep one half without the other. A document under `docs/` carries the same authority as a `*_guide.md` (§ 9).

  ### 6 Style and Code Quality

  - Python: PEP 8, FastAPI conventions, `from __future__ import annotations` in all modules.
  - TypeScript: ESLint config in `frontend/eslint.config.js`.
  - Code language: English everywhere — identifiers, comments, docstrings, and all `.md` documentation files tracked by git. Exception: documents under `docs/` may be written in the team's working language (see `docs/CLAUDE.md` § 6); every `CLAUDE.md` itself must remain in English.
  - Comments only where they clarify non-obvious logic; avoid noise or redundancy.

  ### 7 Immutable Files

  Every `CLAUDE.md` inside this repository — both the root `CLAUDE.md` and every layer-level `CLAUDE.md` (listed in § 1.1) — is protected by a pre-edit hook that prevents any modification. The protection is scoped to this repository only; `CLAUDE.md` files outside the repo are not affected. Claude must never propose direct edits to these files. Instead, describe the suggested change — what, where, and why — so the developer can apply it manually.
  Project-specific changes always go in the corresponding `*_guide.md` file, which is not protected.

  ### 8 Plan Execution Rules

  Every plan produced in plan mode for a non-trivial feature implementation must include these two final steps, in this order:

  **Penultimate step — Tests update:**
  Review the code changes introduced by the plan and add new tests or update existing ones to cover the new or modified functionality.

  **Final step — Documentation update (critical):**
  This step is the foundation of the entire quality assurance system. The Documentation Priority rule (§ 9) establishes that `.md` files are always the source of truth: when code contradicts documentation, the documentation is correct and the code must change. Review subagent md-reviewer rely on this principle to catch and fix code mistakes.

  This only works if the documentation accurately reflects the intended behavior after every plan execution. If a `*_guide.md` is left outdated or partially updated, two things break:
  1. Legitimate new code may be flagged as "wrong" because it doesn't match the stale `.md`.
  2. Actual code mistakes may go undetected because the `.md` never described the new functionality.

  Therefore, this documentation update is not a formality — it is the step that keeps the `.md`-as-source-of-truth model reliable.
  Claude must directly review and update — without launching any external agent or command — the following files so they accurately reflect the new or changed functionality:
  - Root `repository_guide.md`.
  - Root `README.md`.
  - Every `*_guide.md` in directories affected by the plan's changes.
  - Every `docs/features/<slug>.md` / `docs/limits/<slug>.md` pair affected by the plan's changes — update both twins, and the two `README.md` indexes when a feature is added or renamed.

  ### 9 Documentation Priority

  When rules or information conflict, the following precedence applies (highest to lowest):
  **This root `CLAUDE.md`** — general architecture rules. Supreme authority.
  **Layer `CLAUDE.md` files** (e.g. `backend/api/CLAUDE.md`, `docs/CLAUDE.md` and its subdirectory `CLAUDE.md` files) — structural rules for that layer. Override anything below.
  **`*_guide.md` files and `docs/` documents** (e.g. `api_guide.md`, `repository_guide.md`, `docs/features/<slug>.md`, `docs/limits/<slug>.md`) — project-specific details that supplement the `CLAUDE.md` files. Never contradict levels above.
  **The source code itself** — the actual implementation. When code contradicts documentation at any level above, the documentation is correct and the code is what needs to change.

  This hierarchy applies to all decisions: error handling, layer boundaries, naming conventions, allowed imports, and any other rule. If a lower-priority source conflicts with a higher-priority one, always follow the higher-priority source and flag the conflict.

  ### 10 Common Mistakes Tracking

  @common_mistakes.md

  Recurring mistakes are tracked in the file imported above. Claude must treat every entry as a hard rule with the same authority as this `CLAUDE.md`.

  **Proactive flagging:** When Claude notices it is repeating an error — or the user corrects the same kind of mistake more than once across conversations — Claude must explicitly ask the user whether the
  correction should be added to `common_mistakes.md`. Do not add entries autonomously; always ask first.

  ### 11 Integration and E2E Test Execution

  Never run the integration or E2E test suites on your own initiative. Run them only when the user explicitly requests it; in that case you may decide to run them.

  ### 12 Application Startup and Live Browser Verification

  Never start any part of the application stack — database, backend, or frontend (via Podman/compose, uvicorn, the Vite dev server, or any other means) — and never verify a change you made by navigating the running app on localhost with the Playwright MCP (or any other browser automation tool) on your own initiative. These actions are allowed only when the user literally requests them in the conversation. In particular, never include them as final verification steps in plans, and never perform them voluntarily as a "check that my change works" step — live verification belongs to the user unless explicitly delegated.

  ### 13 Memory Files — No Autonomous Writes

  Never edit, restructure, or add entries to the persistent memory store — `MEMORY.md` and the individual memory files under the session's `memory/` directory — on your own initiative. This overrides the memory system's default of proactively saving facts: write to memory only when the user explicitly asks you to remember something. Same "never on your own initiative" discipline as § 12 (live verification) and § 10's rule that `common_mistakes.md` entries are never added autonomously.