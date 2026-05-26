# Favourites

Cross-provider abstraction over Gmail's `STARRED` label and Outlook's
message `flag` property. The feature surface lives in three places:

- Database: `email_metadata.is_favorite` (migration 0027) plus a partial
  index `(account_id, received_at DESC) WHERE is_favorite = TRUE`.
- Backend API:
  - `PATCH /mailboxes/{mid}/accounts/{aid}/emails/{message_id}/favorite`
  - `POST  /mailboxes/{mid}/favorites/sync`
  - `GET   /mailboxes/{mid}/emails?favorite=true`
- Frontend: `FavoriteButton` on every row of `EmailTable`, `FavoritesPage`
  with a "Sincronizar favoritos" button, `useFavorite` hook.

The end-to-end contract is summarised in `repository_guide.md`. This
file captures the **non-obvious design decisions** that informed the
implementation; nothing here is "what the code does" — open the source
for that.

## Why `is_favorite` is a column, not a value of `box`

`box` records WHERE the email lives (`INBOX`, `SENT`, `SPAM`, `TRASH`).
`is_favorite` records a marking the user applied to the email.
Collapsing the two would lose the real location every time the user
favourites a message — Gmail and Outlook both model the favourite as
ORTHOGONAL state, so we mirror them.

## Why the sync does NOT import new metadata rows

Two options were considered (`Ignore/Favoritos-Funcionalidad.md` §
"Cuestiones técnicas"):

- **A**: sync only updates rows already present locally; provider-only
  favourites are ignored.
- **B**: sync imports missing metadata too, inserting new rows with
  `is_favorite = TRUE`.

We picked **A**. Reasons:

1. The provider call that backs `/favorites/sync` is `messages.list`
   (Gmail) / `messages` with `$filter` (Outlook). Both return only the
   id; importing would force a second per-id metadata fetch and turn a
   cheap label-reconciliation into a metadata-sync ghost.
2. The general `/emails/sync-metadata` endpoint already owns the
   responsibility of importing new rows; doing it from two endpoints
   risks divergence.
3. The user-visible effect of "you favourited an email at the provider
   that we haven't synced yet" is "the favourite shows up after the
   next refresh", which is acceptable for a manual reconciliation
   endpoint.

## Why TRASH and SPAM are excluded by default

When the user trashes a favourite, they implicitly told the system
"this is not in my active flow anymore". The Favourites view respects
that decision: `GET /emails?favorite=true` forces `box NOT IN
('TRASH','SPAM')` unless `box` explicitly targets one of those. The
same default applies to virtual mailboxes whose `filter_payload`
contains no `box` clause.

## Bulk operations — out of scope for the MVP

The frontend that needs to mark multiple emails as favourite iterates
and calls `PATCH /favorite` N times. The bulk endpoints exposed by both
providers (`users.messages.batchModify` / Graph `$batch`) would be a
trivial addition later and do not block any other feature. The
no-bulk choice keeps the API surface small and the error model simple
(no partial-success contract to design).

## Provider-First Rule

Both `/favorite` and `/favorites/sync` follow Provider-First — the
provider is the source of truth, and a provider failure aborts the
local persistence so we never drift away from what the user sees in
Gmail/Outlook web. The single non-obvious wrinkle is the **pre-check
ordering** documented in `repository_guide.md`: a missing local
metadata row must collapse to 404 BEFORE the provider call, mirroring
the `DraftNotFound` pre-check pattern.
