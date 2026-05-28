# Virtual (fake) mailboxes

User-defined filtered views over the messages already stored in
`email_metadata`. A virtual mailbox is a **flat list of `account_ids`**
plus a `filter_payload`. End-to-end contract: `repository_guide.md`.

This file captures the **design decisions** that are not derivable
from the code itself.

## Why the account list is static (snapshot) and not auto-expanding

When the user creates a virtual mailbox they hand-pick the accounts it
aggregates. If they later connect a new account, that account does NOT
join their existing virtual mailboxes automatically. The earlier model
had a `scope_kind='all'` mode that auto-included every account the
user owned, and a `scope_kind='mailbox'` mode that auto-included every
account of a real mailbox; both were dropped by migration 0032 along
with `scope_kind` itself.

The trade-off: with the auto-expanding model, a virtual mailbox could
silently start surfacing messages from a sensitive account the user
just connected (e.g. a personal Gmail accidentally included in a
"Trabajo" view). The explicit list makes the boundary visible — the
chips in the form name exactly which accounts are included.

Migration 0032 snapshotted every pre-existing `scope_kind='all'` /
`'mailbox'` virtual mailbox into the equivalent explicit list at the
time of the migration. The semantic shift was accepted: those legacy
bandejas keep the accounts they had at migration time; later additions
will not appear automatically.

## Why ownership lives on the user, not on a real mailbox

A virtual mailbox aggregates accounts that can sit under different
real mailboxes. Hanging the FK on `mailboxes` would either force the
user to pick one as a "home" (artificial) or require a many-to-many
table. The FK targets `users(user_id) ON DELETE CASCADE` because
deleting the user must wipe their virtual mailboxes; deleting one of
the real mailboxes referenced indirectly by `account_ids` does NOT
cascade — the listing layer revalidates ownership on every read and
silently drops accounts the user no longer owns.

## Why the listing reuses `LIST_FILTERED` instead of a dedicated query

The regular `GET /emails` listing and the virtual-mailbox listing
share more than 90 % of the SQL: same projection, same indexes, same
join shape. Keeping two near-identical queries would have produced
silent drift the moment a new column was added to `email_metadata`.
The single query has three `str.format` slots (`{box_predicate}`,
`{search_predicate}`, `{extra_predicate}`) — each one is built from a
trusted whitelist of SQL fragments. **Nothing outside the repository
may inject text into these slots**; any future filter criterion must
extend the whitelist (`_EXTRA_FILTER_BUILDERS`) inside the repository
and the matching key in `ALLOWED_FILTER_KEYS` inside the schema.

One behaviour the virtual listing adds on top of the shared query: the
same physical email can surface **twice** when the user has connected the
same provider account under two different real mailboxes (two
`account_id`s pointing at the same inbox). The service collapses those
duplicates after the query so the user sees one row per message — the
implementation detail (which of the duplicate rows wins) lives in
`api_guide.md` under the virtual-mailboxes trap.

## Why `filter_payload` lives in JSONB instead of typed columns

Six filter criteria today (`box`, `box_not_in`, `from_email`,
`subject_contains`, `is_read`, `is_favorite`), more in the future
("fecha, etiquetas, presencia de adjuntos…"). Modelling each future criterion as a schema
column would force a migration per criterion. JSONB keeps the schema
stable; the Pydantic + repository whitelists provide the type safety
the column-per-criterion approach would have given. The trade-off: a
typo in `filter_payload` is silently ignored at the SQL layer —
Pydantic catches it at the API boundary via `extra="forbid"`.

The `scope_payload` column also remains JSONB even though its shape is
now fixed to `{account_ids: [...]}`. It was kept as JSONB (instead of
renamed to a `text[]` column) to avoid rippling a column rename
through every query and repository row at the cost of one extra
indirection in the SQL projection. The contract surfaces the list as
a flat `account_ids` field to every consumer.

## `box` and `box_not_in` are mutually exclusive, with a TRASH/SPAM default

A filter may carry **either** `box` (show only that box) **or**
`box_not_in` (show everything except those boxes), never both — a Pydantic
validator rejects a payload carrying both with 422. When **neither** is
present, the listing defaults to excluding `TRASH` and `SPAM` (the same
active-flow default the Favourites view applies). To opt back into seeing
trash/spam, the filter sends an explicit empty `box_not_in: []` — "exclude
nothing". The tempting mistake — sending both `box` and `box_not_in` to
"narrow twice" — is a 422 at the boundary, not a silently-empty result.

## Why account ownership is re-validated at every listing call

The user may revoke a mailbox or disconnect an account between create
and the next open of the virtual mailbox. Re-validating at read time
means the virtual mailbox keeps working (showing the surviving
subset) instead of returning a confusing 404. Foreign virtual
mailboxes (someone else's id) collapse to 404 — the existence-leak
anti-pattern applies here too.

## Why we did NOT add bulk endpoints for virtual mailboxes

A virtual mailbox is a definition, not a collection — actions on the
emails it surfaces (trash, mark read, favourite) go through the
existing per-message endpoints. No additional bulk surface is needed
because the virtual mailbox is read-only with respect to the
underlying emails.
