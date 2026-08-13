---
name: ship
description: "Ship a feature from a worktree: commit, push, create PR, merge to master, clean up the worktree and branches, then rebase all remaining worktrees with conflict resolution. Use when user says \"ship\", \"ship it\", \"merge this feature\", \"send to master\", or wants to finalize a worktree and sync the rest. Pass the literal TODO as the first argument (/ship TODO) to batch-ship every active worktree sequentially, ordered most-to-least complex via complexity-rubric.md."
model: opus
effort: max
allowed-tools: Bash, Read, Edit, Grep, Glob, Agent, AskUserQuestion
user-invocable: true
argument-hint: "<worktree-dir> [exclude...]  |  TODO [exclude...] — ship one worktree, or TODO to ship all worktrees most-to-least complex"
---

# Ship — Full Worktree Shipping Workflow

This skill finalizes a feature developed in a git worktree: commits, pushes, creates a PR, merges it, cleans up, and rebases every other active worktree so they stay in sync with master.

**Invocation**: Must be run from the main repository directory (master branch). The full argument string is: $ARGUMENTS. The **first** whitespace-separated token is the worktree directory to ship (referred to as `<worktree-to-ship>` throughout this skill); **every token after it** names a worktree to **exclude** from the Phase 3/4 rebase (the **exclusion list**). Examples: `/ship feature-name` (ship and rebase ALL remaining worktrees) · `/ship feature-name wt-a wt-b` (ship, then rebase all remaining worktrees except `wt-a` and `wt-b`).

The main repository working tree is always the first entry of `git worktree list`; worktrees are its sibling directories named directly after the feature (e.g., `feature-name`, `fix-bug-123`), with no repo-name prefix. The default branch is **master**. GitHub CLI (`gh`) is available.

---

## Mode selection — read this first

Look at the first whitespace-separated token of `$ARGUMENTS` (call it `$0`):

- **`$0` is `TODO`** (case-insensitive) → **batch mode**. Do NOT follow the single-worktree phases below; instead read and follow [mode-todo.md](mode-todo.md). Every token after `TODO` is a worktree directory to exclude from the batch. The batch ordering is scored via [complexity-rubric.md](complexity-rubric.md).
- **Otherwise** → **single-worktree mode** (the default, described below). `$0` is the worktree directory to ship; any remaining tokens are worktrees to exclude from the Phase 4 rebase. Continue with Phase 0.

---

## Phase 0 — Argument Parsing & Validation

### 0.1 Extract arguments

Split the argument string on whitespace:

- **First token** → `<worktree-to-ship>`: the worktree directory to ship.
- **Remaining tokens** (if any) → the **exclusion list**: worktrees to skip during the Phase 4 rebase. It is applied silently in Phase 3 — exclusions are NEVER asked interactively; this list is the only mechanism.

If the argument string is empty or blank, **STOP** and tell the user:
> "Please provide the worktree directory name as an argument. Example: `/ship feature-name` — optionally followed by worktrees to exclude from the rebase: `/ship feature-name wt-a wt-b`"

### 0.2 Validate execution context

Verify we are in the main repo, not inside a worktree:

```bash
git rev-parse --git-dir
git rev-parse --git-common-dir
```

If the two values differ, we are inside a worktree. **STOP** with message:
> "Run /ship from the main repository directory, not from inside a worktree."

