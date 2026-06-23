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

### Test configuration in `e2e_config.py`

Pre-existing test account identifiers are centralized in `e2e_config.py` with env var overrides. These accounts must exist in the real database with valid OAuth refresh tokens before running the suite — the suite never creates or deletes them.

`SEND_RECIPIENT` is the destination address used by every send and draft test. Override via `E2E_SEND_RECIPIENT` when running the suite where the default address is not available.

### Pre-existing test accounts — one per provider

The suite requires **one real, authenticated account per client implementation in `backend/core/email/`**. Each account is linked to a real mailbox owned by the developer and must have valid OAuth refresh tokens in the `accounts` table before the suite is run.

The `display_label` values live only in the database (not in any code file), so they must be documented here:

| provider | account_id | mailbox_id | display_label |
|---|---|---|---|
| gmail | `9805b672-032b-4d74-9696-4db53a5eb512` | `28a83414-36f5-4115-ab61-977d5a06a8e1` | `pruebaGmail` |
| outlook | `3c55eb17-9d5e-4d31-a3b5-14c6c24279b9` | `b61e15d5-153e-42ee-a4c6-2c943bd13c07` | `pruebaOutlook` |

**Extension rule**: when a new provider is added to `backend/core/email/`, create a corresponding real account in the database, register its identifiers in `e2e_config.py`, add the row to this table, and extend the suite with provider-specific tests (see Extension Checklist below).

## Traps and Behavioral Contracts

### Auth lifecycle tests must be LAST in the flow

`POST /auth/logout` invalidates the session cookie — any test running after it gets 401. The logout test and the 401-verification test live in the final section of `test_full_flow.py` for this reason. When adding new tests, always insert them **before** the logout section.

### Pre-existing test data is sacred

The pre-existing user, mailboxes, and accounts defined in `e2e_config.py` must NEVER be deleted, edited, or otherwise mutated by the suite. Provider operation tests (sync, send, drafts, spam, trash) use these accounts but only with additive/idempotent operations, plus cleanup of what the test itself created.

### Draft tests — cleanup pattern

Each draft test creates a draft at the real provider, verifies it, and cleans up via the DELETE endpoint or raw SQL `DELETE FROM drafts WHERE ...` in a `finally` block (safety net for the case where the send/update/delete failed mid-test). The verify step and the cleanup live in the **same** test function (per `common_mistakes.md` § 1) — do not split them into separate tests.

When drafts are **synced** from the provider (Section 5c), the draft is intentionally left at the provider after the test; only local rows are cleared via `_clear_local_drafts`. Provider-side cleanup for drafts is covered by the explicit delete and send sections.

### Provider-specific behavior worth knowing

- **Outlook re-wraps and normalises HTML bodies server-side.** Draft bodies now ship as HTML (`contentType: HTML`; reversed D-31, API field is `body`, not `body_html`). On read, Graph returns the content wrapped in a full `<html><head><meta …us-ascii></head><body>…</body></html>` document — `_parse_outlook_draft` flattens it back to a fragment, but the round-trip is NOT byte-for-byte (Graph also rewrites whitespace). Outlook draft-body tests MUST assert by containment (`"E2E updated body" in data["body"]`), never by equality. Gmail's `raw` round-trips closer to verbatim but its `_parse_gmail_draft` strips one trailing newline from the HTML part — assert by containment there too. The send/forward flow tests should additionally confirm the sent body arrives as HTML at the provider (Gmail: a `text/html` part inside `multipart/alternative`; Outlook: `body.contentType == "html"`).
- **Attachments e2e tests (`test_46e..m`).** Cover the local-only draft-attachment lifecycle (POST → DELETE without provider contact, one Gmail + one Outlook variant), the send-with-attachment paths against both providers (`test_46h`/`test_46i`), the three states of the admin purge endpoint (`test_46j..l`) — `503 purge_disabled` when `ATTACHMENTS_PURGE_TOKEN` is unset, `401 invalid_admin_token` when set but the header is wrong, `200 {purged_count, freed_bytes}` otherwise — and the cache-aside **download** endpoint (`test_46m`). The blocked-extension test runs against Gmail only because the blocklist is uniform (D-04a). **Accepted gaps**: `test_46l` does NOT pre-seed an expired blob — it asserts `purged_count >= 0`/`freed_bytes >= 0`, so a no-op purge against a clean database passes trivially. `test_46m` reuses `bootstrap_attachment_message` (Gmail path), so it only exercises the real download when the bootstrap produces a real downloadable row — for Gmail the cache-aside `/content` priming always persists one (the `'bootstrap'` synthetic row is Outlook-only, gated behind `_force_has_attachments`). The 30 MB multipart cap still has no E2E coverage (unit + integration only).

