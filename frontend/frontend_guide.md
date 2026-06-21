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

### 1.2 `useConnectedAccounts` fetches with `useEffect` + `useState` (features §4.1 exception)

`frontend/src/features/accounts/hooks/useConnectedAccounts.ts` drives the account list, the per-account email previews, the create→connect→sync→preview sequence, and the reconnect re-auth of a previously-connected account whose token died, with a hand-rolled `useEffect` + `useState` machine instead of `useQuery` / `useMutation`, which `features/CLAUDE.md` §4.1 otherwise requires.

The exception is deliberate: the page runs a multi-step per-account flow with a `syncing` / `ready` / `error` tri-state per row that does not map onto a single query/mutation. The accepted trade-off is that this hook holds its data OUTSIDE the TanStack Query cache — it does NOT share the `['accounts', mailboxId]` cache `useEmailList` populates, so adding or removing an account here does not auto-invalidate the email listings (and vice versa). The trigger to migrate it to `useQuery` / `useQueries` is the first time that cross-hook cache coherence actually matters.

### 1.3 `components/ui/` imports **types only** from `api/` (components §5 clarification)

`components/CLAUDE.md` §5 forbids `components/ui/` from importing `api/endpoints/`, and the dependency graph in `frontend/CLAUDE.md` §3 draws `components/ → lib/` with no edge to `api/`. Several `ui/` widgets (`ComposeOverlay`, `AttachmentSendFailedDialog`) nonetheless import **type-only** symbols — `UiError` from `api/client/errors`, `AccountOut` / `FailedAttachmentDetail` from `api/types/dto`. This is allowed and intentional: they are `import type` references erased at compile time, so they create zero runtime coupling and cannot trigger a network call (the thing the boundary protects against). The hard line stays at `api/endpoints/` and `api/client/http` — importing a *value* (a function, the `request` client) from `api/` into a component is still forbidden. If a shared type proliferates, promote it to `lib/types.ts` rather than widening this exception.

### 1.4 `useDebounce` lives in `lib/hooks/`, not in the `emails` feature

`useDebounce` (`frontend/src/lib/hooks/useDebounce.ts`) is generic and domain-agnostic, but its home matters: it was moved out of `features/emails/hooks/` because the recipient-autocomplete hook lives in `features/drafts/` and `features/CLAUDE.md` §6 forbids `drafts` importing from `emails` with no exception. The inbox search pages (`features/emails/pages/`) and the composer both consume it, so the only legal shared home is `lib/hooks/` (alongside `useSelection`). The rationale is invisible at either call site — a future hook that needs debouncing must import from `lib/hooks/`, never re-add a feature-local copy.

