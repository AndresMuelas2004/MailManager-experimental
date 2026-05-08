# General Documentation Directory Rules

This is the `CLAUDE.md` for the **`docs/` directory**. It is the general reference for what this directory contains, who its readers are, and how each file inside it must be written. Every aspect covered here is transferable to any application that follows this layered architecture — nothing is specific to a single project.

**Project-agnostic by design.** Nothing here references a concrete domain, entity, or feature. Every rule applies to any repository that adopts this directory.

**Reusable.** Copy this file into a new project to establish the same documentation directory from day one.

**Precedence.** In case of conflict between this file and any other document inside `docs/`, this file takes precedence. The root `CLAUDE.md` always overrides this file.

**Immutable.** This file must never be edited. All future rule changes go through a new version of this file.

## 1. Purpose

`docs/` is the home for **narrative, project-specific documentation aimed at the engineering team and future maintainers** — not at end users, and not at Claude as a source of architectural rules.

The directory is organised by subdirectory, one per kind of documentation. Today it contains:

- `features/` — behavior-level descriptions of individual features (one Markdown file per feature).

New subdirectories may be added when a different kind of documentation needs a home (for example architecture decisions, runbooks, post-mortems). Any new subdirectory must follow the same spirit established here: narrative prose, written for humans, complementary to (never replacing) the `CLAUDE.md` and `*_guide.md` files that live next to the code.

## 2. Audience

The primary audience is the **engineering team and future maintainers** — people who need to understand a feature, a decision, or a procedure without spelunking through every file that implements it. This is not user-facing documentation, and it is not the place where Claude looks for architectural rules or implementation patterns.

## 3. Scope — What Belongs Here

- Behavior-level descriptions of features (under `features/`): triggers, conditions, edge cases, and observable results.
- Accepted limitations (especially for MVP-scoped work): what is **deliberately** left out and why.
- Decisions and trade-offs whose rationale is not visible in the code itself.
- Concrete examples of inputs paired with the resulting behavior.
- Cross-feature or cross-area interactions that are easy to miss when reading any single module in isolation.

### What does NOT belong here

- Architectural or layer-level rules → those live in the corresponding layer `CLAUDE.md`.
- Project-specific implementation details that mirror the code one-to-one → those live in the corresponding `*_guide.md`.
- API request/response schemas, error codes, or wire contracts → those live next to the code (`schemas/`, error class definitions, etc.).
- Test specifications → those live in the test files.
- Any line that merely paraphrases a function name, a field, or a signature — if it would silently rot the moment the code is renamed, it does not pay for its tokens.

## 4. Writing Style

- **Behavior-first, not implementation-first.** Describe what the user (or another system) experiences, not how the code is wired internally.
- **Natural prose.** Speak to a teammate, not to a compiler. Headings, bullet lists, and short paragraphs are encouraged; dense API-style notation is not.
- **Concrete examples.** Show inputs and the resulting behavior whenever it sharpens a rule.
- **Make trade-offs explicit.** "What we left out and why" is often the most valuable part of the document.
- **Code references earn their place.** Pointing at a file path or a function is fine when it pinpoints the source of a non-obvious behavior; otherwise, prose is better.
- **One topic per file.** If a document starts to cover two unrelated topics, split it.

## 5. How This Differs From Other Documentation

| Document                            | Primary audience          | Style                                                  |
|-------------------------------------|---------------------------|--------------------------------------------------------|
| Root `CLAUDE.md`                    | Claude + maintainers      | Supreme architectural rules                            |
| Layer `CLAUDE.md` (e.g. `api/`)     | Claude + maintainers      | Project-agnostic structural rules                      |
| `*_guide.md` (e.g. `api_guide.md`)  | Claude + maintainers      | Project-specific implementation rules                  |
| `docs/<area>/<topic>.md`            | Team and new contributors | Narrative description of a feature/decision/runbook    |

Rule of thumb: if removing the document from the repository would make a new contributor unable to understand **what something is supposed to do or why a decision was made** without reading the implementation, the document belongs here. If removing it would only hide an architectural or implementation rule, it belongs in a `CLAUDE.md` or a `*_guide.md` instead.

## 6. File Naming and Language

- One Markdown file per topic. Filename matches the topic's short identifier in lowercase (e.g. `lupa.md`, `attachments.md`, `email-search.md`). Use kebab-case if the identifier needs more than one word.
- Files inside `docs/` may be written in **any language the team uses**, since the readers are human contributors. Consistency within a single file is required — do not mix languages within one document.
- This `CLAUDE.md` itself must remain in English (the rule that applies to every `CLAUDE.md` in the repository).

## 7. Maintenance and Authority

- Add a new file when a new feature ships, a significant decision is made, or a runbook is needed, and any of its details are non-trivial enough to deserve a narrative explanation.
- Update an existing file whenever the underlying feature, decision, or procedure changes — even small UX shifts (debounce timing, minimum character count, ordering of results, scope boundaries) belong in the file. A stale document is worse than a missing one.
- Do not delete a file unless the topic itself is removed from the application. If the file is "out of date", update it; do not remove it.
- A document under `docs/` carries the same authority as a `*_guide.md`: when the code contradicts it, the document is the source of truth and the code is what needs to change. Conversely, when the document no longer reflects the desired behavior, it must be updated as part of the same change that altered the underlying topic — never left for "later".
