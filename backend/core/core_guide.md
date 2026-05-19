> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# Email Client Implementation Guide

> **General rules**: this layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Helper Reuse Policy

Before writing new logic in `gmail_client.py`, `outlook_client.py`, or `email_manager.py`:

1. **Check `helpers.py` first.** Shared utilities (`parse_expiry`, `unwrap_app_credentials`, `unwrap_user_tokens`, `wrap_account_tokens`, `http_error_detail`, `inline_cid_images`, `decode_mime_body`, `build_mime_with_attachments`, `retry_with_backoff`, `find_referenced_cids`, `format_content_disposition`, `pick_gmail_send_strategy`, `pick_outlook_attachment_strategy`) already cover the cross-provider needs.
2. **Then check private helpers in the file being edited.** Reuse or extend existing `_*` methods instead of duplicating.
3. **Extract when duplicated.** If both clients end up with similar logic, move the common part to `helpers.py` so both import it.

Applies to every modification — not just when adding a new provider.

## `_last_errors` — per-account error aggregation

`EmailManager` accumulates per-account failures in `_last_errors` (dict keyed by account label). Batch operations (`fetch_all_email_metadata`, `fetch_all_drafts`, `authenticate_all_silent`) collect per-client errors without aborting the others; the service layer inspects them afterwards via `get_last_errors()`.

**Gotchas:**
- `connect_account()` **resets** `_last_errors` at entry — any earlier errors are lost. Fine in practice because the interactive `/connect` endpoint never interleaves with batch operations, but worth remembering when writing new service functions.
- `connect_account()` re-raises the error directly (`raise EmailExternalAPIError(...) from exc`) instead of storing it in `_last_errors` — the interactive flow has a single account, so there is nothing to aggregate.

## `_execute_batch_get` retries; `_execute_batch_modify` is split per operation

Gmail has two batch skeletons. Both chunk by `_BATCH_SIZE = 100` and dispatch to a `ThreadPoolExecutor`.

- **`_execute_batch_get`** (reads) wraps each chunk in a retry loop (`_BATCH_MAX_RETRIES = 4`, `_BATCH_RETRY_DELAY = 1.0s`). Reads are idempotent — retrying is safe.
- **`_execute_batch_modify`** dispatches to the underlying `_batch_modify_labels` helper. Read-status and spam batches DO retry (their label moves are idempotent at the Gmail API level — applying an already-applied label is a no-op). The trash batch is the exception that does not retry, because Gmail's `trash` action is not idempotent (re-trashing an already-trashed message produces a hard error rather than a no-op). Don't collapse this nuance into a blanket "modify never retries" — read-status and spam relied on retry semantics during the metadata-sync rollout.

`fetch_drafts` reuses `_execute_batch_get` with `resource="drafts"`, so it inherits the retry loop.

## Service-stamped `EmailMetadata.account_id`

Provider clients leave `EmailMetadata.account_id` as `""`. The service layer stamps it before persistence. **Trap:** always stamp before calling `persist_email_metadata_batch` — skipping it writes empty strings into the DB, and if a client sets it the service overwrites anyway. Do not try to fix this by stamping in the client; the cross-layer contract is that the service owns it.

## Authentication — silent-refresh invariants

- **`email_address` is fetched only during the interactive `authenticate` flow**, never during silent refresh. The `upsert_tokens` SQL uses `COALESCE(%(email_address)s, email_address)` so silent refreshes don't erase a previously stored value. Do not "fix" silent auth to also fetch it — Gmail's silent flow doesn't expose it without a second round-trip.
- **Outlook rotates refresh tokens; Gmail doesn't.** Outlook's auth server may return a new `refresh_token` on every refresh — always persist the returned refresh token. Gmail's refresh token is stable in practice. The upsert path handles both uniformly, but don't skip writing `refresh_token` for Outlook on the assumption that it's unchanged.

## Email metadata sync — invariants

