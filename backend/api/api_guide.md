> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# API Layer Guide

> **General rules**: this layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Account deletion (`DELETE /auth/me`)

`DELETE /auth/me` requires `require_session`. After deleting the user row, PostgreSQL `CASCADE` takes care of every associated artefact (mailboxes, accounts, tokens, sessions); the service only clears the session cookie afterwards.

## Service Conventions

- **Ownership check first.** Every action scoped to a mailbox calls `ensure_mailbox_access(mailbox_id, user_id)` **before anything else**. It validates the mailbox exists and the authenticated user owns it, raising `MailboxNotFound` (404) or `Forbidden` (403). Skipping or reordering the call is an authorization bug.
- **Building provider clients.** Only via `build_manager_for_accounts(accounts)` — never instantiate `EmailClient` subclasses directly. The helper wraps `CoreError` and unexpected exceptions into `AccountMisconfigured` (400) so error handling is uniform across endpoints.
- **Secret wrapping.** Load credentials with `load_wrapped_app_credentials(provider)` / `load_wrapped_account_tokens(...)` (`pydantic.SecretStr`). Unwrap with `unwrap_secret()` only at the provider boundary.
- **Cookie management in the service, not the router.** Services that manage session cookies receive the `fastapi.Response` object from the router. Cookies are set/cleared in the service layer.
- **One endpoint per identity provider.** `POST /auth/google` is hardcoded to Google OIDC. When adding another identity provider, add a separate `POST /auth/<provider>` with its own schema and service function — **do not** create a generic endpoint with a `provider` parameter. Keeps each flow's validation and error translation isolated.

## Auth Context Sequence

When an endpoint must authenticate against a provider and then perform a provider call, the service function **must** follow this exact sequence. Skipping or reordering any step leads to silent token staleness, missing error surfacing, or unauthenticated provider calls. `drafts_service.create_draft` is the canonical reference implementation.

1. **Build the manager.** `manager = build_manager_for_accounts([account])` — wraps `EmailConfigError` into `AccountMisconfigured`.
2. **Load wrapped credentials and tokens.** `load_wrapped_app_credentials(provider)` + `load_wrapped_account_tokens(mailbox_id, account_id, provider)`. Tokens are unwrapped only at the provider boundary.
3. **Silent auth.** `updated_tokens = manager.authenticate_all_silent(auth_payloads)`. May refresh tokens in place.
4. **Persist refreshed tokens.** If `updated_tokens` is non-empty, call `account_store.upsert_tokens` for each. Any failure here must surface as the endpoint's primary error class (e.g. `DraftCreationError` for drafts, `EmailFetchError` for sync) — never let a token-persistence failure surface as a generic 500.
5. **Raise on silent auth errors.** `raise_on_silent_auth_errors(manager.get_last_errors(), fallback=<endpoint class>)` — turns per-client `EmailAuthError` into a single `AccountNotConnected` (409). **Call it again after the fetch** (`fetch_all_email_metadata`, `fetch_all_drafts`, …) — auth errors can also surface at fetch time and the same function handles both phases.
6. **Provider call.** Only now call `manager.send_email_from_account`, `manager.create_draft`, `manager.fetch_all_email_metadata`, etc. Wrap in `try / except CoreError / except Exception` per the layer `CLAUDE.md` §9.

## Traps and cross-file asymmetries

### `translate_connect_error` vs `translate_core_error`

For the interactive `/connect` flow, `EmailAuthError` maps to `AccountConnectAuthError` (**401**), not `AccountNotConnected` (409). Reason: a connect-time auth failure means the user's credentials are wrong, not that they need to call `/connect` again — 409 would create a retry loop on the same endpoint.

### Interactive connect is two-phase; the callback service function never raises

`POST /connect` only **starts** the flow (`start_account_connect` → authorization URL + single-use `state`); the actual token exchange happens when the provider redirects the user's browser to `GET /auth/{google|outlook}/callback`. Three deliberate asymmetries with the rest of the service layer:

