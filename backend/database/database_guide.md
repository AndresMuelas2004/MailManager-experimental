> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# Database Layer Guide

> **General rules**: this layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Token security model

- Token columns (`access_token_encrypted`, `refresh_token_encrypted`, …) live directly on the `accounts` table (merged from a separate `tokens` table in migration 0005).
- **All new writes are encrypted** via Fernet (`TOKEN_ENCRYPTION_KEY` + `TOKEN_ENCRYPTION_KEY_ID`).
- A malformed `TOKEN_ENCRYPTION_KEY` raises `SettingsError` **immediately** from `get_fernet()` — never silently treated as "key absent". Do not add a fallback here.
- `TOKEN_PLAINTEXT_FALLBACK_ENABLED` toggles whether legacy plaintext columns are still read. On a plaintext hit, `AccountStore` attempts a best-effort lazy backfill to the encrypted columns. The backfill **never propagates failures** — it logs a warning and retries on the next read.
- The plaintext columns (`access_token`, `refresh_token`) are **deprecated** and remain only for migration compatibility. A future migration will remove them once legacy data is fully backfilled.

## `email_address` column (migration 0009)

- Plain text, **not encrypted** — it is not a secret.
- Written during `upsert_tokens`; may be `NULL` if the best-effort provider fetch during `authenticate` failed.
- The UPSERT queries use `COALESCE(%(email_address)s, email_address)` so silent-refresh upserts (which don't carry an `email_address` — only the interactive `authenticate` flow fetches it) cannot erase a previously stored value. **Do not remove the `COALESCE` thinking it is a leftover** — it is load-bearing; without it, any refresh blanks the column.

## `AccountStore.get_tokens()` returns `None` instead of raising

Missing-token scenarios (no row, encrypted columns absent with fallback disabled, etc.) return `None`. The service layer maps `None → AccountNotConnected`. **Input-validation** errors (`TokenValidationError` for blank provider) still propagate — only the absence of a token is expressed as `None`.

## `PgUserStore.get_by_email` omits the `InvalidTextRepresentation` guard its siblings carry

`PgUserStore.get_by_id` and `PgUserStore.delete` both swallow `psycopg2.errors.InvalidTextRepresentation` and collapse to `None` / `False` (a malformed UUID is treated as "not found"). `PgUserStore.get_by_email` deliberately omits that guard: `users.email` is `VARCHAR`, not UUID, so the only way `InvalidTextRepresentation` could surface here is a genuine programming error elsewhere — silencing it would mask real bugs. Do NOT add the guard "for consistency" with the siblings.

## Trash — `previous_box` + `DELETED` box value (migration 0008)

- `previous_box VARCHAR(20)` nullable, CHECK allows `ALL_MAIL`, `SENT`, `SPAM`. `move_to_trash_batch` copies the current `box` into `previous_box` before setting `box = 'TRASH'`. `restore_from_trash_batch` uses `COALESCE(previous_box, 'ALL_MAIL')`. Rows where `previous_box IS NULL` go through `restore_from_trash_discovered_batch`, which receives the discovered box from the caller.
- `DELETED` is a new allowed value in the `box` CHECK. Soft-delete marker for emails removed from trash via the no-op `delete_messages` path (see `core_guide.md`). The `CASE` expression in `UPSERT_EMAIL_METADATA_BATCH` and `UPDATE_LABELS_BATCH` **preserves `DELETED` when the incoming provider box is `TRASH`** (would undo the user's explicit delete), but **overwrites `DELETED` on any other incoming box** — interpreted as "the user restored it manually at the provider". Don't simplify this CASE into a plain `EXCLUDED.box` assignment.

## `EmailMetadataStore` — trash batch guardrails

Every trash-related batch method adds a **box-state precondition** directly in the SQL — these are not client-side checks, they're WHERE clauses:

- `mark_as_deleted_batch`, `restore_from_trash_batch`, `restore_from_trash_discovered_batch` → **only update rows where `box = 'TRASH'`**. Rows in a different box are silently skipped (race-condition safety — if a sync meanwhile moved the row out of trash, the trash operation must not touch it).
- `move_to_trash_batch` → **only updates rows where `box` is NOT already `'TRASH'` or `'DELETED'`** — idempotent move, and does not downgrade a soft-deleted row back to trash.

`restore_from_trash*` and `move_to_trash_batch` all delegate to `_execute_batch_values`, which wraps `psycopg2.extras.execute_values` with the standard error handling.

## `email_content` composite FK (migration 0013) + `EmailMetadataStore.exists`

`email_content` uses the **shared primary key pattern**: composite PK `(provider_message_id, account_id)` + composite FK to `email_metadata(provider_message_id, account_id) ON DELETE CASCADE` (constraint `email_content_metadata_fkey`). No direct FK to `accounts` — the cascade chain `accounts → email_metadata → email_content` is fully transitive, so account deletion still wipes both tables.

**Service-layer contract:** because the FK target is `email_metadata`, `get_email_full_content` **must** verify the metadata row exists before any `email_content` upsert — otherwise the upsert surfaces as `ForeignKeyViolation → 500`. `EmailMetadataStore.exists(account_id, provider_message_id)` is the lightweight probe (`SELECT 1 … LIMIT 1`) used for the pre-check. It handles `InvalidTextRepresentation` gracefully (returns `False` for malformed UUIDs) so bad inputs surface as 404 instead of 500.

## `DraftStore` — contract invariants

- **Composite PK `(provider_draft_id, account_id)`.** Every `get` / `update` / `delete` takes both; single-arg overloads do not exist. `provider_draft_id` is always present because the repo follows the Provider-First Rule (the provider creates the draft and returns its ID before any local persistence).
- **`DatabaseError` re-raise guard is mandatory in every method.** The pattern is `except DatabaseError: raise` **before** `except psycopg2.Error` / `except Exception`. `connection.get_connection()` can raise `ConnectionPoolError` (a `DatabaseError` subclass) — without this guard, pool exhaustion gets re-wrapped as `QueryError` and disappears from the error signal.
- **`UPDATE ... RETURNING` yielding no row → `QueryError("Draft row to update not found.")`.** Defensive check for a race where another caller deleted the draft between the service's pre-check (`DraftStore.get`) and the update/delete. Without the explicit raise, the operation would silently succeed with a `None` return.
- **`list_by_account` / `list_by_mailbox` / `get` return `[]` / `None` on malformed UUID** (`InvalidTextRepresentation`) so service code can treat "unknown account" as an empty result instead of a 500. `delete` intentionally does **not** have this guard — a malformed UUID there is a programming error and must surface.

### `DraftStore.list_by_account` / `list_by_mailbox` co-aggregate `draft_attachments` in-query

Both listing methods embed a correlated subquery (`json_agg` over `draft_attachments` ordered by `position`) and surface it as an `attachments` field on every returned dict. The field is **always** present and defaults to `[]` (never `NULL`) via `COALESCE`. The shared `_LIST_DRAFTS_SELECT` SQL fragment is the single source of shape — modifying it propagates to both variants.

The `blob` column is **deliberately excluded** from the subquery. Adding it would silently turn the listing endpoint into a multi-MB-per-draft response. The bound is D-03 (≤25 attachments per draft), so the JSON payload stays predictable; the binaries flow through the dedicated `LIST_DRAFT_ATTACHMENTS_BY_DRAFT_WITH_BLOB` send-time path instead.

## `DraftStore.replace_all_for_account` — atomic upsert + delete-missing

Single transaction:

1. Non-empty `drafts` → `psycopg2.extras.execute_values` with `UPSERT_DRAFTS_BATCH`. Each tuple carries the caller-provided `created_at` / `updated_at` (the service forwards `DraftMetadata.created_at` / `.updated_at`), so freshly inserted rows preserve "first-time-seen-at-provider" semantics instead of collapsing both timestamps to `now()`. `ON CONFLICT (provider_draft_id, account_id) DO UPDATE` refreshes recipients + subject + body + `updated_at = now()`, but **never touches `created_at`** — it preserves "first time we saw this draft locally", even across multiple syncs.
2. `DELETE_DRAFTS_MISSING_FOR_ACCOUNT` runs **unconditionally, even when `drafts == []`** — intentional: an empty provider response means "no drafts here anymore", so local state is wiped for that account. Do not add a guard to skip the DELETE on empty input.

Invalid `account_id` format raises `QueryError` (wrapped from `InvalidTextRepresentation`).

## `drafts` table invariants (migration 0012, renamed in 0022)

- Recipients stored as `TEXT[] NOT NULL DEFAULT '{}'`. psycopg2 maps Python `list[str]` ↔ PostgreSQL `TEXT[]` transparently.
- `subject` and `body` are `TEXT NOT NULL DEFAULT ''` — empty drafts are valid. The column was named `body_html` until migration 0022 renamed it to `body` (D-31): the historical composer was an HTML editor; the current composer is a plain `<textarea>` and both providers receive `text/plain` MIME, so the column matches the actual semantics. Every layer was rippled (queries, repository, schemas, provider clients, draft API parameters).
- `created_at` / `updated_at` are `TIMESTAMPTZ NOT NULL DEFAULT now()`. The `DEFAULT now()` fires only for `INSERT_DRAFT` (which doesn't list these columns). `UPSERT_DRAFTS_BATCH` always passes them explicitly from provider-reported timestamps.

## `LIST_FILTERED` traps (email metadata search)

- **`unaccent` is a runtime dependency.** The query wraps both columns and the search pattern in `unaccent(lower(...))`. The function is provided by the `unaccent` PostgreSQL extension, enabled once via migration `0020_create_extension_unaccent`. Any environment that bypasses migrations (e.g. a manually restored DB dump) raises `function unaccent(text) does not exist` at query time, not at startup — extension state is checked lazily by the planner.
- **`account_ids` MUST be cast to `uuid[]` in the SQL.** psycopg2 sends a Python `list[str]` as `text[]`, and `account_id = ANY(%(account_ids)s)` without the explicit `::uuid[]` cast raises `operator does not exist: uuid = text`. Removing the cast is silently fine for empty lists (the repository short-circuits before the query) and breaks the moment any account is supplied.

## Attachment tables (migration 0023)

### `email_attachments` — two partial unique indexes encode the provider key asymmetry

Gmail rows key by `(account_id, provider_message_id, part_id) WHERE part_id IS NOT NULL`; Outlook rows key by `(account_id, provider_message_id, provider_attachment_id) WHERE provider_attachment_id IS NOT NULL AND part_id IS NULL`. A single null-tolerant compound index left Outlook rows colliding silently because `part_id` is always NULL there. `PgEmailAttachmentStore.upsert_batch` partitions input rows by which key they carry and runs **one `execute_values` per partition**, each targeting its own index by name in the `ON CONFLICT` clause. Mixed batches are supported but each row only resolves against its own index — do not collapse the two queries into one.

### `email_attachments` composite FK carries `ON UPDATE CASCADE`

The FK to `email_metadata(account_id, provider_message_id)` is `ON DELETE CASCADE` AND `ON UPDATE CASCADE`. The `ON UPDATE CASCADE` is load-bearing: Outlook rewrites `provider_message_id` on `move_to_trash` and spam moves, and the cascade propagates the rename into the attachment rows automatically. Without it, every Outlook move would orphan the attachments.

### Two-table split — metadata vs blob

`email_attachment_blobs` is a separate one-to-one table holding the binary in `BYTEA`. Two reasons: (1) `SELECT *` over the metadata never accidentally pulls megabytes; (2) the TTL purge can drop the blob while keeping the metadata row intact, so the listing endpoint's derived `is_downloaded` flag (`EXISTS` against the blob table) flips back to `false` and the next click re-fetches. Drafts use a single table (`draft_attachments`, blob column inline) because drafts are short-lived and bounded by 25 attachments per draft (D-03) — the two-table split would be over-engineering there.

### `LIST_EMAIL_ATTACHMENTS_BY_MESSAGE` — `is_downloaded` is derived

`is_downloaded` is computed via `EXISTS (SELECT 1 FROM email_attachment_blobs ...)`, never persisted. It survives TTL purges automatically. Do not add a column for it.

### `GET_EMAIL_ATTACHMENT_FOR_DOWNLOAD` — ownership chain in SQL

The download lookup JOINs `email_attachments → email_metadata → accounts → mailboxes` and filters on `mailboxes.owner_user_id`. The query is intentionally restrictive: a row that does not belong to the authenticated user collapses to "no row" and the service surfaces 404 (D-22). Do not relax this JOIN to "fast-path by attachment_id" — that opens UUID-guessing leakage.

### `PURGE_EXPIRED_BLOBS` — `last_accessed_at IS NOT NULL` predicate

The purge query filters on `a.last_accessed_at IS NOT NULL AND a.last_accessed_at < now() - INTERVAL '30 days'`. The `IS NOT NULL` half is non-obvious but load-bearing: a freshly-listed attachment that the user has never downloaded has `last_accessed_at = NULL`, and dropping the predicate would eagerly purge those blobs the moment the metadata row turned 30 days old — dropping a binary the user may still need. The flag is set by the `BackgroundTask` only on a successful stream.

### `email_attachment_store.update_has_attachments` — the only writer of the denormalised flag

`UPDATE_HAS_ATTACHMENTS` recomputes the column from `COUNT(*) WHERE is_inline=false` against `email_attachments` and is the only path that writes `has_attachments`. The flag must never be written freehand from a sync / trash / spam path — the B.lazy strategy (`docs/features/adjuntos.md` § 5) depends on it being driven solely by viewer activity. The query is idempotent.

Unlike every other method on this repository (which silently returns `None` / `[]` on `psycopg2.errors.InvalidTextRepresentation`), `update_has_attachments` deliberately **raises** `QueryError` on a malformed UUID. Silence here would leave `has_attachments` permanently stale and break the B.lazy invariant invisibly; the caller MUST supply a valid `(account_id, provider_message_id)`.

### `INSERT_DRAFT_ATTACHMENT` resolves `position` atomically inside the INSERT

The statement embeds `COALESCE((SELECT MAX(position) + 1 FROM draft_attachments WHERE account_id=… AND provider_draft_id=…), 0)` as the value for the `position` column, so the read and the write run inside a single round trip. The earlier two-statement pattern (`NEXT_DRAFT_ATTACHMENT_POSITION` followed by a separate `INSERT`) was a TOCTOU race despite a code comment claiming atomicity — two concurrent uploads from different tabs read the same `MAX(position)` and inserted with the same value. The repository contract reflects this: callers MUST NOT pre-resolve `position`; any value supplied in the row dict is ignored.

### `UPDATE_DRAFT_ATTACHMENT_PROVIDER_ID` adds `RETURNING` for D-27

Without it, an UPDATE that touches zero rows (CASCADE delete races ahead of the partial-success persist) returns success silently. The repository checks `cur.fetchone()` and raises `QueryError` when the row is gone, so the service can surface the race instead of regressing the partial-success persistence contract to a no-op on retry.

### `BATCH_UPDATE_DRAFT_ATTACHMENT_PROVIDER_IDS` — batch sibling, used on every Outlook send

The single-row variant exists for callers that only have one pair (mostly tests). Every production caller (`send_draft` success path AND `_persist_partial_upload_results` failure path) goes through `batch_update_provider_attachment_ids` which uses positional `%s` + `execute_values` over a `VALUES (id, provider_id)` clause. The `RETURNING` slot lets the repository count actually-updated rows, so a CASCADE-deleted row is dropped silently — the caller is on the best-effort post-send hygiene path and reporting individual misses would require row-by-row state the batch helper deliberately avoids.

### `UPSERT_EMAIL_ATTACHMENTS_*` — Gmail and Outlook are NOT symmetric

Gmail's variant lists `provider_attachment_id = EXCLUDED.provider_attachment_id` in the `DO UPDATE SET` clause because the provider rotates that id every fetch — we must refresh the cached value. Outlook's variant deliberately omits it: `provider_attachment_id` is the conflict key on its partial unique index (`idx_email_attachments_outlook`), so updating it would change which row matches and break the upsert. Do not "DRY" the two queries into one; the asymmetry is correctness, not noise.

### Blob row-shaping asymmetry between attachment repositories

`PgDraftAttachmentStore._row_to_dict` calls `bytes(memoryview)` on the `blob` column so the service sees plain `bytes`. `PgEmailAttachmentStore._row_to_dict` does not — its callers (the StreamingResponse path) consume `memoryview` directly to avoid an extra copy on the hot path. If you ever extract a shared `_row_to_dict`, preserve this divergence behind a flag rather than collapsing it.

### `PgAccountStore.get_by_id_for_user` — JOIN-based D-22 anti-leak

The standard `get(mailbox_id, account_id)` is keyed by both, so a cross-account service flow that only carries the account id (Forward copy from a different mailbox the same user owns) cannot use it without first resolving the mailbox — and that resolution would expose a 403/404 split a foreign account is supposed to avoid. `get_by_id_for_user` runs the JOIN over `mailboxes.owner_user_id` in a single round trip and returns `None` for both missing AND foreign accounts. The service layer converts the absence into 404 `AccountNotFound` uniformly. Do not relax the JOIN to a fast-path lookup keyed only by `account_id` — that opens UUID-guessing leakage.

A malformed UUID surfaces as `psycopg2.errors.InvalidTextRepresentation` and is swallowed into `return None` (consistent with the other store methods that treat malformed input as "not found" rather than 500). Do NOT route this through `QueryError` — the service relies on the silent collapse to drive the 404 path.

### `LIST_FILTERED` slot triple — `{box_predicate}` / `{search_predicate}` / `{extra_predicate}`

Three `str.format` slots back one SQL constant — used by both the regular `GET /emails` listing (single `box`, mandatory) and the virtual-mailbox listing (zero, one or many boxes derived from the stored `filter_payload`). The repository's `list_filtered` accepts `box` (str | None), `box_in` (list[str] | None) and `box_not_in` (list[str] | None) — **passing more than one of those is a programming error**. The service layer guarantees exclusivity; the repository does not re-validate, so a regression there would emit duplicated `AND box ...` predicates that compose with AND and silently return zero rows. `{extra_predicate}` is the slot the virtual-mailbox extra filters land in (built from a closed whitelist inside the repository — never accept free-form text from the caller into any of the three slots).

### `LIST_FILTERED` total-ordering tie-break

`ORDER BY received_at DESC, account_id, provider_message_id` is total by construction (the trailing pair is the table's primary key). Removing the tie-break leaves OFFSET paging non-deterministic when two rows share `received_at` (mass-sent notifications batched to the same second), so the same row can appear on adjacent pages or be skipped. Single-page tests never catch this; the regression only manifests when the user scrolls.

### `DraftAttachmentStore.list_existing_source_attachment_ids` — R-12 idempotency, backed by partial index

The repository method drives the R-12 idempotency check inside `copy_attachments_from_email`: candidates whose `source_attachment_id` already lives on the draft are reported as `skipped[reason="already_copied"]` instead of being inserted twice. The partial index `idx_draft_attachments_source (account_id, provider_draft_id, source_attachment_id) WHERE source_attachment_id IS NOT NULL` (migration 0030) is what keeps the query cheap — without it, every retry of the copy endpoint would table-scan `draft_attachments`. Direct uploads leave both source columns NULL so they do not bloat the index.

### Reply / forward columns on `drafts` (migration 0029)

`_DRAFT_REPLY_FIELDS` is the single source of truth for the set of nullable reply columns: `_row_to_dict` defaults each to `None` on legacy SELECTs that did not project them, and `_draft_insert_params` defaults each to `None` on INSERTs whose payload omitted them. Both helpers MUST iterate the same tuple — drifting them (e.g. adding a column only to `_row_to_dict`) makes `INSERT_DRAFT` fail with `MissingArg` on a payload from a back-compat caller, while reads still appear to work. The COALESCE rationale on `UPSERT_DRAFTS_BATCH` (sync vs local-write conflict resolution) lives in the SQL constant's own header in `queries/drafts.py`; do not duplicate it here.

`idx_drafts_reply_to_message_id` is intentionally added without any current reader — reserved for a future "list drafts replying to message X" lookup so a follow-up does not require a new migration.

### `DraftAttachmentStore.delete` returns `bool`, `DraftStore.delete` raises

The two siblings disagree intentionally. The composer's remove flow tolerates a missing row (the user double-clicked the X, or the row already CASCADE-deleted) and treats the absence as success → 204 from the router. Drafts at the parent level need the harder contract because deleting a non-existent draft is a 404 the user must see. Don't normalise these two interfaces.

There is also a **further asymmetry within `PgDraftAttachmentStore`** itself: `delete` swallows `InvalidTextRepresentation` and returns `False` (a malformed UUID is treated identically to a missing row — the remove flow is idempotent either way), but `update_provider_attachment_id` raises `QueryError` loudly on the same exception. Silence on the update path would break the D-27 partial-success persistence contract by causing a retry to re-upload an already-uploaded attachment. Do not normalise these two handlers.

## Extension

### Whenever a new Alembic migration is created

**`migrations/runner.py` must be updated in the same change**: append the equivalent DDL to `_DDL_STATEMENTS` and advance the stamp at the bottom to the new migration name. Forgetting this silently breaks any environment that relies on the fallback runner (local setup without Alembic, some CI configurations). Data-only migrations also belong here — e.g. migration 0014 adds `TRUNCATE TABLE email_content;` immediately before the stamp line, and every subsequent `email_content`-invalidating migration follows the same shape (see `repository_guide.md` § "Email HTML rendering cache"). Pure DDL extensions (e.g. migration 0020 adds `CREATE EXTENSION IF NOT EXISTS unaccent;`, migration 0025 adds the composite index `ix_drafts_account_created` backing the drafts listing queries, migration 0026 adds the partial index `idx_email_attachments_last_accessed` backing the admin TTL purge) do **not** require a `TRUNCATE` — only schema changes that invalidate cached HTML do. Migration 0026 is the current head. Migration 0022 (drafts column rename) is exposed in the runner via an idempotent `DO $$ ... ALTER TABLE drafts RENAME COLUMN body_html TO body ... $$` block so a fresh bootstrap (which already creates the column under the new name) is a no-op while a partially-bootstrapped DB is brought up to date.

### Adding a new email provider

1. Register the provider env var in `settings.py::_PROVIDER_CREDENTIALS_ENV_VARS`.
2. Extend `security/app_credentials.py` if the provider needs custom JSON parsing.
3. Update the provider CHECK constraint via a new Alembic migration (+ fallback runner per rule above).
4. Add/adjust integration and E2E coverage for connect, send, and inbox flows.
