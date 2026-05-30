# Features Documentation Subdirectory Rules

This is the `CLAUDE.md` for the **`docs/features/` subdirectory**. It defines how the behavior-level feature documents in this folder must be written. It is loaded on demand only when working on files here, and it **complements** the parent [`../CLAUDE.md`](../CLAUDE.md) — it must never repeat what the parent already says.

**Project-agnostic by design.** Nothing here names a concrete feature or domain. Every rule transfers to any project that adopts the behavior/limits two-tier documentation pattern.

**Precedence.** The root `CLAUDE.md` overrides this file, and the parent `docs/CLAUDE.md` overrides this file. This file governs only `docs/features/`.

**Immutable.** This file must never be edited. All future rule changes go through a new version of this file.

## 1. What this folder is

One Markdown file per feature, describing **behavior**: what the feature does, what the user experiences, the triggers, the conditions, the edge cases, and the *why* behind design decisions. Audience: the engineering team and future maintainers — not end users, and not Claude as a source of architectural rules.

## 2. The behavior / limits split (the rule that matters most here)

Every feature is documented across **two paired files**:

- `features/<slug>.md` — **behavior**: how it works and what the user experiences.
- `limits/<slug>.md` — **exact figures** (caps, sizes, TTLs, retries, timeouts, minimums, debounce) plus the exhaustive list of "what it does NOT support".

The pair shares a **slug**: `features/lupa.md` ↔ `limits/lupa.md`. The pairing is **1:1** — never create or keep one half without the other.

**In `features/`, do not state cap values as figures.** Mention a limit only in passing and defer the number to the twin (e.g. "up to a cap — the exact figure is in [`../limits/<slug>.md`]"). Numbers live in `limits/`; the behavior and the *why* live here. Keep the overlap minimal and deliberate.

## 3. Cross-links

- To the limits twin: `[../limits/<slug>.md](../limits/<slug>.md)`.
- To a sibling feature: `[<other-slug>.md](<other-slug>.md)`.
- Prefer linking **inside `docs/`** over linking to code-level guides (`*_guide.md`, `repository_guide.md`) — those target a different audience.
- Never leave a broken link: the target must already exist or be created in the same change.

## 4. Shape of a feature document

- Open with a one-paragraph statement of what the document covers and a pointer to its limits twin.
- Numbered sections (`## N. Title`), short paragraphs, and concrete input→result examples.
- Close with a `## Resumen en una frase` section: a single blockquote that condenses the whole feature.
- Language: match the other documents already in this folder; do not mix languages within one file (parent `docs/CLAUDE.md` § 6).

## 5. Source of truth — keep it accurate

A document here carries the same authority as a `*_guide.md`: when the code contradicts it, the document is treated as correct and the code is what must change (root `CLAUDE.md` § 9). So **every behavioral claim must match the real, current code.** When you write or edit a feature doc, confirm the claim against the implementation — an inaccurate document silently misdirects a future reviewer into "fixing" code that was right.

## 6. Adding or changing a feature

- New feature → create **both** `features/<slug>.md` and `limits/<slug>.md`, and add the entry to **both** `README.md` indexes.
- Changed behavior → update the feature doc (and its limits twin, if a figure moved) in the same change. A stale document is worse than a missing one.
