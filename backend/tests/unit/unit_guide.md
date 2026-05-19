> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# Unit Tests Guide

> **General rules**: this test layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Project-Specific Notes

### Coverage inventory is the tree itself

The canonical inventory of what this layer covers is the `tests/unit/` directory tree. When a new service module is added (e.g. `drafts_service.py`), add a sibling `test_<module>.py`. Do not maintain a manual coverage catalog here — it rots faster than the code.

### Service test pattern — one `_patch_common` per service

Service-layer tests use inline `FakeStore` classes combined with `monkeypatch.setattr` to replace the real store module attributes, avoiding database access entirely. Each service test file owns its own `_patch_common`, tailored to that service's dependencies — `test_drafts_service.py` patches `draft_store.create`, `test_emails_service.py` patches the email persistence helpers. **Do not copy `_patch_common` verbatim across files**; match the patch set to the service's actual dependencies, or tests will silently fall through to real DB / provider paths.

`test_drafts_service.py::_patch_send_common` adds two stubs on `draft_attachment_store` that the send path needs at minimum: `list_by_draft` (composer hydrate) and `list_by_draft_with_blob` (the send-time MIME build). Forgetting either lets the test hit the real connection pool. New `TestSendDraft` cases that need actual rows must override `list_by_draft_with_blob`'s return — that's the slot the service walks to assemble `attachments_inputs`.

**Phase 2.5 trap — listing rows must carry an `attachments` key.** Every fake row passed to `_draft_out_from_row` MUST include `attachments` (even an empty `[]`). The helper short-circuits the per-draft `list_by_draft` follow-up only when the key is present; a fake without it triggers a real DB call mid-test and the failure mode is a confusing `connection.get_connection()` invocation rather than a clear assertion.

**Phase 2.6 trap — `_patch_attachment_common`'s `next_position_value` kwarg is silently ignored.** The kwarg survived the API but Phase 2.6 collapsed the position read+write into the INSERT itself (`COALESCE((SELECT MAX(position)+1 …), 0)` inside `INSERT_DRAFT_ATTACHMENT`). Passing `next_position_value=N` does nothing — the test gets whatever the SQL computes from the seeded fake state. To force a specific position in the fake's response, use `inserted_overrides={"position": N}` instead.

### `test_emails_service.py` — patch helpers are independent, not layered