### 0.3 Derive and validate worktree path

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE_PATH="$(dirname "$REPO_ROOT")/<worktree-to-ship>"
```

Validate against `git worktree list --porcelain` — **always the porcelain form, never the human-readable `git worktree list`**. It emits one record per worktree with labelled fields (`worktree <path>`, `HEAD <sha>`, `branch refs/heads/<branch>`) plus the bare markers `bare`, `detached`, `locked [reason]` and `prunable [reason]`, records separated by blank lines. Paths are read whole even when they contain spaces, and the markers below exist only here. Keep the output — Phase 3.1 reuses it.

1. The directory exists: `test -d "$WORKTREE_PATH"`
2. A record in the porcelain output has exactly that path.
3. That record is **not** `prunable` (a `prunable` record points at a directory git can no longer find — the registration is stale and there is nothing to ship).
4. That record does **not** live under `$REPO_ROOT/.claude/worktrees/`. Those are Claude Code's own worktrees (subagents with `isolation: worktree`, background sessions, `claude --worktree`, desktop parallel sessions) — short-lived, with no remote branch, no Podman stack and no PowerShell shortcut. Shipping one is never what the user meant.

If any of those four fails, **STOP** with message:
> "Worktree directory '<worktree-to-ship>' not found or is not a valid git worktree."

Then one more check, which gets its own message because the cause and the fix are different: the record must **not** carry the `locked` marker. A lock is a deliberate "hands off" signal — set by hand with `git worktree lock`, or by Claude Code while a subagent works inside. **STOP** rather than override it:

> "El worktree '<worktree-to-ship>' está bloqueado (razón: <reason>). No lo desbloqueo por mi cuenta. Si de verdad quieres shipearlo, ejecuta `git worktree unlock \"<worktree-path>\"` y vuelve a lanzar /ship."

Stopping here is safe: nothing has been merged or deleted yet. Never reach for the double `-f` that `git worktree remove` suggests for locked worktrees — it could destroy a worktree an agent is using right now. Claude Code holds the same line: its periodic sweep releases locks *it* set for dead sessions, but never one you set yourself.

### 0.4 Get the branch name

```bash
git -C "$WORKTREE_PATH" branch --show-current
```

Store the result as `<branch-name>`.

**Output to chat:**
> Shipping worktree: `<worktree-path>` (branch: `<branch-name>`)

---

## Phase 1 — Commit + Push + PR

### 1.1 Check for uncommitted changes

Run `git -C <worktree-path> status` and `git -C <worktree-path> diff` (including `--cached`).

- **If there are changes**: analyze the diffs (and any implementation context already in the conversation window) to understand what was built. Draft a concise commit title (imperative mood, max 72 chars) and a short body explaining the "why". Stage relevant files (explicit names, never `git add -A`, never stage `.env` or credentials), commit using a HEREDOC, then push:
  ```bash
  git -C <worktree-path> push -u origin <branch>
  ```
  All staging and commit commands must use `git -C <worktree-path>`.
- **If there are NO changes** (everything was already committed and pushed incrementally): skip straight to PR creation.

### 1.2 Create or reuse the Pull Request

Before creating a new PR, check whether one already exists for this exact branch — a previous `/ship` run may have aborted between PR creation and merge, in which case `gh pr create` would fail with `a pull request for branch "<branch-name>" already exists`.

```bash
gh pr list --head <branch-name> --state open --json number,url
```

- **If the output contains a PR for `<branch-name>`**: reuse it. Capture its URL/number and skip the `gh pr create` call. The `--head <branch-name>` filter must match the branch being shipped exactly — never auto-pick up an open PR for any other branch.
- **If the output is empty**: create a new PR:

  ```bash
  gh pr create --base master --head <branch-name> --title "<title>" --body "<description>"
  ```

  The `--head <branch-name>` flag is required because we are on master, not on the feature branch.

Check the PR for merge conflicts:

```bash
gh pr view <branch-name> --json mergeable
```

- **If `mergeable` is `CONFLICTING`**: **STOP IMMEDIATELY**. Tell the user there is a conflict in the PR, explain which files conflict and why. This should never happen because worktrees are created from a rebased state — if it does, it signals a critical issue that needs manual investigation. Do NOT continue.
- **If clean**: continue.

**Output to chat:**
> Commit + Push + PR ready: <PR-URL>  *(created | reused existing)*

---

## Phase 2 — Merge + Pull + Cleanup

The cleanup order matters: **the worktree must be removed before the local branch can be deleted, and the local branch must be deleted before the remote branch is removed**. Otherwise `git branch -D` errors with "branch in use by worktree" and that error masks the remote deletion (so passing `--delete-branch` to `gh pr merge` ends up doing neither). The steps below enforce that order explicitly.

