> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# Integration Tests Guide

> **General rules**: this test layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Project-Specific Notes

### Trap 1 — new repository module must be patched in `isolated_db`

When adding a new repository module, its `get_connection` must be monkeypatched in the `isolated_db` fixture (`conftest.py`). Without this, the repository uses the real connection pool instead of the per-test transaction, breaking isolation and causing flaky tests. Symptom: data leaks between tests and rollback no longer works. The current set of patched modules is what `conftest.py::isolated_db` lists — keep that list and this section in sync when adding a new repository.

Asymmetry worth knowing: attachments require **two** repository modules in the patch list — `email_attachment_repo_module` (received-email side) and `draft_attachment_repo_module` (composer side). Most layers have one repository per domain; attachments are the exception, and forgetting either causes silent leakage on its half of the surface.

### Trap 2 — new service module must be patched in `_apply_test_monkeypatches`

`_apply_test_monkeypatches` patches `build_manager_for_accounts`, `load_wrapped_app_credentials`, `load_wrapped_account_tokens`, and `account_store.upsert_tokens` **independently in every service module that imports them** (currently `services_helpers`, `accounts_service`, `emails_service`, `drafts_service`, `attachments_service`). Each module imports at its own module level, so each must be patched separately. When a new service module is added, extend this list — otherwise its integration tests will hit real provider APIs.

### Trap — `sync_drafts` `RuntimeError` surfaces as `draft_sync_error`, NOT `external_api_error`

Tests that inject `fetch_drafts_exc=RuntimeError(...)` and assert the standard `external_api_error` 502 envelope will fail. `raise_on_silent_auth_errors` receives `DraftSyncError` as its fallback class, unlike every other translated `RuntimeError` path which surfaces as `external_api_error`. When writing a new sync-style endpoint, verify which fallback class its `raise_on_silent_auth_errors` call receives — the choice silently changes the error code.

### Favourites — five traps not covered elsewhere (`test_favorites.py`)

- **The race-to-404 test patches the store method directly, not the manager builder.** `test_set_favorite_race_zero_rows_returns_404` forces the lost race (provider toggle OK, local row gone → `update_favorite` returns 0 rows → 404) via `monkeypatch.setattr(emails_service.email_metadata_store, "update_favorite", lambda *_: False)` — a different injection axis from every other favourites test (which patches `build_manager_for_accounts`). Patch the wrong alias and the real update runs: the test passes for the wrong reason (same silent-no-op class as the `configurable_test_client` misspelled-key trap).
- **`favorite=true` makes `box=ALL_MAIL` the "exclude TRASH/SPAM" anchor; every other box value is literal.** `test_listing_with_favorite_and_explicit_sent_box_returns_only_sent_favorites` is the named regression guarding the historical bug where any non-{TRASH,SPAM} box collapsed into the anchor and leaked ALL_MAIL favourites into the SENT view. Its SENT/TRASH/SPAM siblings (and the `q=in:sent` operator variant, which reaches the same state through `q`) must stay paired — a single-box assertion misses the leak.
- **`sync_favorites` provider-list failure surfaces as `external_api_error` (502), NOT `favorite_sync_error`** — the inverse of the `sync_drafts`/`draft_sync_error` trap above. A per-account `list_favorite_ids` failure is captured into `manager._last_errors` and re-raised through `translate_core_error` (`EmailExternalAPIError → external_api_error`); the `FavoriteSyncError` fallback only fires for a DB/internal failure after a provider success. Do not assert `favorite_sync_error` on the provider-failure path.
- **`total_synced` (rows touched) is deliberately ≠ `accounts[i].favorites_synced` (provider-list size).** `total_synced` is the rowcount of the single full-replace UPDATE across the whole account; `favorites_synced` is `len(favorite_ids)` from the provider. `test_sync_favorites_full_replace_for_account` pins `total_synced=4` vs `favorites_synced=2` — asserting equality is wrong. A provider favourite absent from local `email_metadata` is counted in `favorites_synced` but creates no row (Option A — `test_sync_favorites_ignores_unknown_provider_id_no_new_row`).
- **The existence pre-check fires before the manager is built.** `set_favorite` calls `email_metadata_store.exists()` and 404s with `email_not_found` BEFORE `build_manager_for_accounts`, so a missing row spends no provider round trip. `test_set_favorite_missing_email_does_not_call_provider` proves it by stubbing the builder to raise and asserting its call-count stays 0 — a status-only 404 assertion would not distinguish a pre-check 404 from a post-build one.