1. **The callback endpoints have no session dependency.** The redirect comes from Google/Microsoft, not from our SPA; the single-use `state` token (issued by an authenticated start, stored in `api/services/oauth_pending.py` with the requester's `user_id`) is the proof of legitimacy. Do not "fix" them by adding `require_session`.
2. **`complete_account_connect` never raises.** The callback renders a human-facing HTML page (with a `postMessage` to the opener SPA), so the function converts every failure — including `ApiError`s from its own helpers — into `{"ok": False, "message"}` instead of letting the JSON error envelope reach a browser tab. It is the one service function exempt from the "services raise ApiError" rule.
3. **The pending registry is process-local.** It may hold live objects (Gmail's `Flow` with the PKCE verifier), so it cannot be serialized; a multi-worker deployment would break the flow silently (start lands on worker A, callback on worker B). The project runs a single uvicorn worker — revisit before changing that.
4. **The callback page is XSS-hardened — keep it so.** It embeds attacker-influenceable text (the provider's `error`) into an inline `<script>` payload. `complete_account_connect` never reflects the raw `error`/`error_description` (it logs them and returns a sanitised, fixed-vocabulary code), and `_render_callback_page` script-context-escapes the JSON (`<`/`>`/`&` → `\uXXXX`) and sets a restrictive CSP. Dropping either guard reintroduces reflected XSS on the backend origin.

Redirect URIs are provider-asymmetric: Gmail's comes from `GOOGLE_OAUTH_REDIRECT_URI` (default `http://localhost:8000/auth/google/callback`; Google "Desktop app" clients accept any localhost redirect without registration), Outlook's comes from the credentials JSON and **must exactly match the Azure app registration** (currently `http://localhost:8000/auth/outlook/callback`). Changing either callback route path breaks the corresponding provider silently.

`AccountOut.email_address` (persisted by `upsert_tokens` during the callback) is how the frontend learns the connection landed — the start response intentionally carries no email.

### Ghost email reconciliation runs only after a full (bootstrap) sync

`_reconcile_ghost_emails` runs inside `sync-metadata` only when `is_full_sync=True`. It diffs stored `provider_message_id`s against `sync_result.upserts`, verifies suspect IDs against the provider via `verify_message_existence`, and deletes the ones the provider no longer reports. Every step is in its own `except Exception` — the reconciliation is best-effort and cannot fail the sync endpoint.

### `sync-metadata` schedules a post-response content prefetch + purge (best-effort, off the response path)

`sync_email_metadata` takes `background_tasks: BackgroundTasks | None = None` **keyword-only**; the router injects it, direct callers (service tests, scripts) omit it. When present, the service registers `_run_content_prefetch_and_purge` to run AFTER the `SyncResultOut` is sent. The task (1) purges the synced accounts' expired cached bodies (`purge_expired_email_content`), then (2) prefetches, sequentially and message-by-message, the body+attachments of each account's recent-unread-uncached inbox mail (`_fetch_and_persist_email_content`, the same helper the cache-miss viewer uses — body-only would hide attachments forever on pre-cached mail). It reuses the manager **already authenticated** during the sync (no re-auth, no token re-persist in the seconds it runs). The whole task is best-effort: every failure is logged and swallowed so it can neither affect the already-sent response nor abort the remaining work. The targets are collected inside the per-account loop using the loop key directly — `label` IS the `account_label` (`_build_auth_context` keys `label_lookup` by it), so it is passed straight through; reconstructing `f"{mailbox_id}__{aid}"` is a bug. When `background_tasks is None` nothing is scheduled and the sync contract is identical to before. Rationale + figures (TTL, window, cap) in `repository_guide.md`.

### `manage_trash` — TRASH verification gate + split restore flow

1. **TRASH verification gate.** Before any provider call, all referenced emails are checked to be in TRASH via `get_trash_emails_by_ids`. A missing row raises `EmailNotInTrash` (**409**, not 404 — the email exists but is in the wrong state).
2. **Split restore by `previous_box`.** After `manager.restore_from_trash`, items with a known `previous_box` go to `restore_from_trash_batch` (SQL uses `COALESCE(previous_box, 'ALL_MAIL')`). Items with `previous_box = NULL` require a second provider round-trip (`manager.fetch_messages_metadata` on the new IDs) to discover the post-restore box, then `restore_from_trash_discovered_batch` with the discovered value.

### `move_to_trash` — provider may rewrite the ID

`manager.move_to_trash` returns a `{old_id: new_id}` map. Outlook assigns a new ID on move; Gmail keeps the same ID. The service always writes the new ID into `provider_message_id`, copies the current `box` into `previous_box`, and sets `box = 'TRASH'`. Do not assume ID stability across trash operations.

### Spam — `restore_from_spam` target box is `ALL_MAIL`, not `INBOX`

The `/restore-from-spam` endpoint writes `ALL_MAIL` into the `box` column, consistent with the box-mapping convention that anything not in a special folder defaults to `ALL_MAIL`. Outlook's `/move` Graph endpoint additionally returns a new message ID (captured via `SpamMoveResult` and persisted).

### `send_email` — fire-and-forget metadata persistence

After a successful send, the service tries to persist the sent email's metadata. If that write fails, the error is **logged and swallowed** — the send is already reported as successful because the user cares that the email left the outbox, not that we tracked it. Same pattern applies to the best-effort DB operations inside `send_draft` (draft row delete + metadata insert).

### Outlook drafts — `Prefer: IdType="ImmutableId"` must be repeated on every call

Outlook drafts are created with `Prefer: IdType="ImmutableId"` so the ID stays stable across state transitions. Every subsequent PATCH / DELETE / SEND for that draft **must re-send the header** — Graph does not remember it per-message. Drop it on any follow-up call and Graph interprets the path parameter as a transient ID and returns 404.

### Draft endpoints — `DraftNotFound` pre-check lives before the provider call

`update_draft`, `delete_draft` and `send_draft` all call `draft_store.get(provider_draft_id, account_id)` **before** touching the provider. A 404 from our own DB must not cost a Gmail/Outlook round trip. The `drafts` primary key is composite `(provider_draft_id, account_id)` — both path params are mandatory for any draft mutation.

### Draft send — `provider_message_id` asymmetry between providers

`DraftSendOut.provider_message_id` differs from the draft ID on Gmail (Gmail creates a new Message when sending a draft) and equals it on Outlook (ImmutableId). Callers, tests and DB assertions must not assume equality. Both clients retry transient failures up to 3 total attempts (`_SEND_DRAFT_MAX_ATTEMPTS = 3`, `_SEND_DRAFT_RETRY_DELAY = 1.0`).

### Send path goes through `send_draft_with_attachments` (NOT `send_draft`)

Even drafts with zero attachments hit `EmailManager.send_draft_with_attachments`. The bare `send_draft` legacy path on the manager exists for older internal callers but the service no longer uses it. Tests asserting failure modes for the send must inject `send_draft_with_attachments_exc` on `FakeEmailClient` — see `tests/unit/unit_guide.md` for the trap.

### `send_draft` D-27 success-path persistence (Outlook)

When the Outlook send completes successfully but only AFTER one or more attachment uploads succeeded, the core layer returns `(EmailMetadata, list[AttachmentUploadResult])`. The service stamps `provider_attachment_id` on the local rows for the **succeeded** entries before deleting the draft (best-effort), so a hypothetical resend of the same draft would skip the already-uploaded ones. Gmail's atomic send returns an empty `AttachmentUploadResult` list because it has no per-attachment intermediate state to persist.

The stamping uses `draft_attachment_store.batch_update_provider_attachment_ids(pairs)` (single round trip) rather than per-attachment `update_provider_attachment_id` calls. With D-03 capping at 25 attachments the round-trip difference is small, but the same helper is also used by `_persist_partial_upload_results` on the failure path (where the loop matters more for retry ergonomics) — keep both call sites on the batch helper to preserve the symmetry.

### Drafts sync — cap enforced in the client, not the service

`manager.fetch_all_drafts` caps each account at `_DRAFTS_MAX_TOTAL = 100` most-recent drafts. The cap lives inside `GmailClient._list_all_draft_ids` (paginated batch-of-100 skeleton with `GMAIL_BATCH_MAX_WORKERS` workers, default 5, and 4 retries per batch) and inside `OutlookClient.fetch_drafts` (`$top=100&$orderby=lastModifiedDateTime desc` + 4 retries per page). The service never filters — if the user has >100 drafts, the newest 100 win silently and no `truncated` flag is surfaced.

### Attachment endpoints — Provider-First exception

`POST .../drafts/{id}/attachments` and `DELETE .../drafts/{id}/attachments/{attachment_id}` write **only** to the local `draft_attachments` table. They do NOT call the provider. The push to Gmail/Outlook happens during `send_draft` via `EmailManager.send_draft_with_attachments`. This is the documented exception in the drafts surface to the Provider-First Rule; other Provider-First exceptions (Trash's `delete_messages`, the favourites toggle pre-check) live in the emails surface and are catalogued in `repository_guide.md` § Provider-First Rule. The rationale (Gmail's full-MIME-rebuild on every `drafts.update` plus Outlook's 4-concurrent-requests cap) lives in `docs/features/adjuntos.md` § 6.9 — do not "fix" the asymmetry by uploading per-attachment.

### `add_draft_attachment` — sanitised filename may differ from upload

`sanitize_filename` runs against the existing-file list to resolve collisions: `report.pdf` uploaded twice resolves to `report (1).pdf` in the response. The frontend must read `response.filename` rather than echoing the local upload's filename — they can disagree by design. Path separators and reserved characters are also rewritten in place; the response is the source of truth for what got persisted.

### `remove_draft_attachment` — ownership chain is Python, not SQL JOIN

Unlike `download_email_attachment` (single SQL JOIN over `email_attachments → email_metadata → accounts → mailboxes`), the draft-attachment delete checks ownership in three Python steps: `ensure_mailbox_access`, `account_store.get`, then `draft_attachment_store.get` whose row carries `(account_id, provider_draft_id)` we compare against the path params. The asymmetry is intentional — the draft tree is shorter (no email_metadata in between) and the per-call DB hits are cheap on the bounded composer surface. A foreign attachment_id (someone else's draft) collapses to 404 `draft_attachment_not_found` at the final step; do NOT add a 403 branch.

The two pre-delete lookup `except Exception` branches (account-lookup and draft-attachment-lookup) raise `AttachmentLookupError` (500), NOT `DraftDeleteError` — at this point no delete has been attempted, so reusing the draft-delete fallback would mislead the client about which step failed. `DraftDeleteError` stays reserved for the final `draft_attachment_store.delete` failure where the row was confirmed and the actual DELETE blew up.

A concurrent delete from another tab is treated as success, not 404. When `draft_attachment_store.delete` returns falsy (the row was already gone), the service logs at INFO and returns `{"status": "deleted"}` — the ownership step already proved the user was entitled to remove this row, so the duplicate request is idempotent. Do NOT add a `DraftAttachmentNotFound` raise on falsy-return; only the *initial* lookup-not-found raises 404.

### `attachments_routers.py` mounts two distinct routers

`email_attachments_router` (cookie-session auth via `require_session`) and `admin_router` (env-var token via `X-Admin-Token`) live in the same file but are intentionally separate FastAPI routers. The session router carries the user-facing endpoints (download, attach, remove); the admin router carries the manual TTL purge endpoint (D-30). Adding a new admin endpoint to the wrong router silently bypasses the env-var gate. The admin router is included into the app **without** a session dependency — see `app.py` for the wiring.

### `_collect_draft_attachment_inputs` — single round trip + concurrent-delete intent

The helper calls `draft_attachment_store.list_by_draft_with_blob` once (single round trip including the binaries). With D-03 capping drafts at 25 attachments the payload is bounded. A `DraftAttachmentNotFound` raised here would mean a CASCADE delete won the race against the send — the service does not retry; it surfaces 404 because the user explicitly removed the attachment elsewhere.

### `_draft_out_from_row` is the only correct constructor for `DraftOut`

Endpoints that build `DraftOut` directly from a row (skipping the helper) ALWAYS produce `attachments=[]` because the row only carries the field when it came from the listing query (which json_aggregates the children) — the helper handles both cases (single-draft fallback fetch vs list-time pre-aggregated). Adding a new endpoint that returns `DraftOut`? Use `_draft_out_from_row(row)`; otherwise the composer reopens existing drafts with no chips visible.

### `get_email_full_content` cache miss: persist BEFORE recompute is load-bearing

The cache-miss branch (extracted into the shared `_fetch_and_persist_email_content` helper, reused verbatim by the sync-time content prefetch) makes a **single** unified provider read — `manager.fetch_content_with_attachments` returns body + downloadable list + `cid_map` in one round trip (D4; the old two-call `fetch_email_content` + `list_message_attachments` path is gone). It then first upserts the discovered attachments into `email_attachments`, THEN calls `recompute_has_attachments`. Inverting the order leaves `has_attachments=false` because the COUNT(*) subquery runs against an empty table. Failing to call `recompute` at all leaves the listing icon stale until the next purge cycle. The body/attachment persistence inside the helper is best-effort (logged, never aborting) — the provider read already succeeded, so a DB hiccup must not turn a readable email into a 502; the unified read is the **single** hard provider-failure point (`EmailContentFetchError`/502).

`_persist_attachment_metadata` runs each `meta.filename` through `core.email.sanitize_filename` (imported directly from `core.email`, NOT re-exported via `services_helpers`) with per-email dedup (`existing=` accumulates the names already sanitised within the same message). This closes the asymmetry with draft attachments (`add_draft_attachment`), which always sanitised: received attachments now also get path-traversal / reserved-name neutralisation and ` (1)`, ` (2)` collision suffixes before persistence. The upsert SQL already carries `filename = EXCLUDED.filename`, so a later re-discovery (HTML cache miss) refreshes the row to the sanitised value with no SQL change.

### `enforce_multipart_size_limit` — fast 413 before body read

`enforce_multipart_size_limit` (in `routers_helpers.py`) is wired as `Depends` on the multipart upload endpoint and rejects any request whose `Content-Length` header reports more than 30 MB with `RequestTooLarge` (413 `request_too_large`) **before** Starlette buffers the body. The 30 MB cap is a hard cushion above D-01's 25 MB per-file cap to absorb multipart boundary + headers overhead. Requests that omit `Content-Length` (chunked transfer encoding) fall through; Starlette's per-request memory cap catches abuses there. The dependency is **not** a substitute for the per-attachment / cumulative checks inside the service — those run after the body is read and apply D-01 / D-02 / D-03 / D-04a granularly.

### Cache-aside attachment download — ownership chain in SQL

`attachments_service.download_email_attachment` proves ownership inside a single SQL query (`email_attachments → email_metadata → accounts → mailboxes`) instead of layering Python-level checks. A row that does not match the authenticated `user_id` collapses into "not found" — the service raises `AttachmentNotFound` (404), **not** `Forbidden` (403). This is intentional: leaking the existence of foreign attachments via UUID guessing is a known anti-pattern (D-22). When `unavailable_at IS NOT NULL` (stamped on a previous provider 404/410), the endpoint short-circuits with `AttachmentUnavailable` (404 `attachment_unavailable`) before hitting the provider — the row is permanently dead until something replaces it.

After the ownership JOIN the service runs an explicit `provider_message_id` cross-check: the path parameter must match `row["provider_message_id"]`. A mismatch raises `AttachmentNotFound` (404), not 403 — defence-in-depth against URL parameter manipulation where a valid `attachment_id` is paired with the wrong `provider_message_id` segment. The `attachment_id` UUID is unique enough on its own, but the explicit comparison closes the gap; do NOT remove or relax it.

`mark_unavailable` on the provider 404/410 path is double-soft-failed: both the `DatabaseError` and the generic `Exception` branches inside the stamp call swallow their failure (logged but never raised). The function always raises `AttachmentUnavailable` afterwards regardless of whether the stamp succeeded — the user-visible 404 must NOT be masked by a DB write hiccup. Do NOT propagate errors from inside `mark_unavailable`; its purpose is a TTL/back-pressure hint, not a correctness guarantee.

**CORS `expose_headers` is load-bearing for the download filename.** The endpoint emits the real filename on `Content-Disposition`, but that header is **not** on the CORS response safelist, so a cross-origin `fetch` (frontend at :5173, backend at :8000, no Vite proxy) reads it as `null` unless `create_app()` mounts `CORSMiddleware` with `expose_headers=["Content-Disposition", "Content-Length"]`. `allow_headers=["*"]` does NOT cover this — it governs request headers in the preflight, not exposed response headers. Dropping `Content-Disposition` from `expose_headers` silently regresses every attachment download to a name-less `attachment-<uuid>` fallback. `Content-Length` is already safelisted (listed only to keep the intent explicit).

### `translate_core_error` — `EmailAttachmentDownloadFailed` splits by `detail.reason`

The mapping list in `services_helpers._CORE_TO_API_MAP` is otherwise purely typed (one `CoreError` subclass → one `ApiError` subclass), but `EmailAttachmentDownloadFailed` is the single exception: the function inspects `detail['reason']` and routes `'forbidden'` to `AttachmentProviderForbidden` (502) and everything else to `AttachmentProviderUnavailable` (503). The classification mirrors D-17: 403 from the provider is "your scope/permissions changed, investigate" (502, do not auto-mark unavailable); persistent 5xx is "try again later" (503). The default mapping in the list points at `AttachmentProviderUnavailable` so a missing `reason` still resolves to a sensible status.

### `AttachmentSendFailed.detail` — D-27 partial-success contract

When Outlook fails mid-flight uploading attachments before the final `POST /messages/{id}/send`, the core layer raises `EmailAttachmentSendFailed` with `detail = {"failed_attachments": [...], "succeeded": [{"draft_attachment_id", "provider_attachment_id"}, ...]}`. `drafts_service._persist_partial_upload_results` reads the `succeeded` list and stamps each `provider_attachment_id` onto the local `draft_attachments` row **before** re-raising the translated `AttachmentSendFailed` (502). The next retry's call to `_collect_draft_attachment_inputs` then surfaces those rows with their `provider_attachment_id` populated, and the Outlook client skips them — that is the entire D-27 partial-success resume mechanism. Gmail's send is atomic, so its failure path leaves `succeeded` empty.

### `recompute_has_attachments` — only writer of the denormalised flag

`email_metadata.has_attachments` is updated **only** through `services_helpers.recompute_has_attachments`, which runs after the cache-miss branch of `get_email_full_content` upserts the discovered attachments. The underlying SQL (`UPDATE_HAS_ATTACHMENTS`) recomputes the flag from `COUNT(*) WHERE is_inline=false` against `email_attachments`, so the helper is idempotent. Do not write `has_attachments` from any other site (sync, trash, spam, drafts) — the B.lazy strategy depends on the flag being driven solely by viewer activity (see `docs/features/adjuntos.md` § 5).

### `get_email_full_content` — attachment list read on cache hit too

The endpoint reads `email_attachments` on **every** request, including cache hits. A previous TTL purge can wipe `email_attachment_blobs` while leaving the metadata rows intact; reading the list always keeps the response's `is_downloaded` flag honest. Skipping the read on cache hit would surface stale `is_downloaded=true` entries that 502 on click.

The cache-hit branch also refreshes the body cache's **sliding TTL** via `touch_email_content_last_accessed` (best-effort, soft-fail — a touch failure must not break the read). The touch is done **inline** in the service (a cheap UPDATE), NOT through `BackgroundTasks`, to keep framework types out of the service signature; it bumps **only** `last_accessed_at`, never `fetched_at` (see `repository_guide.md` for the E2E HIT assertion this protects). No touch is needed on cache miss — the upsert stamps `last_accessed_at = now()` itself.

### `copy_attachments_from_email` — 200 on partial failure, R-12 idempotency

`POST .../drafts/{pdid}/attachments/copy-from-email` always returns HTTP 200 even when individual attachments fail to copy. Per-row failures (provider 404/410, `unavailable_at` set at the source, blob lookup failure, D-02 / D-03 cap hit, already-copied) surface via the `skipped[]` array as structured `{filename, reason}` records — never as the endpoint's status code. Mid-flight errors that abort the whole batch (mailbox not found, ownership pre-check failure, source `email_metadata` missing) still raise their typed `ApiError` and reach the global status map. `AttachmentInsertError` is the **only** per-row exception that escalates to the response status (the row vanishing under us is a DB integrity issue, not a per-attachment failure).

Idempotency (R-12) is driven by `source_attachment_id` (migration 0030). A retried call with overlapping source ids skips the duplicates with `reason="already_copied"` instead of inserting twice. The Outlook branch short-circuits before the source attachment query because `createForward` already inherited everything server-side at draft creation; the returned `attachments[]` is the same chip list the composer already has, kept in the response so the frontend never branches on provider type at the call site.

The endpoint authenticates **two** provider clients: the draft's account (for the ownership pre-check) and the **source** account (which may belong to a different mailbox the same user owns). The source account is resolved via `account_store.get_by_id_for_user` — no mailbox id is read from the request body, so a foreign account collapses to 404 `account_not_found` uniformly (D-22 anti-leak via UUID guessing). Do not "optimise" by reading mailbox id from the request to fast-path the lookup — that re-opens the leakage.

### `get_reply_context` is mounted on `favorites_router`, not `emails_router`

`GET .../accounts/{aid}/emails/{pmid}/reply-context?action=reply|reply_all|forward` lives on `favorites_router` (the bare `/mailboxes/{mailbox_id}` prefix router), NOT `emails_router` — its URL shape would otherwise collide with the favourite toggle path. It is read-only and runs the Gmail triple-requirement coherence guard (`validate_reply_threading_coherence`) **only for `reply` / `reply_all`**; `forward` is excluded because the `Fwd:` subject legitimately diverges and Gmail does not require subject parity for forwards — running the guard unconditionally would 502 every Forward against a real Gmail thread. `action` is a `Literal` validated by the router, so an invalid value is a 422, never a 502. Like `set_favorite`, it runs an `email_metadata_store.exists` pre-check **before** authenticating any provider client — a missing base row collapses to 404 `email_not_found` without a provider round trip.

### `get_conversation` — read + lazy sync, mounted on `favorites_router`, NOT Provider-First

`GET .../accounts/{aid}/emails/{pmid}/conversation` lives on `favorites_router` for the same URL-shape reason as `reply-context`. It is identified by `provider_message_id`, **not** `thread_id`, because Outlook's `conversationId` is base64 (`/`+`=`) and would break a path segment — the service derives the thread from the base message row. It is **not** Provider-First (it only reads from the provider and completes the local copy, like `get_email_full_content` filling `email_content`), so it is NOT in the Provider-First exception catalogue.

Three things the call shape does not reveal:

- **The threadless (`thread_id=''`) short-circuit makes NO provider call.** The base row is read once via `email_metadata_store.get_metadata` (a missing row → 404 `email_not_found`, since the user clicked a listed row); when its `thread_id` is empty the service returns a one-message `ConversationOut` mapped straight from that row. The auth + `fetch_conversation` cascade runs only for real threads.
- **`ConversationOut.messages` is mapped from the provider's FRESH state, not a DB re-read** (`_conversation_message_to_out`) — for the **real-thread** path. The threadless short-circuit above is the exception: its single message is mapped from the DB row (`row_to_email_metadata_out`), so there `is_read` / `is_favorite` reflect the last synced state, not a fresh provider call. A message that moved box or was read out-of-band is reflected on this open even if the best-effort lazy sync that follows failed. `has_attachments` of every viewer message is **always `False`** (B.lazy — the clip appears once the body is opened via `get_email_full_content`); `is_favorite` IS faithful because `ConversationMessage` carries it.
- **`_lazy_sync_conversation` is best-effort and only affects the next LISTING, never this response.** It upserts the thread's messages (so reopening serves bodies from DB and the content pre-check passes) and re-applies the thread's favourite members in a **single** batch (`set_favorites_true_batch` — the shared upsert never touches `thread_id` / `is_favorite` / `has_attachments`). It is **one-directional**: it only ever sets `is_favorite=TRUE`; un-starring a message at the provider is NOT propagated here, only by the favourites toggle / `/favorites/sync`. Every step is swallowed on failure — a cache-fill hiccup must not abort the viewer. Frontend invalidates the listings afterwards to refresh thread counts/order; the backend does not.

`ConversationFetchError` (502, same family as `EmailContentFetchError` / `EmailReplyContextError`) is raised only for the service's own unexpected branches; a genuine provider `EmailExternalAPIError` surfaces as `ExternalAPIError` (502) via `translate_core_error`.

### `set_favorite` — existence pre-check before the provider call

The favourite toggle runs `email_metadata_store.exists` **before** the provider call: a missing row collapses to 404 `email_not_found` without spending a provider round trip (mirrors the `DraftNotFound` pre-check). A successful provider call followed by an `update_favorite` that touches zero rows (row deleted in the race window) also surfaces 404 — never a silent 200.

### Virtual mailboxes — default box exclusion, in-SQL dedupe, race policy

`_build_filter_args` excludes `TRASH`/`SPAM`/`DELETED` by default; a caller opts back into TRASH/SPAM by sending an explicit empty `box_not_in: []`, but `DELETED` can never be opted into. `DELETED` is a local hard-delete state with **no `FilterBox` membership** (the schema's `box`/`box_not_in` only accept `ALL_MAIL|SENT|SPAM|TRASH`), so a user cannot request it; the positive-box branch (`AND box = X`) excludes it by construction, but the negative `box_not_in` branch would leak it, so `DELETED` is appended to **every** `box_not_in` branch (default, custom override, and the `box_not_in: []` opt-in). `box` and `box_not_in` are mutually exclusive at the schema boundary (422 if both arrive). The lupa's `in:` operator is applied here **differently from the regular listing**: the regular `GET /emails` lets `in:` *override* the effective box (wins over the route box and the Favoritos `ALL_MAIL` anchor, clearing `box_not_in` so the box / box_not_in pair stays mutually exclusive), whereas the vmbox listing *intersects* `in:` with the scope the saved filter already defines and **short-circuits to an empty `EmailPageOut` before any DB call** when the requested box is incompatible — i.e. `in:` of a different box than the vmbox's pinned `box`, or `in:` of a box inside its `box_not_in` exclusion. The short-circuit reuses the empty-page pattern of the "no owned accounts" branch precisely so the service never hands both `box` and `box_not_in` to the repository. The `box_not_in: []` opt-in case carries only the always-injected `["DELETED"]` after sanitisation, which is truthy, so an `in:` on it enters the `elif box_not_in:` branch (not the `else`); `DELETED` is never a legal `in:` value (there is no `in:deleted`; `in:trash` maps to `TRASH`), so that lone `DELETED` entry can never block a legitimate `in:` and it still narrows to the requested box. The `is not None` guard on `box_not_in_value` in `_build_filter_args` remains load-bearing: it distinguishes the `box_not_in: []` opt-in (which must reach the repository as `["DELETED"]` — TRASH/SPAM included, only DELETED excluded) from the default (`["TRASH","SPAM","DELETED"]`). Swapping it for a truthiness test (`elif box_not_in_value:`) would collapse the empty list to the default exclusion and silently reverse the opt-in (TRASH/SPAM would vanish again). Ownership is re-validated on every read/update/delete AND on the `account_ids` of create/update — a foreign or missing id collapses to 404 (`virtual_mailbox_not_found` for the vmbox, `account_not_found` for an unowned account), never 403, to avoid leaking existence via UUID guessing. Race policy: when the ownership pre-check passes but the row vanishes before the mutating SQL, the repository returns `None` (update) / `False` (delete) and the service surfaces 404 — never a silent 200 on DELETE nor a generic 500 on UPDATE. The same-provider-message dedup (one provider account connected under two mailboxes surfaces the message twice) now runs **in SQL**, not in the service: the listing passes `distinct_provider_message_id=True` to BOTH `list_filtered` and `count_filtered` so the deduplicated page and the `COUNT(DISTINCT provider_message_id)` total agree (the old Python `_dedupe_rows_by_provider_message_id` is gone — deduping after `LIMIT`/`OFFSET` made pages short by a duplicate). The winner-selection tie-break (non-empty `to_email` > non-empty `to_name` > most recent `received_at`) moved verbatim into `LIST_FILTERED_DISTINCT`'s inner `ORDER BY` — see `database_guide.md`. Ownership of the stored `account_ids` is re-resolved through `_owned_account_ids`, which fetches every account the user owns in a **single** `account_store.list_account_ids_by_user` query (no per-mailbox fan-out) — keep it one query on the CRUD and on every listing read; a per-mailbox loop silently regresses to N+1.

### `contacts_service.suggest_contacts` — user-level, no ownership pre-check (by design)

`GET /contacts/suggestions` is the only read surface besides `/virtual-mailboxes` that is **not** mailbox-scoped: it carries no `mailbox_id` and the service runs **no** `ensure_mailbox_access` / per-id ownership check. That is correct, not an omission — the endpoint receives no client-supplied resource id to validate. Pertinence is guaranteed structurally: the candidate accounts come from `account_store.list_account_ids_by_user(user_id)`, so a user can only ever see addresses aggregated from accounts they own. A reviewer adding a `mailbox_id` path param or an ownership branch here would be re-introducing a check the design deliberately makes unnecessary. The two store calls (`list_account_ids_by_user`, then `list_recipient_suggestions`) each sit in their own `try` with `except DatabaseError → translate_database_error` (503) and `except Exception → RecipientSuggestionsError` (500); whitespace-only `q` and a user with zero accounts both short-circuit to `[]` **before** touching the email-metadata store.

### Listing endpoints return a paginated envelope (`EmailPageOut`), not a bare list

`GET /emails` and `GET /virtual-mailboxes/{id}/emails` wrap the page in `EmailPageOut` (`items` + `total` + `limit` + `offset`). `total` is the exact size of the WHOLE filtered set (same `box` / `q` / `favorite` / accounts) in the local synced copy — never the provider's live mailbox size, never the page length.

**Conversation grouping (`group_by_thread`).** `GET /emails` accepts an optional `group_by_thread` query param (default `False`); the virtual listing **always** groups (`list_emails_for_virtual_mailbox` hardcodes `group_by_thread=True` on both store calls). When grouping, each row represents a thread's most-recent message, its `is_read` / `has_attachments` / `is_favorite` are aggregated across the thread, `thread_message_count` is the thread's message count in that box, and `total` counts **threads**, not messages. The service respects the received flag without special-casing — it does NOT force `group_by_thread=False` when `favorite=True` (Favourites passes `False` from the frontend; the combination `favorite=True`+`group_by_thread=True` is valid SQL — "group the favourites" — just not a product surface, so no rejecting guard exists).

The non-obvious invariant shared by both grouped and ungrouped listings: `parse_search_query(q)` (the successor to `parse_search_tokens` on the listing path; `parse_search_tokens` still exists but no service calls it) is run **once** and the same `tokens` AND `operator_clauses` (plus the same `box` / `extra_filters` / `box_not_in` / `group_by_thread`) feed both `list_filtered` and `count_filtered`, so the count counts exactly what the page lists; the two store calls live in **separate** `try` blocks only to keep their failure messages globally unique (`CLAUDE.md` §7). A `count_filtered` failure raises `EmailListError` / `VirtualMailboxListError` (500) with a count-specific message, distinct from the listing raise site. The empty-accounts branch short-circuits to `EmailPageOut(items=[], total=0, …)` before either store call (no DB round trip). The **router** default `limit` is 50 (was 200); the **service** signature default stays 200, so direct service callers and existing tests that omit `limit` are unaffected — the change is only in the `Query(default=...)`.

## Email content — HTML sanitization lives outside this file

The HTML sanitization pipeline used by `GET /mailboxes/{mid}/emails/{id}/content` lives in `api/services/email_html_pipeline.py` (`prepare_email_html`, re-exported by `services_helpers` as `sanitize_email_html` for legacy callers). The pipeline's non-obvious invariants and the **TRUNCATE-on-change rule for the `email_content` cache** are documented in the root `repository_guide.md` § "Email HTML rendering cache" — do not duplicate them here. The endpoint itself enforces a metadata pre-check (`email_metadata_store.exists` → 404 `email_not_found`) before the cache read because `email_content` has a composite FK to `email_metadata` (migration 0013); skipping the pre-check would surface FK violations as 500s.

### Outbound HTML sanitization — distinct pipeline, applied at the trust boundary

There are **two** sanitizers and they are NOT interchangeable. The inbound one above renders arbitrary third-party newsletter HTML for the viewer; the **outbound** one (`api/services/outbound_html_pipeline.py`, `sanitize_outbound_html`, also re-exported by `services_helpers`) cleans the rich-text composer's HTML before it is persisted to a draft or sent, against a **much stricter** allowlist. `create_draft` / `update_draft` sanitise `payload.body` **once at function entry** and reuse the cleaned value for BOTH the provider call and the persisted row — the two must never diverge (a `<script>` cleaned for the provider but stored raw, or vice versa, would defeat the boundary). `send_email` sanitises `payload.body` before passing it to `manager.send_email_from_account` (covers both providers without touching the clients). `send_draft` reads the already-sanitised row, so it does not re-sanitise. The sanitizer is **fail-soft** (returns its input unchanged on any internal error) and never raises a domain error, so no entry in `_CORE_TO_API_MAP` / translation is needed.

### Composed-body size cap is enforced by Pydantic — and 422 uses FastAPI's default envelope

`DraftCreate.body` / `DraftUpdate.body` / `EmailSendRequest.body` carry `max_length=1_000_000`; a body over the cap is the "message too large" guard and collapses to **422** at the schema boundary (no domain exception). Non-obvious: this 422 is emitted by **FastAPI's default `RequestValidationError` handler** with the standard `{"detail": [...]}` shape, NOT the project's `{"error": {code, message, detail}}` envelope — `register_exception_handlers` registers handlers only for `ApiError` and bare `Exception`, and FastAPI's specific `RequestValidationError` handler wins over the `Exception` catch-all. Any client surfacing "body too large" reads `detail[]`, not `error.code`. This is pre-existing behaviour for every validation 422 (e.g. an empty `EmailSendRequest.body`, which still fails `min_length=1`); the cap just adds another trigger.

## GET endpoint testing rule

DB-only GET endpoints (`list_emails`, `list_drafts`, …) must be integration-tested against the seeded data from migration 0010 with exact content assertions. GET endpoints that hit the provider (`get_email_full_content` cache-aside) need their own strategy documented per-endpoint. The canonical rules live in `backend/tests/integration/integration_guide.md` § "GET Endpoint Testing Rules".

## Service-layer error classes

All `ApiError` subclasses live in `api/errors/exceptions.py` and must be registered in `_STATUS_MAP` (`api/errors/handlers.py`). The base class defaults to 500.

- **Reuse before inventing.** Check the existing hierarchy before adding a new subclass.
- **Register in the same commit.** An unregistered `ApiError` silently defaults to 500 and its `code` never reaches the client.
- **Unique message per raise site.** The layer `CLAUDE.md` §7 is load-bearing: every `ApiError` raised directly by the service layer must carry a globally-unique `message`, so the message alone pinpoints the raise site. Especially important for the drafts services' outer safety nets (`DraftCreationError`, `DraftUpdateError`, `DraftDeleteError`, `DraftSendError`), where multiple `raise` statements in the same function would otherwise be indistinguishable.
- **Status quirks worth remembering:** `DatabaseQueryError` is 503 (transient-from-the-caller perspective, retryable), not 500. `EmailListError` / `DraftListError` are 500 because a listing failure is the only place that specific operation can fail and there is no retry story. 409s (`EmailNotInTrash`, `AccountNotConnected`, `AccountConnectAuthError` is 401) encode state conflicts, not plain missing resources — do not downgrade them to 404 when reusing.
- **Attachment internals (500):** `AttachmentLookupError`, `AttachmentInsertError`, `AttachmentListingError` cover the unexpected-internal-failure paths of `add_draft_attachment` / `remove_draft_attachment` (lookup-before-insert, the insert itself, the size/count pre-check listing). They mirror `DraftCreationError` semantics — "something went wrong on our side, not the provider's" — and exist so the response code identifies the failed step instead of collapsing every internal hiccup into a generic `DraftDeleteError` / `DraftCreationError`.
- **Admin purge endpoint:** `PurgeDisabled` is **503**, not 401. The split with `InvalidAdminToken` (401) is intentional: 503 means "this deploy is not configured for purge" (env var unset), 401 means "your token is wrong". Collapsing the two into 401 would mask the deploy-config error behind a credential error and waste on-call time.
- **Dev-login endpoint:** same shape as the purge split — `DevLoginDisabled` is **503** (deploy not configured, `DEV_LOGIN_ENABLED` not truthy) while `DevLoginNotLocalhost` is **403** (host outside `DEV_LOGIN_TRUSTED_HOSTS`). Collapsing both into 403 would mask the deploy-config error behind a security rejection. The third state of the guard, missing `DEV_LOGIN_EMAIL`, surfaces as `EnvVarError` (500) because at that point the operator has already opted in — a missing email is a config bug, not a security boundary.

## Extension

### New identity provider

- Add `POST /auth/<provider>` in `auth_routers.py` (thin route, single service call).
- Add `<provider>_login` in `auth_service.py` (catch `AuthError`, translate via `translate_auth_error`).
- Add request/response schemas in `api/schemas/auth.py`.
- The existing `AuthTokenError` subclasses are provider-agnostic and reusable. See `auth_guide.md` for the auth-layer side of the checklist.
- `POST /auth/dev-login` is a test backdoor, not a provider flow. It bypasses OIDC entirely (no `verify_*_token`, no `AuthSettings.client_id`, no `translate_auth_error`) and must NOT be used as a template when adding a real identity provider — copy from `google_login` instead.