### Forward tests bootstrap real provider state

`test_forward_flow_gmail.py` and `test_forward_flow_outlook.py` need an inbox row with `has_attachments = TRUE`. When the account does not have one (clean machine, first run), `bootstrap_attachment_message` in `_forward_helpers.py` sends a real self-addressed email with a PDF attachment from the test account to its own `email_address`, polls `sync-metadata` until the SENT `provider_message_id` lands locally, primes the cache-aside content endpoint, and forces `has_attachments = TRUE` via direct SQL (the `_force_has_attachments` workaround applies to any provider that comes back without the flag after priming — typically Outlook, whose `GET /me/messages/{id}/attachments` returns an empty list for the SENT folder copy even when the draft had uploaded one; the binary is preserved at the provider so the downstream forward flow still works against Graph in real time). **`_force_has_attachments` ALSO inserts a non-inline `email_attachments` row** alongside the flag flip: without it the forced `has_attachments=TRUE` would have zero backing attachment rows and turn the integration test `test_has_attachments_invariant.py::test_seeded_state_satisfies_invariant` red on the shared DB (it scans every row, not just migration-0010 seeds — see `repository_guide.md`). The side effect is that each run leaves at least one self-addressed message in the real test account.

### Reply / Forward flow tests — threading assertion and provider asymmetries

`test_reply_flow_gmail.py` / `test_reply_flow_outlook.py` and the two forward-flow files exercise the full `reply-context → create draft → send` path against the real provider. Contracts worth knowing:

- **The reply source must have a provider-retrievable body, and only the forward bootstrap reliably yields one.** `bootstrap_reply_source` in `_reply_helpers.py` reuses `_forward_helpers.bootstrap_attachment_message` (the self-addressed message the forward flow already proves round-trips a `<blockquote>` on both providers) and looks up its `thread_id`; the reply flow ignores the inherited attachment. Two cheaper sources were tried and fail on the real accounts: (1) *"most recent ALL_MAIL row"* — a message sent to an **external** recipient leaves only a body-less **shadow copy** in the Outlook account's `ALL_MAIL` (the body lives on the `SENT` item under a different id); replying to it yields just the attribution line (`build_quoted_body_html` correctly omits the quote on an empty source body). (2) *"self-send and reply to the sent id"* — the Outlook test account's address is a **Gmail** address, so the send never loops back to the Outlook mailbox; only `SENT` copies remain and Graph returns an **empty body** for those via the reply-context `$select=body` read. Gmail keeps one id for the sent+received copy so it works there, but the source has to be reliable for both. The content-fetch step at the end of each reply/forward test passes `?account_id=...` because `GET /content` requires it.
- **Threading assertion is a bounded poll, not a soft skip.** The post-send `email_metadata` persist is best-effort, so the test re-runs `sync-metadata` in a bounded loop until the sent `provider_message_id` row appears, then asserts hard that its `thread_id` equals the original. An earlier `if row is not None: assert …` shape verified nothing when the persist soft-failed — do not regress to it.
- **Outlook Forward: do NOT edit the subject beyond `Fwd:`.** Editing the subject of a `createForward` draft makes Graph reassign a new `conversationId` on save, detaching the sent message from the original thread. Gmail does not exhibit this (forwards may start a new thread).
- **`copy-from-email` asymmetry.** The forward flow asserts `copied_count >= 1` for Gmail (download + re-upload) but `copied_count == 0` for Outlook (`createForward` already inherited the attachments server-side — the endpoint is a no-op).
- Endpoints first exercised end-to-end here: `GET .../reply-context` and `POST .../drafts/{pdid}/attachments/copy-from-email`.