### `failing_test_client` — parametrize with `indirect=True`

The fixture reads its parametrize value to configure which `FakeEmailClient` method raises and what error. The failure behavior is injected via `@pytest.mark.parametrize(..., indirect=True)` — this is not obvious from the fixture signature. Use existing `test_core_error_translation.py` entries as templates when writing a new error-translation case.

### `configurable_test_client` — mutable `config` dict

Returns `(client, config)`. `config` is a mutable dict that `FakeEmailClient` reads by reference, so tests can change provider behavior between API calls within a single test. Supported keys include `metadata`, `deletes`, `label_updates`, `is_full_sync`, `existing_message_ids`, `sync_cursor_return`, and the explicit `*_return` overrides: `delete_return`, `restore_return`, `move_to_trash_return`, `fetch_messages_metadata_return`. Misspelling any of these silently produces a no-op fake (not an error), so the test passes for the wrong reason. Siblings: `test_client` gives a static fake; `failing_test_client` injects one failure via parametrize.

### `seeded_test_client` — for GET tests against migration 0010 data

Per-test fixture that overrides the auth dependency to return the **seeded** user ID (not the default `TEST_USER_ID`) and restores the previous override after the yield. Because `app.dependency_overrides[require_session]` is global, a test using this fixture must not interleave with other auth-override fixtures in the same file.

### DDL inside a test transaction — only PostgreSQL-transactional statements

Tests that prove "NULL `owner_user_id` is forbidden by the API" (`test_null_owner_mailbox_*`) issue an `ALTER TABLE ... DROP NOT NULL` inside the per-test transaction so they can stage the disallowed state. PostgreSQL DDL is transactional and rolls back cleanly with the test, so this is safe. Do **not** copy this pattern with non-transactional DDL like `CREATE INDEX CONCURRENTLY`, `VACUUM`, or `REINDEX CONCURRENTLY` — those cannot run inside a transaction and would leak schema changes across tests.

### Auth override removal pattern — `finally` is essential

Tests that verify real session validation temporarily remove the `require_session` override:

```python
override = app.dependency_overrides.pop(require_session, None)
try:
    # test code
finally:
    if override is not None:
        app.dependency_overrides[require_session] = override
```

Without the `finally`, a test failure leaves the override removed and poisons every subsequent test in the session.

### `_insert_draft` and the `now()` invariance trap

`test_drafts.py::_insert_draft` accepts an optional `created_at` ISO string. This parameter is essential for any test that asserts a specific `ORDER BY created_at DESC` result: PostgreSQL's `now()` returns the **same value for every statement inside a single transaction**, and the isolated-db fixture wraps each test in one transaction. Without explicit timestamps, rows inserted back-to-back share identical `created_at`, and the ordering becomes non-deterministic.

### Trap — virtual mailbox tests must reparent the seeded mailboxes to `TEST_USER_ID`

Migration `0010` seeds the Gmail and Outlook mailboxes under `SEEDED_USER_ID`, not the per-test `TEST_USER_ID`. Any test that creates a virtual mailbox referencing `SEEDED_GMAIL_ACCOUNT_ID` or `SEEDED_OUTLOOK_ACCOUNT_ID` must first call `_reparent_seeded_user(isolated_db, TEST_USER_ID)` (defined at the top of `test_virtual_mailboxes.py`). Without it `POST /virtual-mailboxes` returns 404 `account_not_found` and the failure gives no indication that an ownership reparenting step is missing. Unique to vmb tests — drafts / emails / attachments tests either own their seeds through `TEST_USER_ID` or create data on the fly.

### Trap — raw SQL inserts into `virtual_mailboxes` must populate `scope_payload`

Migration `0032` dropped `scope_kind` from the API contract but **kept** `scope_payload` as a `NOT NULL` column on the table (renaming it would have broken too many in-flight migrations). Tests that bypass the router to stage a `virtual_mailboxes` row — ownership tests against a foreign record, race-condition setups, etc. — must include `scope_payload` with at least `'{"account_ids":[]}'::jsonb`. Omitting it fails with a constraint violation whose message does not hint at the contract / schema divergence.

