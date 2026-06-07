> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# E2E Tests Guide

> **General rules**: this test layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Design Decisions

### Fully automated — no interactive steps

The suite runs without any browser interaction. Authentication is handled by inserting a session directly into the `sessions` table via `psycopg2` (separate from the app's pool); `require_session` then validates it against the real database.

Three endpoints are **excluded** from the automated suite because they require interactive OAuth or depend on it, and are verified manually with scripts:

| Endpoint | Reason |
|---|---|
| `POST /auth/google` | Initiates interactive browser OAuth flow |
| `POST /mailboxes/{mid}/accounts/{aid}/connect` | Initiates interactive per-provider OAuth flow |
| `DELETE /auth/me` | Cannot create a test user without `POST /auth/google` |

### `GOOGLE_CLIENT_ID` derived automatically

`GOOGLE_CLIENT_ID` is **not** required as an env var — `_setup_google_client_id` extracts the `client_id` from the credentials file pointed to by `MIA_GMAIL_CREDENTIALS_PATH` at runtime.

### Test configuration in `e2e_config.py`

Pre-existing test account identifiers are centralized in `e2e_config.py` with env var overrides. These accounts must exist in the real database with valid OAuth refresh tokens before running the suite — the suite never creates or deletes them.

| Identifier | Env override | Default |
|---|---|---|
| `TEST_USER_ID` | `E2E_TEST_USER_ID` | developer's pre-seeded user UUID |
| `GMAIL_MAILBOX_ID` | `E2E_GMAIL_MAILBOX_ID` | `28a83414-36f5-4115-ab61-977d5a06a8e1` |
| `OUTLOOK_MAILBOX_ID` | `E2E_OUTLOOK_MAILBOX_ID` | `b61e15d5-153e-42ee-a4c6-2c943bd13c07` |
| `GMAIL_ACCOUNT_ID` | `E2E_GMAIL_ACCOUNT_ID` | `9805b672-032b-4d74-9696-4db53a5eb512` |
| `OUTLOOK_ACCOUNT_ID` | `E2E_OUTLOOK_ACCOUNT_ID` | `3c55eb17-9d5e-4d31-a3b5-14c6c24279b9` |
| `SEND_RECIPIENT` | `E2E_SEND_RECIPIENT` | `muelonmuelon12@gmail.com` |

`SEND_RECIPIENT` is the destination address used by every send and draft test. Override via `E2E_SEND_RECIPIENT` when running the suite where the default address is not available.

### Pre-existing test accounts — one per provider

The suite requires **one real, authenticated account per client implementation in `backend/core/email/`**. Each account is linked to a real mailbox owned by the developer and must have valid OAuth refresh tokens in the `accounts` table before the suite is run.

The `display_label` values live only in the database (not in any code file), so they must be documented here:

| provider | account_id | mailbox_id | display_label |
|---|---|---|---|
| gmail | `9805b672-032b-4d74-9696-4db53a5eb512` | `28a83414-36f5-4115-ab61-977d5a06a8e1` | `pruebaGmail` |
| outlook | `3c55eb17-9d5e-4d31-a3b5-14c6c24279b9` | `b61e15d5-153e-42ee-a4c6-2c943bd13c07` | `pruebaOutlook` |

**Extension rule**: when a new provider is added to `backend/core/email/`, create a corresponding real account in the database, register its identifiers in `e2e_config.py`, add the row to this table, and extend the suite with provider-specific tests (see Extension Checklist below).

### Test independence — no global skip-all

Each test checks its own prerequisites via `flow_state` keys using the `_require()` helper. If a dependency test fails, only the dependent test is `SKIPPED`; independent tests still run. There is no global "skip everything after the first failure" behavior.

## Traps and Behavioral Contracts

### Auth lifecycle tests must be LAST in the flow

`POST /auth/logout` invalidates the session cookie — any test running after it gets 401. The logout test and the 401-verification test live in the final section of `test_full_flow.py` for this reason. When adding new tests, always insert them **before** the logout section.

### Pre-existing test data is sacred

The pre-existing user, mailboxes, and accounts defined in `e2e_config.py` must NEVER be deleted, edited, or otherwise mutated by the suite. Provider operation tests (sync, send, drafts, spam, trash) use these accounts but only with additive/idempotent operations, plus cleanup of what the test itself created.

### Draft tests — cleanup pattern

Each draft test creates a draft at the real provider, verifies it, and cleans up via the DELETE endpoint or raw SQL `DELETE FROM drafts WHERE ...` in a `finally` block (safety net for the case where the send/update/delete failed mid-test). The verify step and the cleanup live in the **same** test function (per `common_mistakes.md` § 1) — do not split them into separate tests.

When drafts are **synced** from the provider (Section 5c), the draft is intentionally left at the provider after the test; only local rows are cleared via `_clear_local_drafts`. Provider-side cleanup for drafts is covered by the explicit delete and send sections.

### Provider-specific behavior worth knowing

- **Outlook normalises plain-text bodies server-side** (may add a trailing newline / whitespace tweak). Outlook tests assert by containment (`"E2E updated body" in data["body"]`), not byte-for-byte equality. The earlier HTML-wrapping behaviour (`<html>/<body>` injection on plain HTML inputs) no longer applies — D-31 ships every draft body as `text/plain` to both providers, and the API field is `body` (not `body_html`).
- **Send-draft response IDs differ by provider**: Gmail returns a **new** `provider_message_id` (Gmail creates a new Message on send, different from the draft ID). Outlook returns the **same** ID as the draft thanks to `Prefer: IdType="ImmutableId"` used at draft creation.
- **Email content cache-aside tests (`test_46a..d`).** Cover the MISS → persist and HIT → serve-from-DB paths for both providers. `test_46a` (Gmail MISS) and `test_46c` (Outlook MISS) explicitly delete the `email_content` row before calling the endpoint and assert the row was written after the response; `test_46b` (Gmail HIT) and `test_46d` (Outlook HIT) depend on the prior MISS via `flow_state` keys (`gmail_content_msg_id` / `gmail_content_fetched_at` / `outlook_content_*`) and verify that `fetched_at` is unchanged on the second call — proving the provider was not re-fetched. Test order is load-bearing: 46a before 46b, 46c before 46d. `test_38` and `test_39` cover list/get content too, but without the explicit MISS/HIT split — `test_46a..d` are the authoritative cache-aside spec.
- **Attachments e2e tests (`test_46e..m`).** Cover the local-only draft-attachment lifecycle (POST → DELETE without provider contact, one Gmail + one Outlook variant), the send-with-attachment paths against both providers (`test_46h`/`test_46i`), the three states of the admin purge endpoint (`test_46j..l`) — `503 purge_disabled` when `ATTACHMENTS_PURGE_TOKEN` is unset, `401 invalid_admin_token` when set but the header is wrong, `200 {purged_count, freed_bytes}` otherwise — and the cache-aside **download** endpoint (`test_46m`). The blocked-extension test runs against Gmail only because the blocklist is uniform (D-04a). **Accepted gaps**: `test_46l` does NOT pre-seed an expired blob — it asserts `purged_count >= 0`/`freed_bytes >= 0`, so a no-op purge against a clean database passes trivially. `test_46m` reuses `bootstrap_attachment_message` (Gmail path), so it only exercises the real download when the bootstrap produces a real downloadable row — for Gmail the cache-aside `/content` priming always persists one (the `'bootstrap'` synthetic row is Outlook-only, gated behind `_force_has_attachments`). The 30 MB multipart cap still has no E2E coverage (unit + integration only).

### Forward tests bootstrap real provider state

`test_forward_flow_gmail.py` and `test_forward_flow_outlook.py` need an inbox row with `has_attachments = TRUE`. When the account does not have one (clean machine, first run), `bootstrap_attachment_message` in `_forward_helpers.py` sends a real self-addressed email with a PDF attachment from the test account to its own `email_address`, polls `sync-metadata` until the SENT `provider_message_id` lands locally, primes the cache-aside content endpoint, and forces `has_attachments = TRUE` via direct SQL (the `_force_has_attachments` workaround applies to any provider that comes back without the flag after priming — typically Outlook, whose `GET /me/messages/{id}/attachments` returns an empty list for the SENT folder copy even when the draft had uploaded one; the binary is preserved at the provider so the downstream forward flow still works against Graph in real time). **`_force_has_attachments` ALSO inserts a non-inline `email_attachments` row** alongside the flag flip: without it the forced `has_attachments=TRUE` would have zero backing attachment rows and turn the integration test `test_has_attachments_invariant.py::test_seeded_state_satisfies_invariant` red on the shared DB (it scans every row, not just migration-0010 seeds — see `repository_guide.md`). The side effect is that each run leaves at least one self-addressed message in the real test account.

### Reply / Forward flow tests — threading assertion and provider asymmetries

`test_reply_flow_gmail.py` / `test_reply_flow_outlook.py` and the two forward-flow files exercise the full `reply-context → create draft → send` path against the real provider. Contracts worth knowing:

- **Threading assertion is a bounded poll, not a soft skip.** The post-send `email_metadata` persist is best-effort, so the test re-runs `sync-metadata` in a bounded loop until the sent `provider_message_id` row appears, then asserts hard that its `thread_id` equals the original. An earlier `if row is not None: assert …` shape verified nothing when the persist soft-failed — do not regress to it.
- **Outlook Forward: do NOT edit the subject beyond `Fwd:`.** Editing the subject of a `createForward` draft makes Graph reassign a new `conversationId` on save, detaching the sent message from the original thread. Gmail does not exhibit this (forwards may start a new thread).
- **`copy-from-email` asymmetry.** The forward flow asserts `copied_count >= 1` for Gmail (download + re-upload) but `copied_count == 0` for Outlook (`createForward` already inherited the attachments server-side — the endpoint is a no-op).
- Endpoints first exercised end-to-end here: `GET .../reply-context` and `POST .../drafts/{pdid}/attachments/copy-from-email`.

### Safety-net cleanup in fixture teardown

The `created_resources` fixture tracks temp mailbox IDs and session IDs. On teardown, `e2e_session` deletes them via direct SQL, ensuring no orphan data remains even if a test fails mid-flow.

### `create_e2e_schema` — session-scoped, autouse

Runs Alembic migrations against the real E2E database once per session using `backend/database/alembic.ini`. If the database exists but has no `alembic_version` row, it stamps the existing tables as `0001_initial_schema` before upgrading to `head`. This keeps the suite idempotent across cold starts and post-migration runs.

## Extension Checklist — Adding a New Provider

When adding a new provider:

- [ ] Add account creation for the provider.
- [ ] Add connect step for the provider.
- [ ] Add operation steps (send, fetch, read-status, spam, trash) for the provider.
- [ ] Add a draft creation test for the provider.
- [ ] Add two draft sync tests (single-account and mailbox-wide) for the provider.
- [ ] Add a draft listing test for the provider.
- [ ] Add a draft update test for the provider — must create a draft first and clean up the local row afterward.
- [ ] Add a draft deletion test for the provider.
- [ ] Add a draft send test for the provider — must create a draft first, send it, and verify the local row was deleted. Safety-net cleanup in a `finally` block.
- [ ] Add a reply flow test for the provider (`reply-context → create reply draft → send → threading assertion via bounded sync-metadata poll`).
- [ ] Add a forward flow test for the provider (bootstrap an inbox message with an attachment, then `reply-context?action=forward → create forward draft → copy-from-email → send`, asserting the provider's `copied_count` expectation).
- [ ] Ensure flow assertions include the new provider behavior.

The E2E suite should always represent the full set of supported providers.

## Search endpoint coverage — `test_38a` / `test_38b`

`GET /mailboxes/{mailbox_id}/emails?q=…` is exercised by `test_38a_search_emails_single_account` and `test_38b_search_emails_unified_mailbox`. Why the dedicated mention here:

- The contract on `q` (OR semantics across `subject`, `from_email`, `from_name`, AND between tokens, literal substring, no stemming) is not visible from the router signature alone — the test is the executable spec for it. A change in the predicate that breaks one column's contribution must surface here.
- 38a scopes the search to a single `account_id` and verifies every returned row matches the needle in at least one of the three searchable columns.
- 38b drops the `account_id` filter (unified mailbox view) and verifies every returned row belongs to **some** account inside the requested mailbox AND matches the needle. This pins down that the unified path does not leak rows from foreign mailboxes when no `account_id` is supplied — a contract the router signature alone does not express.
