> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session that touches the frontend layer. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what hooks / components / pages do, paraphrases of file names, exhaustive prop / argument enumerations, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Cross-feature decisions whose rationale isn't visible at the call site, deliberate architectural exceptions to the `CLAUDE.md` rules, invariants whose silent regression would slip through review, and TanStack Query key namespaces or cache-policy overrides shared across hooks earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code or into `repository_guide.md`.** Staleness is worse than silence.

# Frontend Guide — Project-Specific (maintained by Claude)

This file is the project-specific complement to `frontend/CLAUDE.md` (and the per-subdirectory `CLAUDE.md` files under `src/`). It documents decisions that are project-specific and not derivable from the code alone. The `CLAUDE.md` files own the structural rules; this guide owns the exceptions, the namespaces, and the cross-feature contracts that the rules do not capture.

## 1. Deliberate exceptions to the layer rules

### 1.1 `DraftComposerHost` calls `useDraftComposer` (features §5.1 exception)

`frontend/src/features/drafts/components/DraftComposerHost.tsx` is a presentational component that calls `useDraftComposer(mailboxId)` — a hook that fetches data and issues HTTP calls. `features/CLAUDE.md` §5.1 says "components do not call hooks that fetch data".

The exception is intentional. The host's whole job is to bridge the drafts feature into the cross-cutting `DraftComposerContext` defined in `app/providers/DraftComposerProvider.tsx`. Without the host, either:

- `app/providers/` would have to import from `features/drafts/hooks/` (`app/CLAUDE.md` §8 forbids `providers/` importing from `features/`); or
- every page that mounts the composer would re-instantiate the hook locally, breaking the singleton composer the layout relies on.

The host preserves both invariants by absorbing the rule break in a single file under `features/drafts/components/`, registering its hook surface via `__register()` on mount and tearing it down on unmount. Any future redesign that makes the composer state-machine live under `app/providers/` would lift this exception; until then the host is the only place in the codebase where `features/<x>/components/` calls a fetching hook.

## 2. TanStack Query key namespaces

All cache keys follow `[<resource>, <scope>, ...<filters>]`. The five namespaces in active use:

- `['emails', mailboxId, box, accountId?, q?, favorite?]` — regular mailbox listings (`useEmailList`).
- `['virtual-mailbox-emails', virtualMailboxId, q?]` — listings produced by a virtual mailbox (`useVirtualMailboxEmails`).
- `['virtual-mailbox', virtualMailboxId]` — a single virtual mailbox record (`useVirtualMailbox`).
- `['virtual-mailboxes']` — the list of every virtual mailbox owned by the user (`useVirtualMailboxes`).
- `['drafts', mailboxId]`, `['accounts', mailboxId]`, `['mailboxes']` — straightforward resource listings.

When a mutation can affect emails across several mailboxes (favourites toggle, bulk move-to-trash, bulk mark-as-spam, favourites sync) the invalidation uses the bare prefix `['emails']` (no `mailboxId` scope) plus `['virtual-mailbox-emails']`. The wider blast radius is the price of correctness: a vmbox can aggregate emails from several real mailboxes, so a scoped invalidation would silently leave stale rows in sibling caches.

## 3. Cache-policy overrides

`useVirtualMailboxEmails` (`frontend/src/features/emails/hooks/useVirtualMailboxEmails.ts`) overrides the global TanStack Query defaults with `refetchOnMount: 'always'` and `staleTime: 0`. Virtual mailboxes are user-curated, time-sensitive views — the user expects newly arrived emails to surface the moment they re-open the view, not after the 30 s global staleTime expires. A regression that drops the override silently re-introduces the "stale for 30 s" behaviour the user explicitly asked to avoid.

No other hook overrides the global cache policy; reach for a project-specific override only when the global defaults are demonstrably wrong for the surface in question.

## 4. Virtual mailboxes live inside the `emails` feature

Virtual mailboxes (`VirtualMailboxesPage`, `VirtualMailboxViewPage`, `VirtualMailboxForm`, `useVirtualMailbox`, `useVirtualMailboxes`, `useVirtualMailboxEmails`, `useAccountPickerData`) are NOT a separate feature directory. They live under `frontend/src/features/emails/{pages,components,hooks}/` alongside the regular email pieces. Three reasons:

1. **Conceptually they are a filtered view over emails.** The same primitive that powers `FavoritesPage` (an `EmailTable` over a server-side filtered listing) powers a vmbox view. Splitting them into separate features forced cross-feature imports of `EmailTable`, `ViewerMount`, `SearchInput`, `useEmailViewer`, `useBulkBar`, `useDebounce`, and `useFavorite` — which `features/CLAUDE.md` §6 prohibits without exception.

