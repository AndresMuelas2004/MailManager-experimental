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
- `subject` and `body` are `TEXT NOT NULL DEFAULT ''` — empty drafts are valid. The column was named `body_html`, renamed to `body` in migration 0022, and now holds **HTML** again (the rich-text composer): migration 0034 converts every pre-existing plain-text `body` to HTML, and from then on `body` is always sanitised HTML (no `body_format` discriminator). The 0022 rename ripple still applies (queries, repository, schemas, provider clients, draft API parameters); 0034 is data-only and touches no column definition.
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

Three `str.format` slots back one SQL constant — used by both the regular `GET /emails` listing (single `box`, mandatory) and the virtual-mailbox listing (zero, one or many boxes derived from the stored `filter_payload`). The repository's `list_filtered` accepts `box` (str | None), `box_in` (list[str] | None) and `box_not_in` (list[str] | None) — **passing more than one of those is a programming error**. The service layer guarantees exclusivity; the repository does not re-validate, so a regression there would emit duplicated `AND box ...` predicates that compose with AND and silently return zero rows. `{extra_predicate}` is the slot the virtual-mailbox extra filters land in (built from a closed whitelist inside the repository — never accept free-form text from the caller into any of the three slots). `box_not_in` distinguishes `None` (absent) from `[]`: an explicit empty list is the deliberate opt-in to *see* TRASH/SPAM, so the guard MUST be `is not None`, never a truthiness test — `if box_not_in:` silently collapses `[]` back to the default exclusion and reverses the caller's intent.

The lupa's Gmail-style operators (`operator_clauses` kwarg on `list_filtered` / `count_filtered`) are appended into this **same** `{extra_predicate}` slot, AFTER the `extra_filters` clauses — there is deliberately **no new SQL slot**. They resolve against `_OPERATOR_CLAUSE_BUILDERS`, a registry kept **separate** from `_EXTRA_FILTER_BUILDERS`: the `kind` keys carry an `_op` suffix (or a distinct name) and the emitted parameter names are `op{idx}` per occurrence, so the three namespaces — `op{idx}` (operators), `extra_*` (saved filters), `tok{i}` (free text) — are disjoint and never collide. That disjointness is what lets an operator and a saved filter touch the **same column** (saved `subject_contains` + lupa `subject:`, or saved `is_read` + lupa `is:read`) emit two independent ANDed clauses instead of overwriting each other; a contradiction (`is:read`+`is:unread`, inverted `received_at` range) therefore returns zero rows **in SQL**, not via a code-level guard. When `operator_clauses` is empty the emitted SQL is byte-for-byte identical to the pre-operator query — the zero-regression invariant the existing SQL-surface tests rely on. The operator registry is repository-only and has no schema counterpart: these operators are ad-hoc search, not saveable filters, so they are exempt from the "two whitelists in lockstep" rule (`repository_guide.md`).

### `LIST_FILTERED` total-ordering tie-break