### Rate limiting stays OFF in E2E (deliberate, #11e)

The suite never sets `RATE_LIMIT_ENABLED`, so per-client throttling is inert here. This is a decision, not an oversight: the limiter short-circuits **before** any provider call, so a real Gmail/Outlook round trip adds nothing the integration suite (real app + real DB + `TestClient`) does not already cover for it — and turning it on would actively harm E2E, since the suite's repeated real sends/syncs against the seeded accounts could trip a 429 mid-flow. Deterministic rate-limit coverage lives entirely in the unit + integration tiers.

### Safety-net cleanup in fixture teardown

The `created_resources` fixture tracks temp mailbox IDs and session IDs. On teardown, `e2e_session` deletes them via direct SQL, ensuring no orphan data remains even if a test fails mid-flow.

### `create_e2e_schema` — session-scoped, autouse

Runs Alembic migrations against the real E2E database once per session using `backend/database/alembic.ini`. If the database exists but has no `alembic_version` row, it stamps the existing tables as `0001_initial_schema` before upgrading to `head`. This keeps the suite idempotent across cold starts and post-migration runs.

## Search endpoint coverage — `test_38a`–`test_38g`

`GET /mailboxes/{mailbox_id}/emails?q=…` (free text + Gmail-style operators) and its pagination envelope are exercised by the `test_38*` block. Why the dedicated mention here — each test is the executable spec for a contract not visible from the router signature alone:

- The contract on `q` free text (OR semantics across `subject`, `from_email`, `from_name`, AND between tokens, literal substring, no stemming) is pinned by `test_38a` (single `account_id`, every row matches the needle in ≥1 searchable column) and `test_38b` (unified view, no `account_id`: every row belongs to **some** account inside the requested mailbox — the unified path must not leak rows from foreign mailboxes).
- `test_38c` pins the `EmailPageOut` envelope against the real account: `total` is the whole filtered set (not the page length), and two adjacent pages share no `provider_message_id` — proving OFFSET paging is stable thanks to the total-ordering tie-break. Skips when the account has < 3 emails.
- `test_38d` is the spec for the `is:read` / `is:unread` operators (every returned row must satisfy the read-state predicate). Both halves stay in one test — they are the same logical contract.
- `test_38e` pins the **asymmetry** of `in:`: with `box=ALL_MAIL&q=in:sent` every row is in `SENT`, and `total` equals a direct `box=SENT` query — so the override reaches the COUNT predicate, not only the listing. A regression that applied `in:` to the page but not the count would pass a bare status check.
- `test_38f` pins two things about ANDed operators: contradictory ones (`is:read is:unread`) resolve to **zero rows in SQL** (`items == []`, `total == 0`), never a 422 (the executable counterpart to the `repository_guide.md` "contradictions resolve in SQL, not via code" note); AND, against an `is:read`-only baseline measured first in the same test, that combining operators **narrows, never widens** (`total <= base_total`).
- `test_38g` is the only place that proves the deployed runtime carries the IANA tz database (`tzdata`): `before:`/`after:` parse the date as midnight in `Europe/Madrid`, so a missing `tzdata` would 500 these requests — a failure neither unit nor integration can catch (they share the interpreter; only E2E exercises the real runtime). Fixed far-past/far-future boundaries keep it deterministic against a live inbox.

## Email-content cache-aside coverage — `test_46a`–`test_46d`

MISS/HIT pair per provider (`46a`/`46b` Gmail, `46c`/`46d` Outlook) over `GET .../emails/{id}/content`. Two couplings invisible from the assertions:

