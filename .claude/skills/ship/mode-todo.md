# Ship — Batch Mode (`/ship TODO`)

You reached this file because the first argument to `/ship` was `TODO` (case-insensitive). This is **batch mode**: ship **every** active worktree that has something to ship, one after another, ordered **most → least complex**, until master contains them all and no shippable worktree is left.

This file owns only the **batch orchestration**. The per-worktree mechanics (commit, push, PR, merge, cleanup, Locked Directory Recovery) are **not duplicated here** — they are the phases of `SKILL.md`, referenced by name. Complexity scoring lives in [complexity-rubric.md](complexity-rubric.md).

**Argument shape:** `TODO [exclude...]` — `$0` is `TODO`; every remaining token is a worktree directory name to **exclude** from the batch (same exclusion semantics as single mode). Example: `/ship TODO V4 V6` ships all worktrees except `V4` and `V6`.

---

## Why "most complex first" (do not reorder)

Each worktree is rebased onto the master that already contains the worktrees shipped before it. Shipping the **most complex** worktree first means it never has to be rebased onto anyone else's changes (it lands clean); only the **simpler** worktrees — cheap and safe to rebase — absorb the rebases. Reversing the order would force the largest change to rebase on top of everything already merged: the most painful, conflict-prone case.

---

## Phase T0 — Validation & argument parsing

1. **Execution context** — reuse `SKILL.md` § Phase 0.2 verbatim: run `git rev-parse --git-dir` and `git rev-parse --git-common-dir`; if they differ you are inside a worktree → **STOP**:
   > "Ejecuta /ship TODO desde el directorio principal del repo, no desde dentro de un worktree."
2. **Parse arguments:** `$0` = `TODO`. Remaining whitespace-separated tokens → the **exclusion list**.
3. `REPO_ROOT=$(git rev-parse --show-toplevel)` and `PARENT=$(dirname "$REPO_ROOT")` — worktrees are siblings under `PARENT`.

---

## Phase T1 — Discover & score worktrees

### T1.1 List candidate worktrees

```bash
git worktree list --porcelain
```

Use the porcelain form for the same reasons as `SKILL.md` § 0.3: labelled fields, unambiguous paths, and the `locked` / `prunable` markers that the human-readable output drops entirely.

The **first record** is always the main repo — **drop it** (git guarantees the order; never identify it by branch name).

From the rest, drop the three unshippable categories first, reporting each one — they are not user exclusions, they simply cannot be shipped:
> Saltado (worktree de Claude Code): `<path>` — bajo `.claude/worktrees/`, lo gestiona Claude Code
> Saltado (prunable — el directorio ya no existe): `<path>`
> Saltado (bloqueado: `<reason>`): `<path>`

Then drop any whose directory name matches a token in the exclusion list. For each excluded match, emit:
> Excluido del batch (por argumento): `<name>` (branch: `<branch>`)

For an exclusion token matching no active worktree, **WARN and continue** (never stop):
> Argumento de exclusión '<token>' no coincide con ningún worktree activo — ignorado.

### T1.2 Split "nothing to ship" from "shippable"

For each remaining worktree (path `<wt>`, branch `<branch>` via `git -C <wt> branch --show-current`):

```bash
git -C <wt> rev-list --count master..<branch>   # commits ahead of master
git -C <wt> status --porcelain                   # working-tree dirtiness
```

- If commits ahead == 0 **and** `status --porcelain` is empty → **nothing to ship**. Record it as `⏭️ saltado (nada que shipear)` and exclude it from the cascade.
- Otherwise it is **shippable** → score it.

If **no shippable worktrees remain**, skip to Phase T4 and report that everything is already in sync — nothing to do.

### T1.3 Score and order

For each shippable worktree, collect the metrics and compute `Score` exactly as defined in [complexity-rubric.md](complexity-rubric.md) (§1–§5). Order the worktrees by `Score` descending, applying the rubric's deterministic tie-breakers.

---

## Phase T2 — Show the plan and confirm once (D4)

Print the ordered scoring table to chat:

```markdown
## Ship TODO — orden por complejidad

| # | Worktree | Branch | Archivos (F) | Líneas (L) | Capas (B/W) | Migr (M) | Commits (C) | Score |
|---|----------|--------|-------------|-----------|-------------|----------|-------------|-------|
| 1 | <name>   | <br>   | 18          | 1200      | 5 / 5       | sí       | 7           | 23.5  |
| … |          |        |             |           |             |          |             |       |

Saltados (nada que shipear): <list or "ninguno">
Excluidos (por argumento): <list or "ninguno">
```

Then ask for a **single** confirmation with `AskUserQuestion` (this is the only interactive step of batch mode):

> "Voy a shipear estos <N> worktrees a master en este orden (de más a menos complejo), rebaseando cada uno justo antes de su turno. Es una operación masiva e irreversible (merge squash + borrado de cada worktree). ¿Procedo?"

Exactly two options:
1. "Sí, shipear los <N> en este orden"
2. "Cancelar"

- **Cancelar** (or `Other` that means stop) → go straight to Phase T4 and report that nothing was shipped.
- **Sí** → proceed to Phase T3.

---

## Phase T3 — Cascade (sequential)

Process the shippable worktrees **strictly in scored order, one at a time** (NOT in parallel — each worktree must rebase onto the master produced by the previous ship). For each worktree `<wt>` (branch `<branch>`), announce `▶ [i/N] Shipeando '<name>' (score <S>)…` and run the steps below **in this exact order**.

> The order differs from single mode on purpose: **commit before rebase**. A dirty working tree blocks `git rebase`, so any auto-commit (D3) must happen first, then the just-in-time rebase, then PR + merge.