2. **There is no architectural home for "domain-aware hooks shared by two features".** `lib/` only takes domain-agnostic helpers; `components/ui/` only takes presentational widgets; `features/` rules out cross-feature imports. Collapsing the boundary is the only fix that satisfies every rule simultaneously.

3. **`FavoritesPage` already follows this pattern.** Favourites is also a filtered email view and lives at `features/emails/pages/FavoritesPage.tsx`. Virtual mailboxes follow the same pattern.

A future rename of the feature directory (e.g. `features/emails/` → `features/inbox/`) would carry both regular and virtual mailbox pages together and would not change this guide's reasoning.

## 5. `useFavorite` has no `mailboxId` constructor argument

`useFavorite()` (`frontend/src/features/emails/hooks/useFavorite.ts`) is intentionally not parameterised by `mailboxId`. Both `toggle({ mailboxId, ... })` and `sync({ mailboxId })` receive the mailbox per call.

The reason is the virtual-mailbox view: a vmbox can aggregate accounts across several real mailboxes, and the backend endpoints (`PATCH /mailboxes/{id}/.../favorite` and `POST /mailboxes/{id}/favorites/sync`) validate that the account belongs to the URL's mailbox. A constructor-level `mailboxId` would silently bind every call to the route's mailbox (which is one of many), so a vmbox-wide sync would mishandle every email living in a different real mailbox. The per-call contract makes the trap structurally impossible — the caller must supply the email's real mailbox.

The cache invalidation cost of this design is that the hook can no longer invalidate `['emails', mailboxId]` (it would not know which mailbox the next caller is about to use). It invalidates the bare prefix `['emails']` instead, which touches every listing — same broad invalidation strategy used by `useEmailBulkActions` for the same reason.

## 6. `EmailTable` column matrix (`view`, `isSent`)

`EmailTable` resolves visible columns from `(view, isSent)`:

- `(individual, isSent=false)`: only **De** (the message's sender — the user's own address is constant across rows in an account-scoped listing).
- `(individual, isSent=true)`: only **Para** (the message's recipient).
- `(unified, *)`: both **Para** and **De**. There is **no** separate "Cuenta" column — the always-visible **Remitente** column already carries the account label that disambiguates which of the user's accounts received / sent each row.
- `(mixed, *)`: both **Para** and **De**, but the cell values are decided **per-row** from `email.box === 'SENT'` (`isSent` prop is ignored in this mode). Used by views that mix received and sent emails — currently only `FavoritesPage`. Without this mode, sent favourites degenerate to "De: <user's own account>" which is constant per row and unhelpful.

Every page that mounts `EmailTable` must decide both `view` and `isSent` explicitly. The prop `isSent` is mandatory even in `mixed` mode (where it is unused) to keep the contract uniform — the matrix decides which axes drive the resolution, not the existence of the prop.

`VirtualMailboxViewPage` is the **only** page that does not hardcode `isSent`: it derives it as `record?.filter_payload?.box === 'SENT'`, because a virtual mailbox can be a saved SENT view and the column sense must follow the saved filter rather than a fixed value.

## 7. Route map and mounting tree

Routes are declared in a single source — `frontend/src/app/routes/router.tsx`. The mailbox-scoped routes mount under `MailboxLayoutPage` and inherit `:mailboxId`:

```
/login                                                  → LoginPage
/                                                       → MailboxGatewayPage (auth required)
/create-mailbox                                         → CreateMailboxPage
/m/:mailboxId
  /accounts                                             → ConnectedAccountsPage (lazy)
  /inbox  /sent  /spam  /trash                          → UnifiedInboxPage (lazy, box prop)
  /drafts                                               → DraftsPage (lazy)
  /favorites                                            → FavoritesPage (lazy)
  /virtual-mailboxes                                    → VirtualMailboxesPage (lazy, from features/emails/pages)
  /virtual-mailboxes/:virtualMailboxId                  → VirtualMailboxViewPage (lazy, from features/emails/pages)
  /account/:accountId/{inbox,sent,spam,trash}           → AccountInboxPage (lazy, box prop)
  /account/:accountId/drafts                            → AccountDraftsPage (lazy)
```

Every non-boot-path page uses `React.lazy()`. The router never knows anything about the `features/` internals beyond the page module's existence — a page move (e.g. the virtual-mailbox collapse documented in §4) is a one-line router edit and zero changes elsewhere.