`_patch_read_status` and `_patch_spam` are **not** extensions of `_patch_common`. They apply a narrower patch set: they omit `account_store.get` and the metadata persistence helpers (read-status and spam don't need single-account lookup or metadata persistence), and add `update_email_read_status_batch` / `update_email_spam_status_batch` respectively. Treat them as independent helpers — extending them from `_patch_common` will over-patch.

`_patch_get_content_common` patches `email_metadata_store.exists → True` by default (metadata row present, so the service's pre-check short-circuits); tests that need a missing metadata row override this patch explicitly. It does **not** patch `get_email_content` — each test inside `TestGetEmailFullContent` patches it independently to control the cache-hit vs cache-miss branch.

### `FakeEmailClient` call-record asymmetries

`tests/shared/email_fakes.py::FakeEmailClient` records invocations of draft operations on `*_calls` lists. The lists intentionally use different tuple shapes per operation — do not assume a uniform schema:

- `create_draft_calls` — 5-tuple `(to, cc, bcc, subject, body)`. Note: the trailing field is `body` (plain text after D-31), not `body_html`.
- `update_draft_calls` — 6-tuple `(provider_draft_id, to, cc, bcc, subject, body)`. Extra leading `provider_draft_id`.
- `delete_draft_calls` — `list[str]` of bare `provider_draft_id`s (not tuples).
- `send_draft_calls` — `list[str]` of bare `provider_draft_id`s; `send_draft_return` returns an `EmailMetadata` (the sent message), not a `DraftMetadata`.
- `send_draft_with_attachments_calls` — 7-tuple `(provider_draft_id, to, cc, bcc, subject, body, attachments_inputs)`; `send_draft_with_attachments_return` is a 2-tuple `(EmailMetadata, list[AttachmentUploadResult])`. The fake's `send_draft_with_attachments_exc` accepts an `EmailAttachmentSendFailed` with its `detail` populated so partial-success persistence (D-27) can be exercised end-to-end without real provider traffic.
- **Trap — pick the right exc kwarg for send failures.** The unified send path goes through `send_draft_with_attachments`, so failure tests MUST use `send_draft_with_attachments_exc`. `send_draft_exc` still exists on the fake but it only fires for the bare `send_draft` legacy code path which the service no longer hits — a test that injects `send_draft_exc=…` against `drafts_service.send_draft` will pass green even when the real error path is broken.
- `list_message_attachments_calls` records bare `provider_message_id` strings; the matching `list_message_attachments_return` is the 2-tuple `(downloadable_list, cid_map)`. Forgetting the second element returns `None` to the cache-miss branch and the `email_attachments` upsert silently writes empty rows.
- `fetch_attachment_binary_calls` is a 2-tuple `(provider_message_id, attachment_metadata)` where the second element is the full `AttachmentMetadata` dataclass instance (not a bare ID). The `attachment_key` lives on `part_id` for Gmail and `provider_attachment_id` for Outlook — they live on the same field on the fake by design, since the service never branches on provider.

### `TestSyncDrafts` — `RuntimeError` asymmetry vs `send_email`

When `fetch_drafts_exc=RuntimeError(...)`, `EmailManager.fetch_all_drafts` captures the error in `_last_errors` — it does **not** wrap it into `EmailExternalAPIError` the way `send_email` does. As a result, the service surfaces `DraftSyncError` (the fallback passed to `raise_on_silent_auth_errors`), not `ExternalAPIError`. Tests asserting 502 `external_api_error` here will fail — assert `draft_sync_error` instead.

### Drafts cap tests live at the client layer, not the service

`_DRAFTS_MAX_TOTAL = 100` is enforced inside `GmailClient._list_all_draft_ids` (single page + paginated) and inside `OutlookClient.fetch_drafts`' `$top=100` loop. `TestSyncDrafts` uses `FakeEmailClient.fetch_drafts_return`, which bypasses both loops entirely and cannot exercise the cap. Any change to `_DRAFTS_MAX_TOTAL` requires updating `test_gmail_client.py::TestFetchDrafts` and `test_outlook_client.py::TestFetchDrafts`.

### `PgDraftStore` error-wrapping invariants

Non-obvious guards verified by `test_draft_repository.py`:

- **`ConnectionPoolError` must propagate unchanged.** The repository's `except DatabaseError: raise` guard distinguishes pool exhaustion from query errors. Every method's test class includes a test that injects `ConnectionPoolError` and asserts it surfaces as-is (not re-wrapped as `QueryError`). If you add a new method, include this test too.
- **`UPDATE ... RETURNING` yielding no row → `QueryError("Draft row to update not found.")`.** This covers the race where the service pre-check (`draft_store.get`) succeeds but another caller deletes the row before the `UPDATE` lands. Without this explicit path the update would silently succeed with a `None` return.
- **`replace_all_for_account` with `[]` intentionally wipes every row for the account.** The UPSERT is skipped but `DELETE_DRAFTS_MISSING_FOR_ACCOUNT` still runs with `keep_ids=[]`. Do not add a guard that short-circuits on empty input — tests rely on the delete running.

### `PgDraftAttachmentStore` / `PgEmailAttachmentStore` — non-obvious invariants

- **`update_provider_attachment_id` raises on the missing-row race AND on bad UUID.** The query carries `RETURNING draft_attachment_id`; if `cur.fetchone()` is `None` the repository raises `QueryError("draft_attachment row missing during provider id update.")`. A bad UUID at the boundary likewise raises `QueryError("Invalid draft_attachment_id passed to …")`. Both used to swallow silently — keeping them loud preserves the D-27 partial-success contract.
- **`batch_update_provider_attachment_ids` is the production path; the single-row variant only survives for tests.** The batch helper uses positional `%s` + `execute_values` against `BATCH_UPDATE_DRAFT_ATTACHMENT_PROVIDER_IDS` and returns the count of rows actually updated. Both Outlook send call sites in `drafts_service` route through it. Bad UUID raises loudly (same D-27 rationale); empty input short-circuits to `0` without touching the connection.
- **`insert` ignores any `position` the caller supplies.** The SQL embeds `COALESCE((SELECT MAX(position)+1 …), 0)` so the row dict must NOT carry `position`. Tests that fake the response with a specific position should use `inserted_overrides={"position": N}` rather than seeding the input. (The matching service-test trap about `_patch_attachment_common`'s ignored `next_position_value` lives in the "Service test pattern" section.)
- **`list_by_draft_with_blob` is the send-time pull** (single round trip), `list_by_draft` is the composer pull (no blob). Mixing them up turns 1 query into 1 + N or, worse, drops the binary needed to build the MIME.
- **`PgEmailAttachmentStore.upsert_batch` partitions Gmail vs Outlook rows by `part_id IS NULL`** — Gmail rows hit `idx_email_attachments_gmail`, Outlook rows hit `idx_email_attachments_outlook`. Each partition runs its own `execute_values(…, fetch=True)`; the returned rows are the source of truth for `persisted` (do NOT chase with a second `cur.fetchall()` — that's how the BLOCKER bug crept in).
- **`update_has_attachments` raises on bad UUID.** Same rationale as the draft sibling: B.lazy invariant breaks silently if a malformed identifier short-circuits without signalling.
- **`mark_unavailable` and `touch_last_accessed` disagree on bad UUID.** Both go through `_single_update`. `touch_last_accessed` is the BackgroundTask after a successful download — a stray bad UUID there is a programming error; the helper logs at `debug` and returns silently (the user already got their bytes). `mark_unavailable` runs on the provider-404 path — the same silent return is acceptable here because the user's response is already locked into 404 `attachment_unavailable` regardless of whether the stamp succeeded. Don't propagate errors from inside `_single_update` without first redesigning the caller flows.
- **`purge_expired_blobs` returns a 2-tuple `(count, freed_bytes)`.** Callers that unpack one value will crash silently (the second element is summed from the row sizes inside the `RETURNING` clause). The endpoint uses both for the admin response; tests assert both.
- **`get_blob` returns `None` for three different reasons** — row absent, `blob` column SQL NULL, and bad UUID. All three are treated identically by the cache-aside path (cache-miss → fetch from provider). Asymmetric vs the rest of the repo where bad UUID raises; documented here so a maintainer adding a fourth caller knows not to expect a distinguishing signal.

### Attachment service / helpers — non-obvious invariants

- **`touch_attachment_last_accessed` swallows every exception.** It runs as a `BackgroundTask` after the StreamingResponse is already on the wire — raising would crash the task without the user noticing. The catch is a literal `except Exception: logger.warning(...)`. New tests for that helper MUST NOT assert on raised errors.
- **`translate_core_error` splits `EmailAttachmentDownloadFailed` by `detail['reason']`.** The mapping list is otherwise purely typed; this is the single exception. `'forbidden'` → `AttachmentProviderForbidden` (502); anything else (including missing reason) → `AttachmentProviderUnavailable` (503). Tests that omit `detail` should assert the 503 default.
- **Outlook `pick_outlook_attachment_strategy` boundary is inclusive at 3 MB; Gmail `pick_gmail_send_strategy` is exclusive at 5 MB.** A file of exactly 3 MB uses Outlook's chunked upload session; a file of exactly 5 MB uses Gmail's simple `drafts.send`. Boundary tests must hit the exact byte counts to lock the asymmetry.
- **`enforce_multipart_size_limit` is strict `>`.** A request with `Content-Length` exactly 30 MB is allowed through (the per-file 25 MB cap inside the service catches the actual abuse); only `> 30 MB` returns 413. Missing or malformed `Content-Length` is silently skipped (best-effort cushion against chunked transfer).
- **`BLOCKED_EXTENSIONS` shape is a frontend contract.** Lowercase, no leading dot, sorted, no duplicates — the JSON exporter at `frontend/src/lib/blocked_extensions.json` mirrors this list and the parity test at `tests/integration/test_blocked_extensions_parity.py` will break the build on any divergence. Don't add an entry without re-running the JSON sync.
- **`sanitize_filename` quirks worth tests.** Windows-reserved basenames (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) get prefixed with `_` regardless of case (`con.txt` → `_con.txt`). Collision resolution within an email/draft appends ` (1)`, ` (2)`, … **before** the extension, never after.

### `translate_*_error` fallback messages — never leak `str(exc)`

When the input exception is not of the expected layer base class (e.g. a stray `RuntimeError` reaches `translate_database_error`), the fallback `ApiError` message is a literal — `"Unexpected database error."`, `"Unexpected core error."`, etc. The original exception goes to `logger.warning` instead. Per `api/CLAUDE.md` §9 rule 4 the API surface MUST NOT carry raw library messages; tests that assert `"boom" in result.message` against a `RuntimeError("boom")` injection are stale and need to flip to asserting the literal.