### T3.1 Auto-commit if dirty (D3) — `SKILL.md` § 1.1

If `git -C <wt> status --porcelain` is non-empty: analyze the diffs, draft a concise commit (imperative title ≤72 chars + short "why" body), stage explicit files (never `git add -A`, never `.env`/credentials), commit via HEREDOC, then `git -C <wt> push -u origin <branch>`. If clean, skip. (This is exactly `SKILL.md` Phase 1.1 applied to `<wt>`.)

### T3.2 Just-in-time rebase if needed (D1)

Check whether master already lives in this branch (refs are shared across worktrees, so local `master` is fresh after each prior ship's `pull --ff-only`):

```bash
git -C <wt> merge-base --is-ancestor master <branch>
```

- **Exit 0** (master is an ancestor of `<branch>`) → branch already contains master → **no rebase needed**, skip to T3.3. (Typical for the first/most-complex worktree when master has not advanced.)
- **Exit 1** (master has commits not in `<branch>`, e.g. the squash commits of worktrees shipped earlier in this batch) → **rebase needed**:
  1. Launch **one** `rebase-conflicts-solver` subagent for this worktree (absolute path, branch name, base branch `master`). Do this **alone**, not batched with others.
  2. On `MANUAL_INTERVENTION_NEEDED` (Type 5, or diverging Type 3) → **best-effort skip (D2)**: record `⚠️ saltado (conflicto manual)`, leave the worktree on its pre-rebase commit (the subagent already ran `git rebase --abort`), emit a chat note, and **continue with the next worktree**. Do NOT ship it.
  3. On **SUCCESS** → sync the local worktree directory with the rebased remote (reuse `SKILL.md` § 4.4): `git -C <wt> fetch origin <branch>` then `git -C <wt> pull --ff-only origin <branch>`. If `--ff-only` is rejected (unexpected divergence) → record the worktree as `⚠️ saltado (divergencia local)`, leave it intact (never `git reset --hard`), and continue with the next worktree.

### T3.3 PR + merge + cleanup — `SKILL.md` § 1.2 → § 2.6

Run, against `<branch>` / `<wt>`, the per-worktree shipping steps of `SKILL.md` in order:
- § 1.2 — create or **reuse** the PR (`gh pr list --head <branch> --state open` first), then check `mergeable`. If `CONFLICTING` → **best-effort skip (D2)**: record `⚠️ saltado (PR en conflicto)` and continue with the next worktree (do not merge). After a successful just-in-time rebase this should not happen; if it does, it signals something the rebase could not reconcile.
- § 2.1 — `gh pr merge <branch> --squash` (no `--delete-branch`). A merge failure here is a **critical infrastructure failure** → **STOP the whole batch** and report (do not touch remaining worktrees).
- § 2.2 — `git pull --ff-only origin master` in the main repo. Rejection → **STOP the whole batch** (master diverged unexpectedly).
- § 2.3 — Podman stack cleanup (`compose down -v`) then `git worktree remove --force` (+ Locked Directory Recovery if the directory is locked).
- § 2.4 / § 2.5 — delete local branch, `git worktree prune`, delete remote branch (verify).
- § 2.6 — remove the PowerShell navigation shortcut for `<name>`.

Record the worktree as `✅ shipeado` with its commit title, PR URL, and any conflicts the subagent resolved. Emit `✅ [i/N] '<name>' shipeado.` and move to the next worktree.

> After this worktree merges, the shared local `master` advances, so the next worktree's T3.2 check will correctly detect it needs a rebase.

---

## Phase T4 — Final summary

Print one consolidated table covering **every** input worktree:

```markdown
## Ship TODO — Resumen

| Orden | Worktree | Branch | Score | Desenlace | Detalle |
|-------|----------|--------|-------|-----------|---------|
| 1 | <name> | <br> | 23.5 | ✅ shipeado | PR <url>; conflictos: <none/N resueltos> |
| 2 | <name> | <br> | 8.5  | ⚠️ saltado (conflicto manual) | requiere rebase manual |
| — | <name> | <br> | —    | ⏭️ saltado (nada que shipear) | rama == master, sin cambios |
| — | <name> | <br> | —    | ⏭️ excluido (argumento) | |
```

Outcome legend: `✅ shipeado` · `⏭️ saltado (nada que shipear)` · `⏭️ excluido (argumento)` · `⏭️ saltado (worktree de Claude Code)` · `⏭️ saltado (prunable)` · `⏭️ saltado (bloqueado)` · `⚠️ saltado (conflicto manual)` · `⚠️ saltado (PR en conflicto)` · `⚠️ saltado (divergencia local)` · `❌ batch detenido (fallo crítico)`.

Close with the count `X/N shipeados`. If the batch was stopped early by a critical failure, highlight it at the very top, name the worktree and the error, and list which worktrees were never reached. For any worktree left needing manual intervention, restate the `rebase-conflicts-solver` analysis so the user can act.

---

## Important rules (batch mode)

- **Sequential, never parallel.** Each ship advances master; the next worktree must rebase onto that. (This is the opposite of single mode's Phase 4, which rebases the *remaining* worktrees in parallel because none of them is being merged.)
- **Best-effort per worktree (D2)** for rebase / PR-conflict / local-divergence failures: skip that worktree, keep going. **Fail-fast (STOP)** only for critical infrastructure failures of the main repo (merge failure, master `--ff-only` rejection) — never leave master half-merged.
- **Never force push, never `--no-verify`, never `git reset --hard`, never stage `.env`/credentials** — same hard rules as `SKILL.md`.
- **One rebase subagent at a time** — launched only for worktrees whose T3.2 check says a rebase is needed.
- Reuse `SKILL.md`'s exact commands for every per-worktree step; this file only sequences them.
