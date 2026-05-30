# Limits Documentation Subdirectory Rules

This is the `CLAUDE.md` for the **`docs/limits/` subdirectory**. It defines how the quantitative "how far it goes" documents in this folder must be written. It is loaded on demand only when working on files here, and it **complements** the parent [`../CLAUDE.md`](../CLAUDE.md) — it must never repeat what the parent already says.

**Project-agnostic by design.** Nothing here names a concrete feature or domain. Every rule transfers to any project that adopts the behavior/limits two-tier documentation pattern.

**Precedence.** The root `CLAUDE.md` overrides this file, and the parent `docs/CLAUDE.md` overrides this file. This file governs only `docs/limits/`.

**Immutable.** This file must never be edited. All future rule changes go through a new version of this file.

## 1. What this folder is

One Markdown file per feature: the **quantitative complement** of `features/`. It holds exact caps, sizes, TTLs, retries, concurrency, minimums, debounce, pagination, exact permissions/scopes, and the exhaustive list of "what it does NOT support" with a short *why* for each deliberate omission. Audience: the engineering team and future maintainers.

## 2. The behavior / limits split (mirror of `features/`)

- `limits/<slug>.md` holds the **figures and the boundaries**; `features/<slug>.md` holds the **behavior**. Same slug, **1:1** pairing, never one half without the other.
- **Put the figures here, not in `features/`.** This is the only place a concrete value (a size cap, a retry count, a timeout, a minimum) is meant to appear as a number.
- **Do not re-narrate behavior here.** A limits document is a catalog, not a flow description. If you find yourself explaining *how* something works step by step, that belongs in the behavior twin — link to it instead.
- Keep the overlap with the twin minimal and deliberate.

## 3. Cross-links

- To the behavior twin: `[../features/<slug>.md](../features/<slug>.md)`.
- To a sibling limits doc: `[<other-slug>.md](<other-slug>.md)`.
- A figure that belongs to another feature lives in **that** feature's limits doc — link to it, do not copy the number.
- Never leave a broken link.

## 4. Shape of a limits document

- Open with a one-line statement of what the catalog covers and a pointer to its behavior twin.
- Prefer **tables** (`Limit | exact value | where it applies | note`) and bullet lists over prose.
- Include a dedicated section listing **"what it does NOT support"**, one short *why* per item.
- Every figure must be the **exact** value taken from the code, with enough context to locate where it applies.
- Language: match the other documents already in this folder; one language per file (parent `docs/CLAUDE.md` § 6).

## 5. Source of truth — figures must be exact

These documents are treated as correct when the code disagrees (root `CLAUDE.md` § 9). A wrong number here will misdirect a future reviewer into "fixing" code that was right. **Verify every figure against the implementation** when you write or edit it; never copy a number from another document without confirming it in the code first.

## 6. Adding or changing limits

- New feature → create the limits doc together with its behavior twin, and add both to the `README.md` indexes.
- A changed cap/number → update it here in the same change. If a figure has leaked into the behavior twin, move it back here and leave only a passing mention plus a link there.