- **Box mapping priority** inside each client is `TRASH > SPAM > SENT > otherwise ALL_MAIL`. Any new Gmail label or Outlook folder must be threaded through this priority (`_FOLDER_TO_BOX` on Outlook, label check on Gmail); **do not introduce a new `box` value without also updating the DB CHECK constraint**.
- **Gmail bootstrap captures `historyId` *before* listing messages.** Any emails arriving during the list window are thus replayed on the next incremental sync. Do not reorder the two calls.
- **Gmail incremental falls back to bootstrap when event count > `_INCREMENTAL_EVENT_THRESHOLD = 100`.** Batch-fetching thousands of accumulated events costs more than a full re-sync.
- **Outlook delta is per-folder.** Microsoft Graph v1.0 does not support delta at the mailbox level, so the client iterates `_DELTA_FOLDERS` (`inbox`, `sentitems`, `drafts`, `deleteditems`, `junkemail`, `archive`) and stores a versioned JSON cursor `{"v": 1, "folders": {"inbox": "<deltaLink>", …}}`. A non-JSON / unversioned cursor (legacy single-URL format) decodes to `None` and triggers `EmailExternalAPIError → bootstrap fallback`.
- **Outlook fault tolerance:** during bootstrap delta init, a failing folder is logged and excluded from the cursor. During incremental, a failing folder keeps its previous deltaLink in the new cursor. If **all** folders fail during incremental, an error is raised to force bootstrap fallback — never accept an "incremental succeeded with zero events" when every folder errored.

## Trash — `delete_messages` intentionally breaks Provider-First

Both Gmail and Outlook use a **no-op** approach for `delete_messages`: the provider API is not called; deletion is marked only in the local DB. This is the one documented exception to the repo's Provider-First Rule (see `repository_guide.md`).

**Why:** Gmail's `gmail.modify` scope cannot call `messages.delete` (that requires the restricted `mail.google.com` scope). Since one provider can't perform permanent deletion without a scope upgrade, we adopt a uniform no-op across all providers so behaviour is consistent and predictable.

**Effect:** the provider retains the messages in Trash until its own retention policy purges them (Gmail ~30 days; Outlook per-tenant). The DB marks them `box = 'DELETED'` as a soft-delete guard — see the `DELETED` CASE logic documented in `database_guide.md`.

## Outlook — folder moves always rewrite the ID

`POST /me/messages/{id}/move` returns a new message object with a new `id`. Any code that moves a message between folders (spam, trash, any future operation) **must** capture the new ID from the response and propagate it to the service layer for DB persistence. Do not assume `new_id == old_id` on Outlook — that assumption is true only on Gmail.

## Send retry asymmetry — Gmail vs Outlook

Both providers retry sends with `_SEND_DRAFT_MAX_ATTEMPTS = 3`, but the back-off shapes differ:

- **Gmail** uses a fixed `_SEND_DRAFT_RETRY_DELAY = 1.0s` between attempts in the bare-text `send_draft` path.
- **Gmail with attachments** (`_send_draft_simple` and the resumable variant) uses a linearly escalating `delay * attempt` (1s, 2s, 3s). The intra-Gmail asymmetry exists because the with-attachments path stays under load longer (full-MIME rebuild) and the linear back-off matches Graph's behaviour the user already sees on the Outlook side.
- **Outlook** uses a linearly escalating `delay * attempt` (so 1s, 2s, 3s) for `send_draft` and the dedicated `_OUTLOOK_RETRY_DELAYS_SECONDS` tuple for attachment fetches.

Don't normalise these — Gmail's per-user-rate-limit pushes back faster than Outlook's per-tenant throttling, and Outlook's escalation matches Graph's documented Retry-After hints when no header is present.

## Gmail send-time 429 — `user-rate limit exceeded (mail sending)` is non-retryable

A generic 429 is retryable. The specific reason `user-rate limit exceeded (mail sending)` is the **daily quota** ceiling and retrying within the window only burns cache — `_is_send_retryable` filters it out before returning to `retry_with_backoff`. Adding a new retryable error class must NOT loosen this filter; the daily cap is a hard wall, not a transient.

## Resumable Gmail upload — chunk size MUST be a multiple of 256 KB

`_GMAIL_RESUMABLE_CHUNK_SIZE = 4 * 1024 * 1024` (4 MB) satisfies this. The Gmail upload protocol rejects chunks that are not multiples of 256 KB unless they're the final chunk. Do not "tune" this constant to an arbitrary value to save bandwidth; the request will fail with a 400 the moment the body is more than one chunk.

## Send-with-attachments failure `reason` — closed enum across two paths

`EmailAttachmentSendFailed.detail.reason` is one of: `forbidden`, `unavailable`, `too_large`, `provider_error`, `throttled`, `daily_limit`. Two dispatchers produce them and they are not symmetric — Outlook's `_classify_send_failure_reason` emits the first five (never `daily_limit`); Gmail's `_raise_send_with_attachments_error` is the only path that emits `daily_limit` (Gmail 429 with the daily quota ceiling). Adding a new reason requires updating both dispatchers AND the frontend dialog (`AttachmentSendFailedDialog`); free-text reasons silently fall through the dialog's branching to a generic message.

## `_graph_request_raw` folds URLError into a synthetic 503