`ORDER BY received_at DESC, account_id, provider_message_id` is total by construction (the trailing pair is the table's primary key). Removing the tie-break leaves OFFSET paging non-deterministic when two rows share `received_at` (mass-sent notifications batched to the same second), so the same row can appear on adjacent pages or be skipped. Single-page tests never catch this; the regression only manifests when the user scrolls.

### `_build_filter_predicates` is the single source feeding both the SELECT and the COUNT

`list_filtered` (the page) and `count_filtered` (its `total`) MUST stay in lockstep — a `total` that counts a different set than the page lists would render a wrong "X of Z" and break the last-page clamp. Both build their `{box_predicate}` / `{search_predicate}` / `{extra_predicate}` slots and named params through the **same** private helper `_build_filter_predicates`; a new filter criterion lands there once and both pick it up. The helper deliberately does **not** add `limit` / `offset` — only the listing queries append them. Both public methods keep the empty-`account_ids` short-circuit (`[]` / `0`) BEFORE touching the connection, so an unauthorized empty scope never leaks a count.

`COUNT_FILTERED` retains the `JOIN accounts USING (account_id)` even though no current predicate references `a.*` (the COUNT does not project `a.mailbox_id` the way `LIST_FILTERED` does). Dropping the JOIN is tempting but would let a future filter on `a.*` diverge the count from the listing silently — the JOIN over the indexed PK is free on the bounded synced window.

### `LIST_FILTERED_DISTINCT` / `COUNT_FILTERED_DISTINCT` — vmbox dedup moved into SQL

The virtual-mailbox listing dedups the same provider message surfaced under two `account_id`s (one provider account connected under two mailboxes) **in SQL** now — the Python `_dedupe_rows_by_provider_message_id` is gone. The dedup MUST happen before `LIMIT`/`OFFSET`: deduping a positionally-paginated result skips/duplicates rows at the page borders, and a plain `COUNT(*)` over-counts the duplicate. `LIST_FILTERED_DISTINCT` uses an inner `DISTINCT ON (provider_message_id)` whose inner `ORDER BY` replicates the removed Python winner-selection (populated `to_email` > populated `to_name` > most recent `received_at`), wrapped by an outer `ORDER BY` that restores presentation order AND adds the PK tie-break the Python sort lacked. `btrim(coalesce(col, ''))<>''` is **load-bearing parity** with the old `isinstance(str) and col.strip()` test: a plain `col <> ''` would diverge twice — `NULL <> ''` is `NULL` (sorts differently from `FALSE` under `DESC`) and `'  ' <> ''` is `TRUE` (Python scored whitespace-only as empty). The column is `VARCHAR NOT NULL DEFAULT ''` since migration 0031 so a migrated DB carries no NULLs, but the coalesce keeps parity for any pre-0031 row and is free. The regular `GET /emails` listing is always scoped to a single mailbox where the duplicate cannot occur, so it leaves `distinct_provider_message_id=False` (plain `LIST_FILTERED` / `COUNT_FILTERED`). `COUNT_FILTERED_DISTINCT` (`COUNT(DISTINCT provider_message_id)`) needs no tie-break `ORDER BY` — counting distinct keys does not pick which duplicate row wins.

### Conversation grouping (`*_GROUPED_BY_THREAD`) — the four invariants the SQL headers do NOT capture

The `group_by_thread` flag on `list_filtered` / `count_filtered` selects a thread-collapsing template via the 2×2 matrix in `_select_list_template` / `_select_count_template` (the second axis is the existing `distinct_provider_message_id`). The SQL constants carry their own headers; only the cross-constant invariants live here:

- **The COUNT template MUST track the LIST template's `distinct` axis, not just the `group_by_thread` axis.** Regular grouping (`LIST_GROUPED_BY_THREAD`) pairs with `COUNT_GROUPED_BY_THREAD`; virtual grouping (`LIST_GROUPED_BY_THREAD_DISTINCT`) pairs with `COUNT_GROUPED_BY_THREAD_DISTINCT`. The two `_select_*` helpers are siblings precisely so a future edit to one forces the matching edit to the other — picking the wrong COUNT silently descuadra the `total` against the page (e.g. `COUNT_GROUPED_BY_THREAD` on the virtual listing over-counts the same provider account connected under two mailboxes).
- **Grouping-key asymmetry: regular partitions by `(account_id, thread_key)`, virtual by `thread_key` alone.** Provider thread namespaces are per-account, so genuinely distinct accounts never share a `thread_id` and never merge under either key. The `account_id` in the regular key is the guard for the *pathological* same-string collision; dropping it from the virtual key is what *intentionally* collapses one provider account connected under two `account_id`s (the same dedup intent as `LIST_FILTERED_DISTINCT`). Mirror this split if you ever add a third grouped surface.
- **`COUNT(DISTINCT COALESCE(NULLIF(thread_id,''), provider_message_id))` — the COALESCE is mandatory, not cosmetic.** `COUNT(DISTINCT thread_id)` alone drops every threadless (`''`/NULL) message from the total because PostgreSQL excludes NULL from `COUNT(DISTINCT)`, so the page would list threads the total never counted. Same reason the LIST templates key threadless rows by their own `provider_message_id`.
- **The virtual COUNT needs NO inner dedup (no `d1` subnivel), but the virtual LIST does.** The same `provider_message_id` under two `account_id`s carries the same `thread_key`, so `COUNT(DISTINCT thread_key)` collapses it exactly as the LIST's `d1` dedup does — the LIST only needs `d1` to pick *which* duplicate row to show and to paginate stably. Coherence between the two is guaranteed because both go through the same `_build_filter_predicates` slots and the same shared `tokens`.

`thread_message_count` is an extra projected column on the grouped templates only; `row_to_email_metadata_out` reads it via `row.get("thread_message_count", 1)` so the non-grouped templates (which never project it) fall through to `1`.

### `GET_METADATA_BY_MESSAGE` / `EmailMetadataStore.get_metadata` — single-row read added for the conversation endpoint

`EmailMetadataStore` had only `exists` (a `SELECT 1` probe); the conversation endpoint needs the base message's actual `thread_id` plus its presentation columns. `get_metadata` projects the EXACT column list + `a.mailbox_id` JOIN of `LIST_FILTERED` **on purpose** — so the same `row_to_email_metadata_out` maps it with no special-casing, and the threadless (`thread_id=''`) singleton path can return the viewer message from this single read without a second SELECT. Do NOT slim it to a `thread_id`-only getter: that would force an extra read on the singleton path. Malformed UUIDs collapse to `None` (treated as "not found"), matching `exists`; the unique error message is `"Failed to get email metadata row by message id."`.

### `DraftAttachmentStore.list_existing_source_attachment_ids` — R-12 idempotency, backed by partial index

The repository method drives the R-12 idempotency check inside `copy_attachments_from_email`: candidates whose `source_attachment_id` already lives on the draft are reported as `skipped[reason="already_copied"]` instead of being inserted twice. The partial index `idx_draft_attachments_source (account_id, provider_draft_id, source_attachment_id) WHERE source_attachment_id IS NOT NULL` (migration 0030) is what keeps the query cheap — without it, every retry of the copy endpoint would table-scan `draft_attachments`. Direct uploads leave both source columns NULL so they do not bloat the index.

### Reply / forward columns on `drafts` (migration 0029)

`_DRAFT_REPLY_FIELDS` is the single source of truth for the set of nullable reply columns: `_row_to_dict` defaults each to `None` on legacy SELECTs that did not project them, and `_draft_insert_params` defaults each to `None` on INSERTs whose payload omitted them. Both helpers MUST iterate the same tuple — drifting them (e.g. adding a column only to `_row_to_dict`) makes `INSERT_DRAFT` fail with `MissingArg` on a payload from a back-compat caller, while reads still appear to work. The COALESCE rationale on `UPSERT_DRAFTS_BATCH` (sync vs local-write conflict resolution) lives in the SQL constant's own header in `queries/drafts.py`; do not duplicate it here.

`idx_drafts_reply_to_message_id` is intentionally added without any current reader — reserved for a future "list drafts replying to message X" lookup so a follow-up does not require a new migration.

### `DraftAttachmentStore.delete` returns `bool`, `DraftStore.delete` raises

The two siblings disagree intentionally. The composer's remove flow tolerates a missing row (the user double-clicked the X, or the row already CASCADE-deleted) and treats the absence as success → 204 from the router. Drafts at the parent level need the harder contract because deleting a non-existent draft is a 404 the user must see. Don't normalise these two interfaces.

There is also a **further asymmetry within `PgDraftAttachmentStore`** itself: `delete` swallows `InvalidTextRepresentation` and returns `False` (a malformed UUID is treated identically to a missing row — the remove flow is idempotent either way), but `update_provider_attachment_id` raises `QueryError` loudly on the same exception. Silence on the update path would break the D-27 partial-success persistence contract by causing a retry to re-upload an already-uploaded attachment. Do not normalise these two handlers.

### `to_email` / `to_name` refresh category (migration 0031) — three distinct UPSERT behaviours

`email_metadata` now has three distinct refresh behaviours on `UPSERT_EMAIL_METADATA_BATCH` conflict, and a new column MUST pick one deliberately:

1. **Refreshed on every conflict** — `to_email` / `to_name` are in the `DO UPDATE SET` list, so a re-sync overwrites them with the latest provider value. They are `VARCHAR NOT NULL DEFAULT ''`; empty string means "not yet synced", never NULL. Only the **first** `To` recipient is stored, not the full list (see `repository_guide.md`).
2. **Never refreshed** — `from_email` / `from_name` are set on insert and left untouched on conflict (a received email's sender never changes).
3. **Excluded from the UPSERT entirely** — `has_attachments` is driven only by `recompute_has_attachments` (B.lazy); a sync conflict must not touch it.

Putting a new column in the wrong category silently regresses one of these contracts.

### `LIST_RECIPIENT_SUGGESTIONS` — recipient-autocomplete aggregation, no index by design

Backs the user-level `GET /contacts/suggestions` endpoint. Two `UNION ALL` branches collect candidate `(email, name, received_at)` tuples — `from_*` (received senders) and `to_*` (sent recipients) — over `box NOT IN ('SPAM','TRASH','DELETED')`, then the outer SELECT dedupes by `lower(email)`, picks the most-recent non-empty name, and orders by frequency then recency. The token-predicate slots reuse `LIST_FILTERED`'s shape (`unaccent(lower(coalesce(col,'')))ILIKE …`, `account_id = ANY(%(account_ids)s::uuid[])`) so the same two traps apply: `unaccent` is a runtime extension dependency (migration 0020) and the `::uuid[]` cast is mandatory.

- **Own-address exclusion carries a `NOT IN (… NULL …)` trap.** The exclusion subquery `lower(c.email) NOT IN (SELECT lower(a.email_address) FROM accounts … )` MUST keep its `AND a.email_address IS NOT NULL` filter: `accounts.email_address` is nullable (migration 0009), and a single NULL inside a `NOT IN (...)` set makes the whole predicate evaluate to NULL → **zero rows returned**. Dropping the `IS NOT NULL` silently empties every suggestion list the moment any owned account has no stored email. The accepted corollary: an account whose `email_address` was never fetched cannot have its own address excluded (we cannot exclude what we do not know).
- **Heavier than the lupa — accepted for the MVP.** The lupa runs ONE filtered scan of the account's rows; this query runs TWO (one per UNION branch) plus the `GROUP BY` aggregation and the exclusion subquery, and the aggregation touches every matching row BEFORE the `LIMIT`. Bounded by `account_id` (`idx_email_metadata_account_id`) and by the frontend's 2-char + debounce gating, so it is fine for personal-mailbox volumes. **No dedicated index** (no migration; head stays 0032): the leading-wildcard `ILIKE '%…%'` cannot use a B-tree, the same reason `pg_trgm` GIN was rejected for the lupa. The natural escape hatch if a user ever syncs hundreds of thousands of messages is a `pg_trgm` GIN index or a materialised contacts table — neither is built today.

### `virtual_mailboxes` + `PgVirtualMailboxStore` (migration 0032)

The table stores the scope as a `scope_payload` JSONB column shaped `{"account_ids": [...]}`, but the repository projects it back as a flat `account_ids: list[str]` (an artefact of migration 0032, which dropped the old `scope_kind` indirection and snapshotted pre-existing rows into explicit lists). The FK is `users(user_id) ON DELETE CASCADE` — there is **no** FK to `accounts`, so deleting a real account does NOT cascade into the snapshot; the listing path re-validates ownership per read and silently drops accounts the user no longer owns. `update()` swallows `InvalidTextRepresentation` and returns `None` (same shape as `get()`), `delete()` returns `False` — unlike `DraftStore.update`, which raises. The race policy depends on this: when the ownership pre-check passes but the row vanishes before the mutating SQL, `update → None` / `delete → False` lets the service surface 404 instead of a 500 / silent 200.

## Pending optimizations (post-MVP)

> Deferred performance work that is **correct but not optimal** today. None is a bug; each is accepted for MVP volumes. **When a future task asks to optimise / scale / improve the application's data-access flows (i.e. move beyond MVP), recommend addressing the items below** — they are recorded here precisely so they are not forgotten once performance becomes a goal.

### `sync_favorites` issues one UPDATE per account (N round trips)

`POST /favorites/sync` → `emails_service.sync_favorites` loops over the per-account provider results and calls `email_metadata_store.sync_favorites_for_account` once per account, so a buzón with N accounts runs N separate `SYNC_FAVORITES_FOR_ACCOUNT` UPDATEs (one round trip each). Correct and bounded today (users connect ~1–3 accounts, and it is a manual sync endpoint), but it scales linearly with account count. **Two distinct facets, both deferred:** (1) *inter-account* — the N round trips just described; (2) *intra-account* — each `SYNC_FAVORITES_FOR_ACCOUNT` rewrites **every** row of the account (`SET is_favorite = (provider_message_id = ANY(...))`), not only the rows whose favourite state actually changed. Both are accepted: neither is a bug, and the `total_synced` contract (rows touched across the buzón) deliberately depends on the full rewrite — see `docs/features/favoritos.md` § 5.3.

The optimisation is a single bulk UPDATE driven by a `VALUES` set of `(account_id, provider_message_id)` favourite pairs (mark those TRUE and every other row of the touched accounts FALSE in one statement). **Non-obvious refactor trap to preserve:** the per-account loop currently also (a) accumulates `total_synced` from each account's `cur.rowcount`, and (b) builds one `FavoriteSyncAccountDetail` per account for the response, and (c) catches DB errors **per account**. A single statement collapses (c) (loses per-account error granularity) and changes (a) — `total_synced` would come from the one bulk `rowcount` (the grand total, which is still correct), while (b) does not depend on rowcount (`favorites_synced = len(favorite_ids)`). Any bulk rewrite must keep the response contract intact.

## Extension

### Whenever a new Alembic migration is created

**`migrations/runner.py` must be updated in the same change**: append the equivalent DDL to `_DDL_STATEMENTS` and advance the stamp at the bottom to the new migration name. Forgetting this silently breaks any environment that relies on the fallback runner (local setup without Alembic, some CI configurations). Data-only migrations also belong here — e.g. migration 0014 adds `TRUNCATE TABLE email_content;` immediately before the stamp line, and every subsequent `email_content`-invalidating migration follows the same shape (see `repository_guide.md` § "Email HTML rendering cache"). Migration **0034** (rich-text composer) is the current head and is also data-only, but it is a textual **conversion** rather than a `TRUNCATE`: a single idempotent `UPDATE drafts ... SET body = '<p>' || … || '</p>'` (HTML-escape `&`/`<`/`>` then `nl2br`) that only touches non-blank rows not already looking like HTML, mirrored byte-for-byte between the Alembic file and the runner so the two stay in lockstep. Pure DDL extensions (e.g. migration 0020 adds `CREATE EXTENSION IF NOT EXISTS unaccent;`, migration 0025 adds the composite index `ix_drafts_account_created` backing the drafts listing queries, migration 0026 adds the partial index `idx_email_attachments_last_accessed` backing the admin TTL purge, migration 0027 adds the `is_favorite` column plus the partial index `idx_email_metadata_favorites (account_id, received_at DESC) WHERE is_favorite = TRUE` backing the Favourites listing, migration 0033 adds the composite index `idx_email_metadata_account_thread (account_id, thread_id, received_at DESC)` backing the conversation grouping templates) do **not** require a `TRUNCATE` — only schema changes that invalidate cached HTML do. The conversation grouping queries are correct WITHOUT 0033 (it only avoids seq-scan + sort; the real grouping key `COALESCE(NULLIF(thread_id,''), provider_message_id)` is not directly covered, so threadless rows fall back to the PK). Migration 0022 (drafts column rename) is exposed in the runner via an idempotent `DO $$ ... ALTER TABLE drafts RENAME COLUMN body_html TO body ... $$` block so a fresh bootstrap (which already creates the column under the new name) is a no-op while a partially-bootstrapped DB is brought up to date.

### Adding a new email provider

1. Register the provider env var in `settings.py::_PROVIDER_CREDENTIALS_ENV_VARS`.
2. Extend `security/app_credentials.py` if the provider needs custom JSON parsing.
3. Update the provider CHECK constraint via a new Alembic migration (+ fallback runner per rule above).
4. Add/adjust integration and E2E coverage for connect, send, and inbox flows.
