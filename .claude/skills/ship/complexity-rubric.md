# Complexity Rubric — scoring a worktree's diff for `/ship TODO`

This file is the **single source of truth** for how `/ship TODO` rates each worktree's complexity. The batch flow ([mode-todo.md](mode-todo.md)) reads this file and applies the rubric to order worktrees **most → least complex**. The scoring is deliberately **shallow**: it uses only git diff statistics and file paths — no semantic reading of the code.

To re-tune the ordering, edit the tables and weights here; do **not** change the flow in `mode-todo.md`.

---

## 1. Collect the raw data (read-only)

For each worktree (path `<wt>`, branch `<branch>`), against `master`:

```bash
# Per-file churn the worktree ADDS to master (merge-base diff: only what this branch introduced).
git -C <wt> diff --numstat master...<branch>      # lines: "<added>\t<deleted>\t<path>"

# Commit count the branch adds on top of master.
git -C <wt> rev-list --count master..<branch>
```

If the worktree's working tree is **dirty** (it will be auto-committed before shipping — see `mode-todo.md` D3), also fold in the uncommitted churn so the score is not under-counted:

```bash
git -C <wt> status --porcelain      # non-empty ⇒ dirty
git -C <wt> diff --numstat HEAD     # staged + unstaged churn vs HEAD
```

Merge the two `--numstat` outputs by path (sum added/deleted per file; union the file set). Binary files print `-` for added/deleted in numstat — count them as **1 file, 0 lines** (they cannot be churn-measured but still widen the surface).

> A worktree whose `master...<branch>` diff is empty **and** whose working tree is clean has **nothing to ship** — it is skipped upstream in `mode-todo.md`, never scored.

---

## 2. Derived metrics

From the merged data, compute:

| Symbol | Meaning |
|---|---|
| `F` | number of **unique files** changed |
| `L` | total **lines changed** = Σ(added + deleted) over all files |
| `B` | number of **distinct layers** touched (breadth) — see §3 |
| `W` | **max criticality weight** among the touched layers — see §3 |
| `C` | number of **commits** (`rev-list --count`) |
| `M` | **migrations flag** — does any changed path sit under `backend/database/migrations/versions/`? |

---

## 3. Path → layer mapping and criticality weight

Classify **each** changed path into exactly one layer using the **first** pattern that matches (top to bottom). `W` is the highest weight among all touched layers; `B` is the count of distinct layers.

| Path pattern (first match wins) | Layer | Weight |
|---|---|---|
| `backend/database/migrations/` | migrations | 5 |
| `backend/core/` | core | 4 |
| `backend/api/` | api | 4 |
| `backend/database/` | database | 3 |
| `backend/auth/` | auth | 3 |
| `frontend/` | frontend | 3 |
| repo-root config (`compose*.yml`, `requirements*.txt`, `*_guide.md`, `README.md`, `*.toml`, `.env*`, `Dockerfile*`, `Caddyfile`, settings) | root-config | 3 |
| `backend/tests/` | tests | 2 |
| `docs/` | docs | 1 |
| `backend/Scripts/` | scripts | 1 |
| anything else not matched above | other | 2 |

> Rationale: migrations carry the highest weight because a forked Alembic chain (Type 4 conflict) is the most dangerous rebase outcome; `core`/`api` carry domain + orchestration logic; `docs`/`scripts` rarely cause logical conflicts.

---

## 4. Per-factor points

Bucket each metric into points:

| Factor | Buckets → points |
|---|---|
| `F` (files) | ≤2 → 1 · 3–5 → 2 · 6–12 → 3 · 13–30 → 4 · >30 → 5 |
| `L` (lines) | <50 → 1 · 50–200 → 2 · 201–600 → 3 · 601–1500 → 4 · >1500 → 5 |
| `B` (breadth) | 1 → 1 · 2 → 2 · 3 → 3 · ≥4 → 4 |
| `W` (criticality) | the layer weight itself, 1–5 |
| `C` (commits) | 1 → 1 · 2–4 → 2 · 5–9 → 3 · ≥10 → 4 |
| `M` (migrations) | yes → **+3** bonus · no → 0 |

---

## 5. Score and ordering

```
Score = 1.0·F + 1.0·L + 1.5·B + 1.0·W + 0.5·C + M_bonus
```

- `B` and `W` carry extra weight (1.5× and via the 1–5 layer weight) because "layers touched / how critical they are" is the core complexity signal — a change spanning api + core + migrations + frontend is far more conflict-prone than a same-line-count change confined to `frontend/`.
- `M_bonus` (+3) front-loads any worktree that adds migrations, since those must land before others rebase onto them.

**Order worktrees by `Score` descending.** Deterministic tie-breakers, in order:
1. has migrations (`M`) first,
2. higher `W`,
3. higher `L`,
4. higher `F`,
5. worktree directory name, alphabetical (final deterministic fallback).

> Optional qualitative nudge: if two worktrees land within **1.0** of each other, you may glance at their diffs to break the tie by judgement (e.g. one is a mechanical rename of many files, the other is dense new logic) — but never skip the numeric score; it is the primary signal.

---

## 6. Worked example

Two candidate worktrees:

**`feat-attachments`** — `git diff --numstat master...feat-attachments` reports 18 files; Σ(added+deleted) = 1200; commits = 7. Touched paths fall in `backend/api/`, `backend/core/`, `backend/database/migrations/versions/`, `frontend/`, `backend/tests/`.
- `F` = 18 → **4**
- `L` = 1200 → **4**
- layers = {api, core, migrations, frontend, tests} ⇒ `B` = 5 → **4**; `W` = max(4,4,5,3,2) = **5**
- `C` = 7 → **3**
- `M` = yes → **+3**
- `Score` = 1·4 + 1·4 + 1.5·4 + 1·5 + 0.5·3 + 3 = 4 + 4 + 6 + 5 + 1.5 + 3 = **23.5**

**`fix-docs-typo`** — 2 files; Σ = 12; commits = 1. Touched paths: `docs/features/lupa.md`, `repository_guide.md`.
- `F` = 2 → **1**
- `L` = 12 → **1**
- layers = {docs, root-config} ⇒ `B` = 2 → **2**; `W` = max(1,3) = **3**
- `C` = 1 → **1**
- `M` = no → **0**
- `Score` = 1·1 + 1·1 + 1.5·2 + 1·3 + 0.5·1 + 0 = 1 + 1 + 3 + 3 + 0.5 = **8.5**

Result: `feat-attachments` (23.5) ships **before** `fix-docs-typo` (8.5).