### Trap — `GET /virtual-mailboxes` populated path: assert owner-scoping, not just the empty list

`test_list_empty_returns_empty_array` only ever exercises the `[]` path. A cross-user leak in `LIST_VIRTUAL_MAILBOXES_BY_OWNER`'s `WHERE owner_user_id = %s` would pass it. `test_list_returns_only_owned_vmboxes` creates two owned vmboxes plus one inserted under a foreign owner (raw insert, `scope_payload='{"account_ids":[]}'::jsonb` per the trap above) and asserts the response contains EXACTLY the two owned ids — the only test that catches an owner-scoping regression.

### Trap — empty-string filter values must be 422, not "no filter"

`subject_contains=""` / `from_email=""` would reach the repository as `ILIKE '%%'` and match every row — a silent full-inbox dump indistinguishable from "no filter". `VirtualMailboxFilterPayload` carries `min_length=1` to reject them at the schema boundary; the test pins the 422. A regression that drops `min_length` turns "filter by nothing" into "match everything" with no error. Clear a filter by OMITTING the key, never by sending `""`.

### Trap — `box` + `box_not_in` together silently returns zero rows

`_validate_box_exclusivity` rejects a payload carrying both with 422. Unguarded, the repository would emit `box = X AND NOT (box = ANY(Y))` at once → a perpetually-empty bandeja with no error signal. The test asserts the 422; a regression surfaces as an always-empty listing, not an exception.

### Trap — `box_not_in=[]` means "include TRASH/SPAM", not "use default"

An explicit empty list is a valid opt-in to see TRASH/SPAM (the repository guard is `is not None`, not truthiness — see `database_guide.md`). The test seeds TRASH/SPAM rows and asserts they appear. A truthiness-check regression collapses `[]` to the default exclusion and the rows vanish — this test is the only thing that catches it. The opt-in is NOT "show everything", though: the same test also seeds a `box='DELETED'` row and asserts it stays hidden, because `_build_filter_args` injects `DELETED` into every `box_not_in` branch (it is not a `FilterBox` value, so it can never be requested — see `repository_guide.md`). So under `box_not_in: []` the service hands the repository `["DELETED"]`, not `[]`; a test author who "extends" this to expect the DELETED row to surface is wrong. A companion test (`test_default_listing_excludes_deleted_rows`) pins the same carve-out on the default (no `box`) branch, isolating its assertion with a unique `subject_contains` so `total == 0` is exact.

### Trap — cross-account dedup winner is decided by `to_email` completeness

When the same `provider_message_id` exists under two `account_id`s (one OAuth account connected twice), `LIST_FILTERED_DISTINCT` collapses them and the row with a non-empty `to_email` wins regardless of `received_at`; `total` is `COUNT(DISTINCT provider_message_id)` (must not be 2). Pin BOTH the surviving `to_email` and `total == 1` — asserting only the count misses a winner-selection regression.

### Trap — pagination determinism needs the `(account_id, provider_message_id)` secondary sort

`ORDER BY received_at DESC` alone leaves same-second rows (mass newsletters) in arbitrary order, so OFFSET paging can repeat or skip a row. The determinism test seeds timestamp ties and asserts a stable full sweep across pages. A single-page assertion never catches this — the regression only shows when paging.

### Trap — PATCH/DELETE on a vanished vmbox must be 404, not 500 / silent 200

Race window between the ownership pre-check and the mutating SQL: on UPDATE the repository returns `None` → service raises `VirtualMailboxNotFound` (404); on DELETE it checks the affected-row count → 404. The historical UPDATE bug was the repo raising `QueryError("not found")` → `VirtualMailboxOperationError` (500). The tests pin PATCH-after-delete → 404 and DELETE-after-delete → 404; a refactor that re-raises on no-match (UPDATE) or skips the rowcount check (DELETE) silently restores the 500 / a misleading 200.

### Trap — the virtual listing ALWAYS groups by thread; `total` counts threads

`list_emails_for_virtual_mailbox` hardcodes `group_by_thread=True`, and filters (`is_favorite`, etc.) apply at the message level BEFORE grouping — two favourite messages sharing a `thread_id` collapse into one thread row. Tests asserting item count / `total` under a filter must expect thread counts, not message counts (e.g. two seeded favourites in one thread → one row, `total == 1`).