### 2.1 Merge the PR

```bash
gh pr merge <branch-name> --squash
```

**Do NOT pass `--delete-branch` here.** It would try to delete the local branch first, which fails because the worktree still has it checked out, and that masks the remote deletion. Branch deletion (local then remote) happens in 2.4 and 2.5 below, in the correct order.

If the merge fails for any reason, **STOP IMMEDIATELY** and notify the user with the error details.

**Output to chat:**
> Merge completed without conflicts.

### 2.2 Update local master

We are already in the main repository directory. Pull the latest master with `--ff-only` so an unexpectedly divergent local master fails loudly instead of producing a silent merge commit:

```bash
git pull --ff-only origin master
```

If `--ff-only` rejects (local master has commits not on origin/master, which should never happen in this workflow), **STOP IMMEDIATELY** and report the divergence to the user — do not auto-resolve.

### 2.3 Remove the worktree

The worktree no longer needs to exist. First clean up its Podman stack (idempotent — does nothing if no stack was ever started), then remove the git registration and the directory:

#### 2.3.0 Clean up the Podman stack of the shipped worktree

Compute `<branch-name-lc>` by lowercasing `<branch-name>` (Podman Compose project names only allow lowercase + digits + dash). Then run:

```bash
podman compose --project-name mailmanager-<branch-name-lc> down -v --remove-orphans
```

Idempotent: if no containers/volumes/network exist for that project (the worktree was never started, or already cleaned up), it returns `0` with no error. If anything was up, it brings containers down, removes them, deletes the `mailmanager-<branch-name-lc>_mailmanager_pgdata` volume, deletes the `mailmanager-<branch-name-lc>_default` network, and releases bind mounts to the worktree directory.

This step is **mandatory before `git worktree remove`** because:
1. If the postgres container of the worktree were still up, its bind mount to the worktree directory would hold a file lock that makes `git worktree remove --force` fail and trigger the costly Locked Directory Recovery unnecessarily.
2. Without it, the volume and any stopped containers would survive the ship as orphans in Podman.

**Output to chat:**
> Stack Podman cleanup for `mailmanager-<branch-name-lc>`: done (idempotent).

#### 2.3.1 Remove the git worktree

```bash
git worktree remove <worktree-path> --force
```

`git worktree remove --force` does two things at once: it deletes the directory contents AND removes the registration from `.git/worktrees/<id>` (`<id>` is usually the directory name, but a worktree that went through `/cambiar-nombre-worktree` keeps its original one — `git worktree move` renames the directory, not the registration; it makes no difference here because git resolves by path). `--force` is required because these worktrees are always dirty: `/creacion-worktree` copies gitignored files into them.

One `-f` covers "dirty" but **not** "locked" — on a locked worktree git fails with `fatal: cannot remove a locked working tree` and asks for a second `-f`. **Never add it.** Phase 0.3 already stopped the ship for locked worktrees, so reaching this point with one means something changed mid-run; report it rather than forcing.

On Windows, when the directory cannot be deleted because some process holds an open handle, git will still print a "Permission denied" / "failed to delete" error — but the registration is usually removed anyway, leaving an orphan empty directory on disk. Verify with `git worktree list --porcelain`: the path must be gone entirely, not merely reduced to a `prunable` record.

If the directory is gone, jump to **2.4**.

If the directory is still on disk, also try:

```bash
rm -rf <worktree-path>
```

⚠️ **Before running `rm -rf`, delete reparse points first.** A recursive delete can follow an NTFS junction or a directory symlink and wipe the **target's** contents outside the worktree — `node_modules` is the usual carrier. `git worktree remove` is immune to this (it is also what Claude Code does since 2.1.205: removing a worktree deletes only the link and keeps the folder it points to), so this manual fallback is the one place that needs the guard:

```powershell
Get-ChildItem -Path "<worktree-path>" -Recurse -Force -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint } |
  ForEach-Object { [System.IO.Directory]::Delete($_.FullName, $false) }
```