`fetch_attachment_binary`'s retry loop dispatches by status code. A connection-level failure (DNS, timeout, refused) used to escape immediately as `EmailExternalAPIError`, which the loop never matched — so a single network blip produced a hard fail instead of a retry. The helper now logs the URLError and returns `(503, {}, b"")` so the retry path engages naturally. Side effect: the request count in tests will reflect the retry attempts rather than a single shot.

## Outlook — percent-encode every message ID in the URL path

Outlook Immutable IDs are base64-like strings containing `+`, `/`, and `=`, none of which are safe in URL path segments. Every call to `_graph_request` that interpolates a message/draft ID into the URL path **must** wrap it with `urllib.parse.quote(id, safe='')`. Without this, Graph returns 400 / 404 on any ID containing those characters.

## Outlook drafts — `Prefer: IdType="ImmutableId"` must be re-sent on every call

Drafts are created with `Prefer: IdType="ImmutableId"` so the ID survives state transitions. Graph does **not** remember that preference per-message — every follow-up call (PATCH / DELETE / send) must re-send the header. Drop it on any follow-up and Graph reinterprets the stored Immutable ID as a transient ID and returns 404. The implementation threads this via `_graph_request`'s keyword-only `extra_headers` parameter.

The same rule applies to **every** Graph call that touches a message OR an attachment: `GET /me/messages/{id}/attachments`, `POST .../attachments`, `POST .../attachments/createUploadSession`, `GET .../attachments/{att}/$value`, `POST .../send`. The shared `_PREFER_IMMUTABLE_HEADERS` constant is passed via `extra_headers=...` on every such call. `urllib.parse.quote(message_id, safe="")` is required around every interpolated message/draft id (Outlook ImmutableIds are base64-like and contain `+`/`/`/`=`).

`_graph_request` returns `{}` on HTTP **204** (DELETE / send produce no body). Callers that branch on response keys (e.g. `response.get("id")`) must NOT treat the empty dict as an error — it is the success signal for those endpoints. Adding a defensive fallback like `response.get("id") or _raise_missing_id()` would silently break send and delete on the happy path.

**`_upload_chunk_put` and `_graph_request_raw` are not interchangeable.** `_graph_request_raw` always sends `Authorization: Bearer …` and is the helper for normal authenticated binary fetches (`/$value`). `_upload_chunk_put` is exclusively for the pre-authenticated `uploadUrl` returned by `createUploadSession` and **omits** the bearer header on purpose — sending it corrupts the binary because Outlook re-interprets the auth header. Picking the wrong helper for chunk PUTs is a silent corruption trap; the call sites are not abstracted into a single dispatcher precisely so the mistake is local and obvious.

## Drafts — provider asymmetries

- **Cap per account:** both providers enforce `_DRAFTS_MAX_TOTAL = 100` most-recent drafts. Gmail inside `_list_all_draft_ids` + `_execute_batch_get(resource="drafts")` (parallel workers controlled by `GMAIL_BATCH_MAX_WORKERS`, default 5). Outlook inside the paginated `$top=100&$orderby=lastModifiedDateTime desc` loop + 4 retries per page.
- **Gmail ordering is by convention, not by docs.** `drafts.list` has no `orderBy` parameter and returns drafts in reverse-chronological order in practice. If this ever becomes unreliable, the cap semantics break — track it.
- **Send-draft ID asymmetry:** Gmail's `drafts().send()` returns a Message with a **new** `id` (and auto-deletes the draft). Outlook's `POST /messages/{id}/send` returns 202; the message ID stays the **same** thanks to ImmutableId. Callers and tests must not assume equality.
- **Send-draft retry policy:** both clients retry up to 3 attempts (`_SEND_DRAFT_MAX_ATTEMPTS = 3`, `_SEND_DRAFT_RETRY_DELAY = 1.0s`). Gmail retries only on `_RETRYABLE_STATUS_CODES` (429, 5xx); Outlook retries every `EmailExternalAPIError` — by design, because Graph returns a narrower error surface.
- **`body` is plain text on both providers (D-31).** `create_draft` / `update_draft` accept `body` (was `body_html`) and ship it as `text/plain` — Gmail builds a single `MIMEText(body, "plain")` (no `multipart/alternative` wrapping a single part), Outlook sets `body.contentType = "Text"`. Pasting HTML in the composer ships the raw markup as plain text — that is intentional. A future rich-text editor will introduce a separate `body_format` field rather than reviving the HTML naming.
- **Legacy HTML-only drafts surface as raw markup on read.** Drafts created at the provider before D-31 (Gmail) or stored with `contentType=HTML` (Outlook) still exist in real mailboxes. `_parse_gmail_draft` prefers the `text/plain` part and falls back to the HTML part when no plain version exists; `_parse_outlook_draft` returns `body.content` regardless of `contentType`. The user opens such drafts and sees `<p>…</p>` markup verbatim — that is an accepted MVP edge case (the user can re-edit). Do NOT add HTML-to-text stripping; an unintended hit on a freshly composed plain draft would silently mangle it.
- **Round-trip body asymmetry — Gmail strips one trailing newline on read.** `MIMEText(body, "plain")` appends a single `\n` (or `\r\n`) at MIME serialization, and Gmail returns it verbatim on `drafts.get`. `_parse_gmail_draft` strips exactly one terminator — `\r\n` first, falling back to `\n` (the order matters: stripping `\n` first would leave a stray `\r` whenever the serializer chose CRLF). Outlook does NOT need this because Graph stores `body.content` as-is. Don't move this normalization into a shared helper — adding it to the Outlook path would silently truncate bodies a user explicitly ended with a blank line.

