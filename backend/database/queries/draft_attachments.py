"""
SQL string constants for draft_attachments.

Drafts attachments live entirely locally until the user clicks "Send"
or "Save" (D-07 lazy push). The binary is stored inline as ``BYTEA`` in
the same row as the metadata — drafts are short-lived and the table is
bounded by 25 attachments per draft (D-03), so the simplification of a
single table here is preferable to the two-table split used by the
received-attachments cache.

``provider_attachment_id`` carries the Outlook ``attachment.id`` after
a successful upload during ``send_draft`` — used by D-27 to skip
already-uploaded attachments on a retry. For Gmail it stays ``NULL``
because the send is atomic (no intermediate provider state to track).
"""

# ---------------------------------------------------------------------------
# Listing & lookup
# ---------------------------------------------------------------------------

# Excludes ``blob`` deliberately so list calls (composer hydrate, drafts
# listing JOIN) never accidentally pull megabytes per row.
LIST_DRAFT_ATTACHMENTS_BY_DRAFT = """
    SELECT draft_attachment_id, account_id, provider_draft_id,
           filename, mime_type, size, content_id, is_inline, position,
           provider_attachment_id, created_at
    FROM draft_attachments
    WHERE account_id        = %(account_id)s
      AND provider_draft_id = %(provider_draft_id)s
    ORDER BY position
"""

# Same shape as :py:data:`LIST_DRAFT_ATTACHMENTS_BY_DRAFT` plus ``blob``,
# used exclusively by the send path to avoid the 1 + N round trips that a
# list-then-per-row-get pattern requires (D-03 caps drafts at 25 rows so
# the worst-case payload is bounded). Never use for the composer hydrate.
LIST_DRAFT_ATTACHMENTS_BY_DRAFT_WITH_BLOB = """
    SELECT draft_attachment_id, account_id, provider_draft_id,
           filename, mime_type, size, content_id, is_inline, position,
           provider_attachment_id, blob, created_at
    FROM draft_attachments
    WHERE account_id        = %(account_id)s
      AND provider_draft_id = %(provider_draft_id)s
    ORDER BY position
"""

# Single-row lookup by draft_attachment_id. Excludes ``blob`` — DELETE
# and ownership checks do not need it. The send path uses
# :py:data:`LIST_DRAFT_ATTACHMENTS_BY_DRAFT_WITH_BLOB` (single round
# trip for the whole batch).
GET_DRAFT_ATTACHMENT = """
    SELECT draft_attachment_id, account_id, provider_draft_id,
           filename, mime_type, size, content_id, is_inline, position,
           provider_attachment_id, created_at
    FROM draft_attachments
    WHERE draft_attachment_id = %(draft_attachment_id)s
"""

# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

# Position assignment is folded into the INSERT — both the lookup and
# the write run inside a single statement, so two concurrent tabs uploading
# to the same draft cannot read the same ``MAX(position)`` and collide.
# This replaces the earlier two-statement pattern (separate
# ``NEXT_DRAFT_ATTACHMENT_POSITION`` + ``INSERT``) which was a TOCTOU race
# despite a code comment claiming atomicity.
INSERT_DRAFT_ATTACHMENT = """
    INSERT INTO draft_attachments (
        draft_attachment_id, account_id, provider_draft_id,
        filename, mime_type, size, content_id, is_inline, position,
        blob, blob_storage_kind, blob_ref, provider_attachment_id
    )
    VALUES (
        %(draft_attachment_id)s, %(account_id)s, %(provider_draft_id)s,
        %(filename)s, %(mime_type)s, %(size)s, %(content_id)s,
        %(is_inline)s,
        COALESCE((
            SELECT MAX(position) + 1
            FROM draft_attachments
            WHERE account_id        = %(account_id)s
              AND provider_draft_id = %(provider_draft_id)s
        ), 0),
        %(blob)s, 'db', NULL, NULL
    )
    RETURNING draft_attachment_id, account_id, provider_draft_id,
              filename, mime_type, size, content_id, is_inline, position,
              provider_attachment_id, created_at
"""

# DELETE returns the deleted draft_attachment_id when the row existed
# (used by ``PgDraftAttachmentStore.delete`` to distinguish hit vs miss
# without a separate SELECT). On miss, ``cur.fetchone()`` is ``None`` so
# the repository returns ``False`` rather than raising — the caller treats
# a missing row as a 404, not a server error.
DELETE_DRAFT_ATTACHMENT = """
    DELETE FROM draft_attachments
    WHERE draft_attachment_id = %(draft_attachment_id)s
    RETURNING draft_attachment_id
"""

# Set after a successful provider upload (Outlook only) — D-27.
# RETURNING lets the repository distinguish a missing row (CASCADE delete
# raced ahead of us) from a successful update; without it, an UPDATE
# touching zero rows succeeds silently and the partial-success persistence
# contract degrades to a silent no-op on retry.
UPDATE_DRAFT_ATTACHMENT_PROVIDER_ID = """
    UPDATE draft_attachments
    SET provider_attachment_id = %(provider_attachment_id)s
    WHERE draft_attachment_id  = %(draft_attachment_id)s
    RETURNING draft_attachment_id
"""

# Batch variant: stamps ``provider_attachment_id`` for many rows in a
# single round trip. Used by ``send_draft`` (Outlook) on both the
# success path (post-send hygiene before CASCADE delete) and the D-27
# partial-success persistence path. Used with ``execute_values``;
# placeholders must be POSITIONAL ``%s``. The cast to ``::uuid`` lets
# the VALUES clause supply text-shaped UUID strings without psycopg2
# adaptation. RETURNING preserves the same hit-vs-miss distinction the
# single-row variant offers (caller can spot rows the CASCADE removed
# mid-flight) — D-27 retries rely on it.
BATCH_UPDATE_DRAFT_ATTACHMENT_PROVIDER_IDS = """
    UPDATE draft_attachments AS d
    SET provider_attachment_id = v.provider_attachment_id
    FROM (VALUES %s) AS v(draft_attachment_id, provider_attachment_id)
    WHERE d.draft_attachment_id = v.draft_attachment_id::uuid
    RETURNING d.draft_attachment_id
"""
