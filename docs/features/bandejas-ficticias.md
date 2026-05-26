# Virtual (fake) mailboxes

User-defined filtered views over the messages already stored in
`email_metadata`. Spec: `Ignore/bandejas-ficticias.md`. End-to-end
contract: `repository_guide.md`.

This file captures the **design decisions** that are not derivable
from the code itself.

## Why ownership lives on the user, not on a real mailbox

A virtual mailbox can deliberately cross real mailboxes
(`scope_kind = 'all'`, `scope_kind = 'accounts'`). Hanging it under a
single `mailbox_id` would either force the user to pick one as a
"home" (artificial) or require a many-to-many table for the
`accounts` scope (gold-plating for a feature that already has the
user_id boundary). The FK targets `users(user_id) ON DELETE CASCADE`
because deleting the user must wipe their virtual mailboxes; deleting
one of the real mailboxes referenced in `scope_payload` does NOT
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

## Why the JSONB scope/filter columns over typed columns

Five filter criteria today, more in the spec ("fecha, etiquetas,
presencia de adjuntos, etc."). Modelling each future criterion as a
schema column would force a migration per criterion. JSONB keeps the
schema stable; the Pydantic + repository whitelists provide the type
safety the column-per-criterion approach would have given. The
trade-off: a typo in `filter_payload` is silently ignored at the SQL
layer — Pydantic catches it at the API boundary.

## Why scope validation runs at create AND at every listing call

The user may revoke a mailbox or disconnect an account between create
and the next open of the virtual mailbox. Re-validating at read time
means the virtual mailbox keeps working (showing the surviving
subset) instead of returning a confusing 404. Foreign virtual
mailboxes (someone else's id) collapse to 404 — the existence-leak
anti-pattern (D-22) applies here too.

## Why we did NOT add bulk endpoints for virtual mailboxes

A virtual mailbox is a definition, not a collection — actions on the
emails it surfaces (trash, mark read, favourite) go through the
existing per-message endpoints. No additional bulk surface is needed
because the virtual mailbox is read-only with respect to the
underlying emails.
