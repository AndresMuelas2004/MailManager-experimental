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