## Attachments — strict inline rule, send asymmetry, retries

- **Strict inline-vs-attachment rule (D-13).** A part is treated as inline (embedded as `data:` URL in the rendered body) **only** if all four conditions hold: `Content-Disposition: inline` (Outlook `isInline=true`), the `Content-ID` is referenced by the rendered HTML body via `cid:…`, the part has bytes, and the MIME type starts with `image/`. The reference check uses the shared helper `helpers.find_referenced_cids`, which inspects both the HTML attribute form (`src="cid:…"` / `background="cid:…"`) AND the CSS `url(cid:…)` form (post-premailer). Any other part — including inline-marked-but-unreferenced and `Content-Disposition`-less parts that simply carry a `filename` — is promoted to a downloadable. Both clients implement the rule in `_classify_attachments` and return `(cid_map, downloadable)`.
- **Provider key asymmetry, persisted in `email_attachments`.** Gmail's `attachmentId` is **not declared stable** by the docs (observed to change between calls), so we cache by `part_id` (immutable per the docs) and rediscover the `attachmentId` by walking the message tree via `users().messages().get(format=FULL)` whenever a download is needed. Outlook's `id` IS stable while the message stays in the same mailbox, **provided every Graph call sends `Prefer: IdType="ImmutableId"`** — so we persist it as `provider_attachment_id`. The `AttachmentMetadata` dataclass carries both columns; one is always `None` per provider. Mixing them silently corrupts the cache key.
- **Send-with-attachments asymmetry (D-07, D-18, D-27).** Gmail is **atomic**: `send_draft_with_attachments` builds a fresh `multipart/mixed` MIME (body + every attachment) and replaces the draft contents in a single `drafts.send` call (or its `uploadType=resumable` variant when total MIME > 5 MB, picked by `pick_gmail_send_strategy`). The returned `AttachmentUploadResult` list is always empty — there is no intermediate provider state to persist. Outlook is **non-atomic**: each pending attachment is uploaded via `POST /me/messages/{id}/attachments` (<3 MB) or `createUploadSession` + chunked PUTs (≥3 MB, picked by `pick_outlook_attachment_strategy`), and each upload's returned id is appended to the result list so the service can persist `provider_attachment_id` immediately. After every attachment is in place, `POST /messages/{id}/send` finalises the send.
- **Outlook upload session — chunk PUTs MUST omit `Authorization`.** The `uploadUrl` returned by `createUploadSession` is pre-authenticated (the token is in the URL). Sending the bearer token in the chunk PUT silently corrupts the binary because Outlook re-interprets the auth header. Encode `contentBytes` with **standard base64 (RFC 4648)**, NOT base64url like Gmail — mixing them is also a silent-corruption trap.
- **Outlook partial-success contract (D-27).** When one attachment fails after others succeed, the client raises `EmailAttachmentSendFailed` with `detail = {"failed_attachments": [{draft_attachment_id, filename, reason}, ...], "succeeded": [{draft_attachment_id, provider_attachment_id}, ...]}`. The service stamps each `succeeded` row's `provider_attachment_id` onto `draft_attachments` **before** re-raising the translated error, so a retry's `_collect_draft_attachment_inputs` finds those rows already populated and the client skips them. Gmail's atomic path leaves `succeeded = []`. Do not "fix" the asymmetry by issuing per-attachment uploads in Gmail — `drafts.update` rebuilds the entire MIME, making it O(n²) for n attachments.
- **Attachment download retries (D-16, D-17).** `fetch_attachment_binary` retries on 429 / 5xx with delays from `_DEFAULT_RETRY_DELAYS_SECONDS = (1, 2, 4)` — `Retry-After` overrides the default delay when present. 404/410 → `EmailAttachmentNotFound` (service stamps `unavailable_at` once and short-circuits future requests). 403 → `EmailAttachmentDownloadFailed(detail={"reason": "forbidden"})`. Persistent 5xx → `EmailAttachmentDownloadFailed(detail={"reason": "unavailable"})`. The service uses `detail['reason']` to split into 502 vs 503 (see `api_guide.md`).
  - Gmail's retry count is the dedicated `_FETCH_ATTACHMENT_MAX_ATTEMPTS = 3` constant — intentionally **decoupled** from `_SEND_DRAFT_MAX_ATTEMPTS`. Both equal 3 today but each path has its own quota story; tuning one must not silently move the other.
  - Each Gmail attachment download costs **2 API calls** (`messages.get(format=FULL)` to walk the MIME tree + locate the part by `part_id`, then `attachments().get()` for the binary). Outlook costs **1** (`GET /attachments/{id}/$value`). The Gmail asymmetry is the price of not persisting Gmail's volatile `attachmentId`; do not "optimise" it by caching `attachmentId` in `email_attachments` — the docs do not declare it stable.