The recipient-autocomplete data hook (`useRecipientSuggestions`) is invoked by `DraftComposerHost`, riding the **same** §1.1 host exception (the composer's `RecipientAutocompleteInput` lives in `components/ui/` and only receives `suggestions` by props). Its **silent-degradation** contract is an invariant whose regression would slip through review: any error (including a 401) collapses to an empty list — the hook never surfaces a blocking error, so the composer keeps accepting manual typing and the send path is never blocked by a suggestions failure. A change that starts propagating that error to the UI breaks the "purely additive, never blocks" guarantee.

### 1.5 Feature pages/hooks consume context-reader hooks from `app/providers/` (features §8 clarification)

`features/<x>/pages/` and `hooks/` import the context-reader hooks `useAuth` (`app/providers/AuthContext`) and `useDraftComposerContext` (`app/providers/DraftComposerContext`). The `features/CLAUDE.md` §8 import table enumerates `own hooks/`, `own components/`, `components/`, `lib/` for `pages/` but neither lists nor forbids `app/`. This is intentional, not a boundary break: the dependency arrow `app/ → features/` (`frontend/CLAUDE.md` §3) governs who imports whose *modules*; a feature **reading a cross-cutting React Context** mounted above it is the idiomatic inverse the provider pattern relies on, and the sanctioned counterpart to the §1.1 host bridge (which exists precisely so `app/providers/` never imports `features/`). Only context-reader hooks cross — nothing forbidden (`api/client/http`, another feature). A reviewer seeing `FavoritesPage` / the inbox pages import `useDraftComposerContext` must not read it as a §8 violation.

### 1.6 `useComposerAttachments` writes without `useMutation` (features §4.1 exception)

`frontend/src/features/drafts/hooks/useComposerAttachments.ts` calls the attachment endpoints directly inside `useCallback`s instead of `useMutation` — the same §4.1 deviation as §1.2, deliberate for the same reason: per-chip upload progress, `AbortController` cancellation, and the D-07 lazy-push lifecycle do not map onto a single `useMutation`. The architecture-compliance reviewer flags this on every run; it is an accepted exception, not a regression.

### 1.7 `I18nProvider` lives in `lib/i18n/`, not in `app/providers/`

The interface-language provider (`frontend/src/lib/i18n/I18nProvider.tsx`) is mounted in `app/providers/Providers.tsx` like the auth/query providers, but its **code** lives under `lib/i18n/` on purpose: `components/ui/` widgets (`AccountCard`, `MailboxDropdown`, `Sidebar`, …) call `useTranslation`, and `components/` may not import from `app/` (`frontend/CLAUDE.md` §3). `lib/` is the only layer every higher layer — `app/`, `features/`, AND `components/` — is allowed to import from, so it is the sole legal home for a provider that the whole tree (widgets included) must reach. This is a deliberate placement, not a `lib/`-purity break: `lib/CLAUDE.md` §3.1 permits documented impure helpers, and the language detect/persist functions (`detect.ts`) read/write `localStorage` for exactly that reason. A future move of the provider into `app/providers/` would silently break every `components/ui/` import of `useTranslation`.

### 1.8 `AuthProvider` imports from `api/endpoints/auth` (app §8 exception)

`frontend/src/app/providers/AuthProvider.tsx` imports the auth endpoint wrappers (`loginWithGoogle`, `loginWithMicrosoft`, `devLogin`, `getMe`, `logout`, `deleteMe`) directly from `api/endpoints/auth`. The `app/CLAUDE.md` §8 import table lists only `api/client/errors`, `lib/`, and pinned third-party providers for `providers/`, so `api/endpoints/` is not enumerated.

The exception is deliberate: `AuthProvider` is the session-bootstrap provider, and the session calls cannot be delegated to a feature hook — `features/` may not hold global auth state (`frontend/CLAUDE.md` §5), and moving them into a feature would invert the `app/ → features/` arrow. Keeping the auth endpoint calls in the provider is the only home that satisfies every rule, and is the symmetric counterpart to the §1.1 host bridge (which exists so `app/providers/` never imports `features/`). The alternative — a thin re-export at the `api/` boundary — was considered and rejected as indirection without benefit. The architecture-compliance reviewer flags this on every run; it is an accepted exception, not a regression.

## 2. TanStack Query key namespaces

All cache keys follow `[<resource>, <scope>, ...<filters>]`. The seven namespaces in active use:

- `['emails', mailboxId, box, accountId?, q?, favorite?, groupByThread, page]` — regular mailbox listings (`useEmailList`). `groupByThread` is a non-nullable boolean (it goes straight into the key, no `?? null`); it namespaces the grouped conversation cache apart from the ungrouped (favourites) cache so the two never collide.
- `['virtual-mailbox-emails', virtualMailboxId, q?, page]` — listings produced by a virtual mailbox (`useVirtualMailboxEmails`).
- `['virtual-mailbox', virtualMailboxId]` — a single virtual mailbox record (`useVirtualMailbox`).
- `['virtual-mailboxes']` — the list of every virtual mailbox owned by the user (`useVirtualMailboxes`).
- `['contact-suggestions', q]` — recipient autocomplete results (`useRecipientSuggestions`). The second slot is `null` (not the query) while gating is off (`q` under 2 chars), so the disabled state caches under a stable key instead of churning one entry per keystroke prefix.
- `['conversation', mailboxId, accountId, providerMessageId]` — the message chain of an opened conversation (`useConversation`), invalidated + optimistically rewritten via the bare `['conversation']` prefix by `useFavorite` because `is_favorite` renders per-message inside the viewer.
- `['drafts', mailboxId, accountId ?? null]` — drafts listing (`useDraftsList`); the `accountId` third slot (`?? null`) scopes a single account's drafts, while the bare `['drafts', mailboxId]` prefix invalidates all accounts at once.
- `['accounts', mailboxId]`, `['mailboxes']` — straightforward resource listings.

When a mutation can affect emails across several mailboxes (favourites toggle, bulk move-to-trash, bulk mark-as-spam, favourites sync, metadata sync, **"sync everything"** in `useSyncAll`, **mailbox delete** in `useDeleteMailbox`) the invalidation uses the bare prefix `['emails']` (no `mailboxId` scope) plus `['virtual-mailbox-emails']`. The wider blast radius is the price of correctness: a vmbox can aggregate emails from several real mailboxes, so a scoped invalidation would silently leave stale rows in sibling caches. `useDeleteMailbox` widens it further — `['accounts']` (the removed mailbox's account listing) on top of the email prefixes and `['mailboxes']` — because the cascade also takes the mailbox's accounts and all their synced mail. `useRenameMailbox`, by contrast, touches **only** `['mailboxes']`: a rename changes no email row, so the broad email invalidation the delete needs would be wasted work here.

Asymmetry to keep: `useConnectedAccounts.editAccountLabel` (rename an account's label) invalidates **nothing**. That hook holds its accounts in `useState`, not the TanStack Query cache (§1.2), so there is no query key to refresh; it splices the updated `AccountOut` back into its `entries` array in place. A future migration of `useConnectedAccounts` to `useQuery` (the §1.2 trigger) must add an `['accounts', mailboxId]` invalidation here. The favourites toggle and favourites sync additionally invalidate `['conversation']` (and optimistically rewrite the open `ConversationOut`) because `is_favorite` is rendered per-message inside the conversation viewer, so an open thread would otherwise show a stale star after a toggle fired from a row; the bulk and metadata-sync mutations do not touch `['conversation']`.

The one exception is `useVirtualMailboxEmails`' **open-time sync** fan-out (one `sync-metadata` per aggregated account, see §3): it invalidates **only** `['virtual-mailbox-emails']`, NOT the bare `['emails']`. Narrower on purpose — its sole consumer is the vmbox listing, which never reads `['emails']`, so refreshing the regular inbox caches would be wasted work. Do not "align" it to the broader set above.

## 3. Cache-policy overrides

`useVirtualMailboxEmails` (`frontend/src/features/emails/hooks/useVirtualMailboxEmails.ts`) overrides the global TanStack Query defaults with `refetchOnMount: 'always'` and `staleTime: 0`. Virtual mailboxes are user-curated, time-sensitive views — the user expects newly arrived emails to surface the moment they re-open the view, not after the 30 s global staleTime expires. A regression that drops the override silently re-introduces the "stale for 30 s" behaviour the user explicitly asked to avoid.

This override only re-reads the **local** synced copy on re-open; it does not by itself pull genuinely new mail from the provider. That is the job of the **open-time sync** the same hook runs (the vmbox listing endpoint is local-only on the backend by design — the provider sync is orchestrated client-side here, never inside `virtual_mailboxes_service`; see `repository_guide.md`). On open the hook fans out one `sync-metadata` call per aggregated account, resolving each account's **real** `mailbox_id` from the already-loaded catalogue (a vmbox account can live under a different real mailbox than the route — the same per-mailbox fan-out trap as §5 / §8), and refreshes the listing when the fan-out settles. The two mechanisms are complementary, not redundant: the cache override re-shows local rows instantly, the fan-out brings the new ones down.

`useConversation` (`frontend/src/features/emails/hooks/useConversation.ts`) applies the **same** override (`refetchOnMount: 'always'` + `staleTime: 0`) for the same reason: a conversation is a time-sensitive view whose state the backend refreshes on every fetch, so re-opening the viewer must show the current state, not a 30 s stale snapshot. No other hook overrides the global cache policy; reach for a project-specific override only when the global defaults are demonstrably wrong for the surface in question.

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

No listing page hardcodes `isSent` any more (except `FavoritesPage`, which is `view='mixed'` so `isSent` is unused). `AccountInboxPage`, `UnifiedInboxPage` and `VirtualMailboxViewPage` all derive it because the lupa's `in:` operator can shift the **effective** box of every returned row server-side — so the columns must follow that box, not the route/saved one. The shared rule is `(parseInOperator(debouncedQ) ?? <fallback>) === 'SENT'`, where the fallback is the route `box` for the inbox pages and `record?.filter_payload?.box` for the vmbox page (a vmbox can be a saved SENT view). This is **cosmetic only**: the box sent to the backend is unchanged; the real override is applied server-side from `q`. `parseInOperator` (`lib/searchOperators.ts`) is a deliberate mirror of the backend's `_IN_VALUES` map — a divergence degrades a column at worst, never the result set, so it does not need to track the backend in lockstep. `SearchHelpPopover` (mounted next to every `SearchInput`) is a static operator cheat-sheet; its English operator syntax must stay in sync with the backend parser, but its Spanish copy and the help text are UI-only.

## 7. Server-side pagination across the email listings

Every email listing (unified, account, favourites, virtual, and search results) is server-paginated with a single fixed page size, `EMAILS_PAGE_SIZE` (`frontend/src/lib/constants.ts`). It lives in `lib/` — not in `api/` or a feature — precisely because both the `api/endpoints/` layer (which converts `page → limit`/`offset` on the wire) and the `features/` layer (`useEmailList`, `useVirtualMailboxEmails`, `EmailPagination`, `useBulkBar`) consume it, and `api/` may not import from `features/`. The page is a 1-based URL search param (`?page=`); `page=1` is encoded by **omitting** the param so the canonical first-page URL stays clean. Changing the active search term (`q`) must reset to page 1 in the **same** `setSearchParams` update that writes `q` (every listing page does `params.delete('page')` next to the `q` edit) — otherwise the URL would briefly point at a page that does not exist for the new filter.

Each listing hook returns the page rows plus `total` / `totalPages` / `isPlaceholder` and threads `placeholderData: keepPreviousData` into its query, so paging keeps the previous page visible (no spinner flash) while the next loads; `isPlaceholder` is what disables the pager buttons mid-flight. Each page also runs a clamp effect — `if (!loading && !isPlaceholder && page > totalPages) handlePageChange(totalPages)` — to re-home the user onto the last valid page after the total shrinks under them (background sync, bulk delete). The clamp is gated by `!loading && !isPlaceholder` so it never fights an in-flight fetch, and `totalPages` floors at 1 so an emptied box lands on page 1.

The pager and the `1–N de Z` range render **inside `EmailTable`'s sticky top bar** (range left, `EmailPagination` right) — `EmailTable` owns the range label, `EmailPagination` only the buttons — so paging never requires scrolling to the end of the 50-row page. That bar pins on scroll **only because `MailboxLayoutPage` makes the `Outlet` wrapper the scroll container** (`h-screen` shell + `overflow-auto` on the content div; the `Sidebar` stays put via its own `sticky h-screen`). Reverting that wrapper to `min-h-screen` / `overflow-x-auto` (its pre-pagination shape) hands the vertical scroll back to the document and **silently unsticks the pager on every listing** — nothing errors, the bar just stops pinning.

### `useBulkBar` keeps a selection `Map` that survives page changes

`useSelection` stores only keys in a `Set`, so it forgets the `EmailMetadataOut` objects of rows that scroll off the visible page. With server pagination a selection can span pages the table is no longer rendering, and the bulk actions need the full object of EVERY selected row — `mailbox_id` drives the per-mailbox HTTP fan-out (a vmbox can span real mailboxes) and the read/unread counts decide the toggle target. `useBulkBar` therefore wraps `toggle` / `toggleTopN` / `clear` to mirror every mutation into a parallel `Map<key, EmailMetadataOut>` (React state, not a ref, so the bar re-renders), and reads the selected set from the Map rather than from `selection.getSelected(visiblePage)`. The `Set` stays the source of truth for "is this selected" (the table consumes `isSelected` / `headerState` / `size`); the `Map` is the data store. A regression that reverts `useBulkBar` to deriving the selection from the visible `emails` array would silently drop every off-page selected row from a bulk action. Consumers MUST call `toggle` / `toggleTopN` / `clear` off the returned (wrapped) `selection`, never off the raw `useSelection` result, or the two fall out of sync. The bar clears the selection whenever the debounced search term changes (a same-page `q` change does not unmount the hook, so the Set/Map would otherwise survive into a filtered result that no longer shows those rows). For that cross-page selection to be reachable from the UI at all, the in-header pager stays visible **while the bulk bar is shown** (bulk bar on the left, pager on the right; `BulkActionsBar` is deliberately not `w-full` so it does not push the pager out) — hide the pager in selection mode and the `Map` can never accumulate rows from a second page.

## 8. Composer body is HTML — emptiness, dirty-state, and size invariants

The composed message `body` is HTML end to end (the rich-text composer, TipTap). Three cross-file invariants are not reconstructable from any single file:

1. **A "visually empty" editor never serialises to `''`.** TipTap emits `'<p></p>'` (or a stray `<br>` / `&nbsp;`) for an emptied document. `normalizeEmpty` (`lib/richText.ts`) collapses those to `''`; `htmlIsEmpty` reports whether the HTML has any visible text. Every emptiness / dirty check must route through them: `useComposerForm` compares snapshots with `normalizeEmpty(a.body) !== normalizeEmpty(b.body)` and derives `hasAnyContent` from `!htmlIsEmpty(body)`, and the silent-draft bootstrap asserts `body: ''`. A new code path that compares `form.body` against `''` or with `.trim()` will mis-fire the close-confirmation dialog (fires on an untouched composer) and the "Enviar borrador" dirty-skip (sends stale HTML or skips a real edit).

2. **`normalizeEmpty` / `htmlIsEmpty` live in `lib/`, not in `RichTextEditor`.** Both the component (`components/ui/RichTextEditor`) and `useComposerForm` (`features/drafts/hooks/`) need them, and a feature hook may not import from `components/ui/` (`features/CLAUDE.md` §8). Moving them into `RichTextEditor.tsx` as a "cleanup" silently breaks the `useComposerForm` import chain — they are domain-agnostic string helpers, so `lib/` is their only correct home.

3. **The client-side body-size gate is `BODY_MAX_CHARS = 1_000_000` in `useDraftComposer`.** It mirrors the backend's Pydantic `max_length` and is the REAL guard: the `.max()` on the Zod *request* schemas never runs at runtime (`request<T>()` validates responses only). `bodyError` gates all three actions (`canSendEmail`, `canSaveDraft`, `canSendDraft`) — not just send — because `handleSendEmail` reroutes through the draft-send path once a silent draft exists, so the size block must hold there too. Dropping the constant or the gating silently re-enables submitting an oversized body that only the backend rejects, as an opaque 422 that `toComposerError` (in `useDraftPersistence`) has to rewrite into a readable message.

## 9. Rate-limit (429) handling invariants

Two cross-file decisions about the backend's 429 are invisible at either call site:

1. `toUiError` (`api/client/errors.ts`) reads the countdown from `error.detail.retry_after` — the JSON **body** — not from the `Retry-After` header. That header is not on the CORS response safelist, so the cross-origin SPA cannot read it (the same trap as the `Content-Disposition` download filename in `repository_guide.md`); reaching for the header here would silently yield `undefined`.
2. The two consumers discriminate on **different** axes on purpose: `toUiError` branches on `error.code === 'rate_limit_exceeded'` (it only special-cases OUR throttle message), while `shouldRetryQuery` (`app/providers/queryRetry.ts`) branches on `error.status === 429` (suppress retrying ANY 429 — a retry only burns the limit faster). Do not "align" them — the message wants the semantic code, the retry guard wants the HTTP status.

## 10. i18n in tests — the jsdom default is English

`renderWithProviders` wraps every test in the real `I18nProvider`, which resolves its initial language from `localStorage['lang']` and otherwise from `navigator.language` — **English in jsdom**. So a spec that asserts on fixed Spanish copy must seed the stored language first via `pinTestLang()` (`src/test/i18nTestLang.ts`), which registers the `beforeEach`/`afterEach` to set and clear `localStorage['lang']` so the choice does not leak across files. Two non-obvious consequences: a UI string changed from a hardcoded literal to a `t('…')` key silently flips its rendered language under test unless the spec pins one; and the helper is deliberately **not** named `useTestLang` (it is not a React hook — a `use` prefix would trip `react-hooks/rules-of-hooks` when called at a module's top level).