- The `_delete_email_content` between `sync-metadata` and the GET is **load-bearing**: `sync-metadata` schedules a background content prefetch of recent unread mail that may pre-cache the target, so without the delete the "MISS" GET silently resolves as a HIT and the provider-fetch branch goes untested (the `is None` assert right after pins the restored MISS).
- The HIT tests assert `fetched_at` **unchanged** across the read — the executable guard for the sliding-TTL invariant (a hit bumps only `last_accessed_at`, never `fetched_at`). A regression that re-stamped `fetched_at` on a hit turns these red.

## Recipient-autocomplete coverage — `test_46n`

`GET /contacts/suggestions?q=…` is DB-only (no provider call). The needle is the first 5 chars of `SEND_RECIPIENT`'s local part, so the SENT rows the send/draft tests leave carry it on `to_email` and make a match likely without making it required — the assertion is **containment-only** (every `{email, name}` item carries the fragment) and tolerates an empty list, exactly like `test_38a`/`38b` against a live inbox. The `q` < 2 → 422 boundary rides the **same** test (`common_mistakes.md` § 1).

## Unread-count badge coverage — `test_46o` (Gmail) / `test_46p` (Outlook)

`GET .../emails/unread-count?box=ALL_MAIL|SPAM` is DB-only (no provider call). Contracts not visible from the router:

- The control count has its OWN helper `_count_unread_by_box` (`is_read = FALSE`), distinct from the sibling `_count_by_box` that ignores read state — reusing the latter would make the assertion vacuously true. The endpoint counts **individual messages, not threads**, so its `total` deliberately diverges from a grouped listing's thread `total`; the per-row control `COUNT(*)` is what pins that distinction.
- `test_46o` also rides the invalid-`box` → 422 boundary in the same test (`common_mistakes.md` § 1, like `test_46n`) and a read→restore mutation proving the badge tracks a real state change (restored in a `finally`, the "sacred seeded data" rule).
- `test_46p` exists because each seeded mailbox holds a **single account**, so `test_46o` cannot exercise the per-account back-fill invariant — the breakdown must carry an entry for EVERY account of the mailbox (0-filled when the GROUP BY omits it), never a subset — nor the Outlook provider path. It asserts `total == sum(breakdown)` and that the breakdown's account set equals `GET /accounts`. A Gmail-only suite would pass even if a multi-account mailbox silently dropped its zero-unread accounts.

## Favourites coverage — `test_favorites_flow_gmail.py` / `test_favorites_flow_outlook.py` (`test_58`–`test_63`)

The two suites exercise `PATCH .../favorite`, `POST /favorites/sync` and `GET /emails?favorite=true` against the real provider, one file per implementation in `backend/core/email/`. The load-bearing contract is **state restoration, not the assertions**: the favourite toggle mutates real provider state (Gmail `STARRED` / Outlook `flag`), so each toggle test captures the chosen row's original `is_favorite` and restores it in a `finally` block — the seeded test account must look identical before and after the run (the "pre-existing test data is sacred" rule applies to the favourite mark too, not just to rows/accounts). The toggle is Provider-First and idempotent, so the restore is always safe. The sync assertion is intentionally loose (`favorites_synced <= total_synced`, both `>= 0`) because the live account's favourite count is not fixed; do not tighten it to an exact number against a mutable inbox. The Outlook retry path (`set_favorite` / `list_favorite_ids` now retry transient throttling honouring `Retry-After`) is **not** directly assertable here — it only engages on a real provider hiccup; its deterministic coverage lives in the unit suite. The listing tests pin `group_by_thread=false` deliberately: Favoritos is **always ungrouped** by product decision (the backend applies no `favorite`-coupled grouping override, so `favorite=True`+`group_by_thread=True` is valid "group the favourites" SQL that is simply not a product surface). The hardcoded `false` is the executable spec of that decision — do not "fix" it to the router default or flip it to exercise grouping, which would silently change what the test pins.