### Trap — `test_dev_login.py`: the TestClient host is `testclient`

Starlette's `TestClient` presents client host `testclient`, not `127.0.0.1`/`localhost`. To exercise the dev-login happy path the test sets `DEV_LOGIN_TRUSTED_HOSTS=testclient`; otherwise every call 403s `dev_login_not_localhost`. The happy path also **removes** the standard `require_session` auth override (which injects a fixed user) and relies on the cookie the endpoint itself sets, restoring the override in a `finally` — dev-login is one of the few endpoints whose entire purpose is to mint the session the override otherwise fakes.

### Trap — `test_admin_purge.py`: the happy path must seed an aged triplet

The purge only deletes blobs whose `email_attachments.last_accessed_at < now() - 30 days`. `_seed_expired_blob` stages the full `email_metadata` + `email_attachments` + `email_attachment_blobs` triplet with `last_accessed_at = now() - 45 days` inside the `isolated_db` transaction. Seeding only the blob without an aged `email_attachments` row would purge nothing and the test would assert `purged_count=0` for the wrong reason. The three states (`purge_disabled` 503 / `invalid_admin_token` 401 / executed) hang off the `ATTACHMENTS_PURGE_TOKEN` env var + `X-Admin-Token` header combination.

### Trap — `test_reply_context_endpoint.py`: 422 (not 502) on bad action, and BOTH builders are patched

`action` is a router-level `Literal`, so an invalid value is rejected by FastAPI with **422** before reaching the service's 502 path. The test named `test_invalid_action_returns_502` actually asserts 422 — the name is historical; do not "fix" it to expect 502. The endpoint resolves a manager through BOTH `emails_service.build_manager_for_accounts` AND `drafts_service.build_manager_for_accounts`, so the fake must be patched on both modules. `_seed_email_metadata` is mandatory (the `exists()` pre-check) and the Reply-All CC dedup only exercises when `email_address` is injected into the account row.

The same envelope subtlety applies to the composed-body size cap (`max_length=1_000_000` on `DraftCreate` / `DraftUpdate` / `EmailSendRequest`): a body over the cap is a **422** emitted by FastAPI's default `RequestValidationError` handler with the `{"detail": [...]}` shape, NOT the project's `{"error": {code, message, detail}}` envelope (no `RequestValidationError` handler is registered). Tests asserting "body too large" must read `detail[]`, never `error.code` — the project's standard `assert error.code == …` pattern does not apply to validation 422s.

### Trap — `test_copy_attachments_from_email.py`: patch only `drafts_service.build_manager_for_accounts`

The copy endpoint builds its provider clients from `drafts_service`, so the local `_patch_fake_manager` patches only that module. Patching `emails_service` instead leaves the real builder in place and the test falls through to a real provider call. The non-happy paths it covers (Outlook no-op `copied_count=0`, R-12 `already_copied`, `unavailable_at` short-circuit with no provider call, inline filtering) all hinge on the `skipped[]` structured array rather than the response status.

### `test_drafts_reply_metadata.py` is NOT redundant with `test_drafts.py`

It covers two invariants the plain drafts tests do not: the `COALESCE(EXCLUDED.col, drafts.col)` guard on `UPSERT_DRAFTS_BATCH` (a drafts sync must not clobber locally-persisted reply metadata with the NULLs the provider read path carries), and that `send_draft` reads the threading from the **local row**, not from the request body (a tampered client cannot rethread at send time). It asserts against the fake's `send_draft_with_attachments_reply_kwargs` tracker.

## GET Endpoint Testing Rules (mandatory)

GET endpoints that read exclusively from the database (no provider calls) are covered by integration tests with the same fidelity as E2E. GETs with external dependencies (e.g. cache-aside with provider fallback) need their own strategy documented per-endpoint.

1. **Use seeded data, not ephemeral data.** GET tests use the seeded fake data from migration `0010` via `seeded_test_client`. Do not create throwaway rows via POST just to test a GET — the seeded data is deterministic and provides known expected values.
2. **Assert exact content, not just status codes.** Verify the actual response body against known seeded values (exact counts, specific field values), not `len >= 1` or bare 200 checks.
3. **Cover all parameter variants.** Use `@pytest.mark.parametrize` for every valid combination of filter params.
4. **New DB-only GET endpoints follow these rules.** If a new GET reads data not covered by the current seed, extend the seed (new migration + update the data section below) before writing the tests.