`[System.IO.Directory]::Delete($path, $false)` removes the link entry without following it (`$false` means non-recursive). If a link resists, do **not** fall back to a recursive delete on it — report it and treat the removal as failed.

If `rm -rf` fails too with `Device or resource busy` / `Permission denied`, follow the **Locked Directory Recovery** procedure below before continuing.

#### Locked Directory Recovery (Windows)

Some other process has an open handle on the worktree path — typically its current working directory (CWD) — which prevents Windows from deleting the directory entry even though it is empty. The order below has been tried and verified to work; follow it exactly.

**Step 1 — Identify the processes whose CWD is inside the worktree.**

Sysinternals `handle.exe` is not assumed to be installed. Use the PowerShell snippet below (it reads each running process' PEB via `NtQueryInformationProcess` to extract the CWD — the only reliable way on Windows without third-party tools):

```powershell
$ErrorActionPreference = 'Stop'
$peb = @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class PebReader {
  [DllImport("ntdll.dll")] public static extern int NtQueryInformationProcess(IntPtr h, int c, ref PROCESS_BASIC_INFORMATION pbi, int len, out int rl);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, IntPtr buf, int sz, out int read);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(int access, bool inh, int pid);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct PROCESS_BASIC_INFORMATION { public IntPtr R1; public IntPtr PebBase; public IntPtr R2a; public IntPtr R2b; public IntPtr Pid; public IntPtr R3; }
  public static string GetCwd(int pid) {
    IntPtr h = OpenProcess(0x0410, false, pid); if (h == IntPtr.Zero) return null;
    try {
      var pbi = new PROCESS_BASIC_INFORMATION(); int rl;
      if (NtQueryInformationProcess(h, 0, ref pbi, Marshal.SizeOf(pbi), out rl) != 0 || pbi.PebBase == IntPtr.Zero) return null;
      byte[] buf = new byte[8]; int read;
      var gch = GCHandle.Alloc(buf, GCHandleType.Pinned);
      try { if (!ReadProcessMemory(h, (IntPtr)((long)pbi.PebBase + 0x20), gch.AddrOfPinnedObject(), 8, out read)) return null; } finally { gch.Free(); }
      IntPtr pp = (IntPtr)BitConverter.ToInt64(buf, 0); if (pp == IntPtr.Zero) return null;
      byte[] uni = new byte[16]; gch = GCHandle.Alloc(uni, GCHandleType.Pinned);
      try { if (!ReadProcessMemory(h, (IntPtr)((long)pp + 0x38), gch.AddrOfPinnedObject(), 16, out read)) return null; } finally { gch.Free(); }
      short uLen = BitConverter.ToInt16(uni, 0); IntPtr uBuf = (IntPtr)BitConverter.ToInt64(uni, 8);
      if (uLen <= 0 || uBuf == IntPtr.Zero) return null;
      byte[] s = new byte[uLen]; gch = GCHandle.Alloc(s, GCHandleType.Pinned);
      try { if (!ReadProcessMemory(h, uBuf, gch.AddrOfPinnedObject(), uLen, out read)) return null; } finally { gch.Free(); }
      return Encoding.Unicode.GetString(s);
    } finally { CloseHandle(h); }
  }
}
'@
Add-Type -TypeDefinition $peb -Language CSharp
$target = "<WORKTREE_PATH_WITH_BACKSLASHES>"
foreach ($p in (Get-Process)) {
  try {
    $cwd = [PebReader]::GetCwd($p.Id)
    if ($cwd -and $cwd -like "*$target*") {
      $ci = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue
      [PSCustomObject]@{ PID=$p.Id; Name=$p.Name; CWD=$cwd; Cmd=$ci.CommandLine }
    }
  } catch {}
} | Format-List
```

**Step 2 — Kill the lockers.** The lockers are almost always orphan MCP servers from previous Claude Code sessions launched in this worktree (typically `glance-mcp` from the `npx → cmd → glance-mcp.mjs` tree, several per session).

| Process pattern | Action |
|---|---|
| `glance-mcp` (any process whose name matches `glance-mcp` and whose CWD is inside the worktree) | Kill with `Stop-Process -Id <pid> -Force` |
| Anything else | Kill with `Stop-Process -Id <pid> -Force` AND notify the user in chat with one line: `Killed unexpected locker: PID=<pid> Name=<name> Cmd=<cmdline>`. Novel offenders are worth surfacing so future runs can predict them. |

**Step 3 — Delete the orphan directory.**

```bash
rmdir "<WORKTREE_PATH>"
```

Now that no process holds the CWD handle, this succeeds.

**Output to chat:**
> Worktree directory locked. Killed N orphan MCP processes, deleted directory.

### 2.4 Delete the local branch

Now that the worktree is gone, the branch is no longer "in use" and can be deleted:

```bash
git branch -D <branch-name>
```

Then prune any stale worktree refs:

```bash
git worktree prune
```

### 2.5 Delete the remote branch

```bash
git push origin --delete <branch-name>
```

Verify:

```bash
git ls-remote --heads origin "refs/heads/<branch-name>"
```

- **Empty output**: deletion confirmed.
- **Branch still listed**: **WARN** the user that the remote branch could not be deleted, but continue to the next phase.

**Output to chat:**
> Remote branch `<branch-name>`: deleted (verified) | WARNING: could not delete

### 2.6 Remove PowerShell navigation shortcut

The `/creacion-worktree` skill adds a navigation function to the PowerShell profile when creating a worktree. That function must be removed now that the worktree no longer exists.

- **Resolve the profile path dynamically** — never hardcode it. The actual path depends on the Windows user account and can be redirected by OneDrive:
  ```bash
  powershell -NoProfile -Command "Write-Output \$PROFILE"
  ```
  Capture the output as `<profile-path>`.
- Read `<profile-path>` with the Read tool.
- The worktree directory name is `<worktree-to-ship>` (the first argument passed to `/ship`).
- Find the 3-line function block matching the worktree directory name:
  ```powershell
  function <worktree-directory-name> {
      Set-Location "<path>"
  }
  ```
- **If found**: use Edit to remove the entire function block (all 3 lines, plus any trailing blank line so no double blanks remain).
- **If NOT found**: skip silently — the function may not exist if the worktree was created before this convention.

**Output to chat:**
> Cleanup done: worktree removed, local branch deleted, remote branch deleted, PowerShell shortcut removed.

---

## Phase 3 — Worktree Selection (argument-driven, never interactive)

### 3.1 List remaining worktrees

```bash
git worktree list --porcelain
```

Drop the **first** record: git guarantees it is always the main working tree. Do not identify it by branch name — a main repo checked out on a feature branch would slip through that test.

Then drop these three categories from the rebase set, reporting each one:

1. **Claude Code-managed worktrees** — any record under `$REPO_ROOT/.claude/worktrees/` (see Phase 0.3). Rebasing one would rewrite a temporary branch another agent may be working in right now, and Claude Code removes them itself in its periodic sweep.
   > Saltado (worktree de Claude Code): `<path>`
2. **`prunable` records** — the directory is gone, there is nothing to rebase. Suggest `git worktree prune` and continue.
   > Saltado (prunable — el directorio ya no existe): `<path>`
3. **`locked` records** — locked deliberately, or left locked by a killed session. Leave it alone and surface the reason.
   > Saltado (bloqueado: `<reason>`): `<path>`

### 3.2 Apply the exclusion list from the arguments

**Do NOT ask the user anything in this phase.** The default behavior is always to rebase **ALL** remaining worktrees. The only way to exclude a worktree from the rebase is the exclusion list the user passed as extra arguments when invoking the skill (Phase 0.1) — if the user wanted to exclude one, they already said so at invocation time. Never re-confirm, never offer an exclusion prompt.

- **Exclusion list empty** (the common case): proceed with every remaining worktree.
- **Exclusion list non-empty**: remove from the rebase set every worktree whose directory name matches a token in the exclusion list. For each excluded worktree, output to chat:
  > Excluido del rebase (por argumento): `<excluded-worktree>` (branch: `<branch-name>`)
- **Token with no matching worktree** (typo, already-removed worktree, or the shipped worktree itself): **WARN** in chat and continue — never stop for this:
  > Argumento de exclusión '<token>' no coincide con ningún worktree activo — ignorado.

If there are NO remaining worktrees (none exist, or all were excluded), skip to the final summary and inform the user that everything is clean.

---

## Phase 4 — Rebase Remaining Worktrees

### 4.1 Check for uncommitted changes

Before launching any rebase subagent, inspect each non-excluded worktree for uncommitted changes:

```bash
git -C <worktree-path> status --porcelain
```

- **If all worktrees are clean**: continue directly to 4.2.
- **If any worktree has uncommitted changes**: use `AskUserQuestion` to ask:

> "Los siguientes worktrees tienen cambios sin commitear ni pushear:\n\n{list with name and branch of each dirty worktree}\n\n¿Quieres que haga commit + push de cada uno antes de iniciar el rebase + resolución de conflictos?"

With exactly two options:
1. "Sí, commit + push antes de rebasear"
2. "No, excluir esos worktrees del rebase"

**If the user chooses option 1 (commit + push):**
For each dirty worktree, invoke the `/commit-push` skill in the context of that worktree (using `git -C <worktree-path>` for all git commands within that worktree). All commits and pushes must complete before launching any rebase subagents.

**If the user chooses option 2 (exclude):**
Remove those worktrees from the rebase list and output to chat for each one:
> Excluido del rebase: `<worktree-name>` (branch: `<branch-name>`) — tiene cambios sin commitear.

**Rationale**: The rebase + conflict resolution process only operates on committed code. WIP changes that are not committed are invisible to the rebase and cannot be checked against incoming conflicts, which may cause silent data loss or conflicts.

### 4.2 Launch subagents

For each non-excluded worktree, launch a `rebase-conflicts-solver` subagent **in parallel** (all in one message with multiple Agent tool calls). Each subagent receives:

- The absolute path to the worktree directory
- The branch name checked out in that worktree
- The name of the base branch: `master`

**Output to chat:**
> Subagents launched for worktrees: {list of worktree names}

### 4.3 Collect results

Wait for all subagents to complete. Each returns a structured conflict report.

### 4.4 Sync local worktree directories

After all subagents complete, the remote branches are updated but local worktree directories may not reflect the rebased state. For each worktree where the rebase reported **SUCCESS**:

```bash
git -C <worktree-path> fetch origin <branch-name>
git -C <worktree-path> pull --ff-only origin <branch-name>
```

`--ff-only` succeeds in the happy path (the rebase subagent left the worktree on a state that is a strict ancestor of `origin/<branch-name>`) and **fails loudly** when there is unexpected divergence — for example, a commit made locally in the worktree between the subagent's push and this step, or a file edited outside the normal flow. On rejection, do **NOT** fall back to `git reset --hard`:

1. Report the affected worktree with the exact `git pull` error.
2. Leave the local state intact (no automatic recovery).
3. Continue with the remaining worktrees — one divergent worktree must not block the others.

**Skip** worktrees that reported **MANUAL_INTERVENTION_NEEDED** — their local state was already restored by `git rebase --abort` in the subagent, so they remain on their pre-rebase commit (which is correct and safe).

**Why `--ff-only` and not `--hard`**: the project's parent `CLAUDE.md` forbids `git reset --hard` in any automated workflow because it silently destroys uncommitted edits and local commits made outside the normal flow. `--ff-only` reaches the same desired state (local matches remote) when nothing unexpected happened, and surfaces the anomaly when something did — without any data loss.

**Output to chat (per worktree):**
> Synced local directory: `<worktree-path>` (branch: `<branch-name>`)  *or*
> Divergence detected, left intact: `<worktree-path>` — <error>; please reconcile manually.

### 4.5 Policy on the Podman volume of the rebased worktrees

This skill **does not touch** the postgres volumes of the rebased worktrees. If the rebase modified migration files (Type 4 — Migration Chain Fork — auto-resolved by the subagent renumbering `0033_X` → `0034_X`, or Type 2 modifying an existing migration), the `alembic_version` stored in the worktree's postgres volume can be out of sync with the rebased code.

**Visible consequence for the user**: the next time the user starts the stack of that worktree, the backend will run `alembic upgrade head` and fail with `Can't locate revision identified by '<old-id>'`.

**Recommended manual remediation when this occurs**:
```bash
cd <rebased-worktree>
podman compose down -v             # deletes the worktree's postgres volume
podman compose up -d                # bring up again; the old seed.sql is re-applied,
                                    # then alembic migrates up to the current head
```

If the user wants the latest master state (not the master state at the moment the worktree was created), they can regenerate the seed by running `/eliminar-worktree <name>` + `/creacion-worktree <name>` (master postgres up). That is a more radical alternative that loses any local code changes in the worktree — rarely necessary.

This policy is documented here so the user knows what to do when they see the error; the skill itself performs no automatic action on rebased worktrees' volumes.

---

## Phase 5 — Final Summary

After all phases complete, output a structured final summary to chat. This is **in addition to** the interim "Output to chat" messages shown during earlier phases — those keep the user informed in real time, and this summary provides a complete record at the end. The summary must list every concrete action taken (or explicitly skipped), organized by phase.

### Output format:

```markdown
## Ship Summary

### Phase 0 — Validation
- Worktree: `<worktree-path>` (branch: `<branch-name>`)

### Phase 1 — Commit + Push + PR
- Commit: `<commit-title>` | No uncommitted changes (skipped)
- Push: `origin/<branch-name>` | Already up to date (skipped)
- PR created: <PR-URL>
- Merge check: MERGEABLE

### Phase 2 — Merge + Pull + Cleanup
- Squash merge: completed
- Pull: `git pull --ff-only origin master` — local master updated to `<short-hash>`
- Podman stack cleanup: `compose down -v` for `mailmanager-<branch-name-lc>` — done (idempotent)
- Worktree removed: `<worktree-path>` | required Locked Directory Recovery (killed N glance-mcp + cleanly restarted PostgreSQL) | failed (<reason>)
- Local branch `<branch-name>`: deleted
- `git worktree prune`: done
- Remote branch `<branch-name>`: deleted (verified) | WARNING: could not delete
- PowerShell shortcut: removed | not found (skipped)

### Phase 3 — Worktree Selection
- Remaining worktrees: <N> | None (nothing to rebase)
- Excluded (via invocation arguments): none | <list of excluded names>
- Skipped (Claude Code worktrees under `.claude/worktrees/`): none | <list>
- Skipped (prunable / locked): none | <list with the marker and its reason>
- Ignored exclusion tokens (no matching worktree): <list> (omit this line if none)

### Phase 4 — Rebase (only if worktrees exist)
#### <worktree-name> (`<branch>`)
- **Status**: SUCCESS | MANUAL INTERVENTION NEEDED
- **Conflicts**: None | <N> resolved
- **Local sync**: `git pull --ff-only origin <branch>` completed | divergence — left intact (<error>) | skipped (manual intervention)
(If conflicts were resolved, include a detail table:)
| File | Type | Details |
|------|------|---------|
| path/to/file.py | Type 1 (Additive) | Both branches added code to different sections |
| path/to/other.py | Type 2 (Combinatorial) | Merged logic in `function_name`: branch A added X, branch B added Y, combined as Z |
```

### Rules for the summary:
- Every action from every phase must appear — nothing omitted.
- If a step was skipped, say so explicitly with the reason (e.g., "No uncommitted changes (skipped)").
- If a step failed, say so explicitly with the error (e.g., "failed (Device or resource busy)").
- If Locked Directory Recovery ran, list every unexpected (non-glance-mcp, non-PostgreSQL) process that was killed — the user wants to track novel offenders.
- Phase 4 is only shown if there were worktrees to rebase. If none, Phase 3 ends with "None (nothing to rebase)" and Phase 4 is omitted entirely.
- If any worktree needs manual intervention, highlight it at the very top of the summary before Phase 0.

### Conflict types reference (for Phase 4 detail tables):
- **Type 1 (Additive)**: Both branches add code to the same file but in different sections or functions. Resolution is straightforward — keep both additions. Low risk.
- **Type 2 (Combinatorial)**: Both branches modify the same function or method. Resolution requires understanding both intents and combining the logic into one coherent implementation. Higher risk — the summary explains exactly what was done so the user can verify.
- **Type 3 (Semantic Duplication)**: Both branches independently created or modified code that serves the same or very similar purpose. Detected post-rebase by performing a **full-diff comparison** of everything each branch changed since they diverged, **across every language and every file** (Python, TypeScript/TSX, JavaScript, SQL, Markdown, configuration, migrations, etc. — not just Python). Covers same-file duplicates, same-name cross-file duplicates, different-name semantic equivalents (e.g., both branches created a helper to query the same DB table or a React hook wrapping the same endpoint under different names), and **intra-function duplication** (similar blocks of logic injected into pre-existing functions on each side, invisible to a definition-level scan). Auto-fixed when safe; flagged for manual intervention when logic diverges. This pass runs only after Step 3 has resolved all textual conflicts — correctness first, efficiency second.
- **Type 4 (Migration Chain Fork)**: Two Alembic migration files share the same `down_revision`, forking the migration chain. Invisible during development — only fails when `alembic upgrade head` runs in deployment. Auto-fixed for linear chains (renumbered); manual intervention for complex chains.
- **Type 5 (Unknown)**: The conflict does not fit any recognized type (e.g., delete/modify, rename/rename, binary). The agent aborted the rebase and provided analysis + proposed resolution without executing it. **Always requires manual intervention**.

---

## Error Handling

- **PR conflict**: Stop immediately, explain, do not merge.
- **Merge failure**: Stop immediately, explain the error.
- **Cleanup failure**: Warn but continue to the rebase phase (cleanup can be retried).
- **Rebase subagent failure**: Report which worktree failed and why. The other worktrees' results are still valid.
- **No worktrees remaining**: Report success and that there is nothing to rebase.

## Important Rules

- Never force push (`--force` / `-f`) unless the user explicitly asks.
- Never skip hooks (`--no-verify`).
- Never stage `.env`, credentials, or secret files.
- Must be invoked from the main repository directory (master branch), never from inside a worktree.
- The worktree to ship is the **first** argument (the directory name, e.g., `feature-name`); any **additional** arguments name worktrees to exclude from the Phase 4 rebase.
- Worktree exclusion is decided exclusively by the invocation arguments — Phase 3 must never prompt the user about exclusions. The three automatic skips (Claude Code worktrees, `prunable`, `locked`) are not exclusions the user chooses; they are records that cannot be shipped or rebased at all, and they are always reported.
- Never ship or rebase a worktree under `.claude/worktrees/` — those belong to Claude Code, not to `/creacion-worktree`.
- Never run a recursive delete on a worktree directory without first removing its reparse points (Phase 2.3.1) — it can wipe the target of an NTFS junction outside the worktree.
- Always enumerate worktrees with `git worktree list --porcelain`; the human-readable form loses the `locked` / `prunable` markers and makes paths with spaces ambiguous.
- The worktree path is derived as: `<parent-of-repo-root>/<worktree-to-ship>`.
- All git operations targeting the main repo can omit `git -C` since we are already in the main directory.
- All git operations targeting the worktree must use `git -C <worktree-path>`.
- This skill never starts or stops Podman containers other than via `compose down -v` (an idempotent cleanup, not a process start). All stack lifecycle for master and worktrees is the user's responsibility.