- **Inline classification — Gmail silently drops anonymous parts.** Gmail's `_classify_attachments` skips parts that have **no filename, no `Content-Disposition` header, and no `Content-ID`** all at once. Such parts cannot be meaningfully surfaced (no anchor for `cid:`, no name to download by) so they neither become inline-data URLs nor downloadables. Outlook has no equivalent filter (Graph guarantees richer metadata). Don't "fix" the Gmail filter without auditing whether real-world senders ship anchorless parts that should reach the user another way.
- **Outlook downloadables include item / reference attachments.** `_classify_attachments` lets non-`#microsoft.graph.fileAttachment` `@odata.type` values through (item attachments, `referenceAttachment`) so their metadata reaches the user. Calling `fetch_attachment_binary` on a `referenceAttachment` returns provider 405 — the retry loop exhausts and the service surfaces `unavailable`. This is an accepted MVP limitation; do not add a special case without also surfacing the cause in the UI (the user currently sees a generic "no se pudo descargar").
- **Filename sanitisation (`helpers.sanitize_filename`, D-20).** Replaces `/`, `\`, `:`, `?`, `*`, `<`, `>`, `|`, `"` and control bytes (0x00–0x1F) with `-`, neutralises `..`, prefixes Windows-reserved basenames (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) with `_`, preserves UTF-8 (no slugify), preserves the extension, and resolves collisions inside the same email/draft by appending ` (1)`, ` (2)`, … before the extension. The output is what gets persisted and what is sent in `Content-Disposition`.
- **`format_content_disposition` emits BOTH legacy and RFC 5987 forms.** `attachment; filename="<ascii-fallback>"; filename*=UTF-8''<percent-encoded>`. FastAPI does not produce this combined form automatically; relying on FastAPI's default header would break international filenames in older clients.
- **Blocked-extensions list is a UNION of Gmail + Outlook.** `core.email.blocked_extensions.BLOCKED_EXTENSIONS` is the canonical source (D-04a). The frontend mirrors it from `frontend/src/lib/blocked_extensions.json`; sync is **manual** with a parity test in CI to break the build on divergence. Outlook's `sendMail` returns `202 Accepted` even for blocked extensions and bounces with a delayed NDR — so client-side rejection is the only path to immediate, consistent feedback (see `docs/features/adjuntos.md` § 3).

## Extension — new provider checklist

Core layer:

- [ ] Subclass `EmailClient` in `backend/core/email/<provider>_client.py`. Implement every abstract method on the contract — the class will not import without them.
- [ ] `fetch_drafts` MUST enforce `_DRAFTS_MAX_TOTAL = 100` by the provider's timestamp field (most recent first).
- [ ] Raise typed `CoreError` subclasses on every failure path; no bare `Exception` leaks.
- [ ] Reuse helpers from `helpers.py` (see Helper Reuse Policy above).
- [ ] Add a provider branch in `EmailManager._build_client`.
- [ ] Export new public symbols in `core/email/__init__.py`.

Cross-layer:

- [ ] Add provider env var mapping in `database/settings.py`; extend `database/security/app_credentials.py` if JSON parsing differs.
- [ ] Update the provider CHECK constraint in `database/schema.sql` via a new Alembic migration (and `migrations/runner.py` — see `database_guide.md`).
- [ ] Extend `_CORE_TO_API_MAP` in `api/services/services_helpers.py` if the provider introduces new error types.
- [ ] Add unit, integration, and E2E coverage (see each test guide's Extension Checklist).