### Seeded fake data (migration `0010_seed_fake_data_for_get_tests`)

Data inserted into the real database for testing DB-only GET endpoints. Not authenticated with any provider — exists only so tests can assert exact records.

Fixed UUIDs:

| Entity | ID |
|---|---|
| User | `11111111-1111-4000-a000-111111111111` |
| Gmail mailbox | `aaaaaaaa-aaaa-4000-a000-aaaaaaaaa001` |
| Outlook mailbox | `aaaaaaaa-aaaa-4000-a000-aaaaaaaaa002` |
| Gmail account | `bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001` |
| Outlook account | `bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002` |

User: `name=inventadoParaEndpointGet`, `email=inventadoParaEndpointGet@fake.test`, `google_sub=inventadoParaEndpointGet-google-sub`.

Accounts: Gmail label `Gmail inventada - inventadoParaEndpointGet` (`gmailinventada@gmail.com`), Outlook label `Outlook inventada - inventadoParaEndpointGet` (`outlookinventada@outlook.com`).

Email distribution per account (50 total each): `ALL_MAIL=30`, `SENT=10`, `TRASH=4`, `SPAM=6`. SENT rows have `from_email` = the account's email address and `from_name = inventadoParaEndpointGet`. TRASH rows have `previous_box='ALL_MAIL'`; SPAM rows have `previous_box=NULL`. Emails span `2026-03-01` to `2026-03-13` with unique subjects and varied `is_read` values. Full data also available at `tests/fixtures/seed_get_endpoints.json`.

## Attachments — invariants worth their tokens

### `send_draft_with_attachments_exc` is the correct injection point for send failures

The unified send path goes through `EmailManager.send_draft_with_attachments`, so a `FakeEmailClient(send_draft_exc=…)` injection is silently inert against `drafts_service.send_draft`. Use `send_draft_with_attachments_exc=EmailAttachmentSendFailed(detail={...})` to exercise the partial-success persistence path (D-27) end-to-end.

### Draft-attachment endpoints are local-only — no provider call

`POST /drafts/{id}/attachments` and `DELETE .../attachments/{aid}` write only to `draft_attachments`. Tests that monkeypatch `EmailManager.send_draft_with_attachments` do NOT need to extend that mock for attach/remove tests; the service never reaches the provider on these endpoints. The DELETE is intentionally **not idempotent at the row level**: a missing attachment_id surfaces 404, never 204. (Composer's optimistic-UI flow tolerates 404 by ignoring it — that's a frontend choice, not a contract.)

`position` is auto-assigned server-side per draft as `MAX(position)+1` (or `0` for the first row) and the response carries the resolved value. Tests that upload several attachments back-to-back must assert monotonic increment (`0, 1, 2, …`) — any change to the repository's position logic that breaks this contract regresses the composer's chip ordering.

### `test_has_attachments_invariant.py` — the B.lazy contract

Two test classes cover separate concerns: `TestHasAttachmentsInvariant` runs the SQL invariant query directly (and verifies that migration 0010's seeded data already satisfies it from the start); `TestRecomputeHasAttachments` calls the `recompute_has_attachments` helper directly against the live DB to verify flag-flip on non-inline insert, no-op on inline-only, and idempotency. Both classes must be updated when the helper or the SQL invariant changes — they are not redundant.

The four invariant states the SQL query enforces: `(false, 0 non-inline rows) ✅`, `(false, ≥1 non-inline rows) ❌`, `(true, 0 non-inline rows) ❌`, and inline-only attachments **must NOT** flip the flag to true (the COUNT subquery filters `is_inline=false`).

### `test_blocked_extensions_parity.py` — cross-boundary parity, no shared build step

The backend `BLOCKED_EXTENSIONS` constant and the frontend `blocked_extensions.json` must list the SAME extensions. There is no shared build step that derives one from the other — both are hand-maintained. The test asserts: set equality (same membership), length equality (catches duplicates), the frontend list is alphabetically sorted, and frontend entries are lowercase without leading dots. Mutual-order equality is **not** asserted at this layer — the backend constant's order is governed by its own unit test.
