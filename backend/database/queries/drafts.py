"""
SQL string constants for draft persistence.
"""

GET_DRAFT = """
    SELECT provider_draft_id, account_id, to_recipients, cc_recipients, bcc_recipients,
           subject, body, created_at, updated_at
    FROM drafts
    WHERE provider_draft_id = %(provider_draft_id)s
      AND account_id        = %(account_id)s
"""

UPDATE_DRAFT = """
    UPDATE drafts
    SET to_recipients = %(to_recipients)s,
        cc_recipients = %(cc_recipients)s,
        bcc_recipients = %(bcc_recipients)s,
        subject       = %(subject)s,
        body          = %(body)s,
        updated_at    = now()
    WHERE provider_draft_id = %(provider_draft_id)s
      AND account_id        = %(account_id)s
    RETURNING
        provider_draft_id,
        account_id,
        to_recipients,
        cc_recipients,
        bcc_recipients,
        subject,
        body,
        created_at,
        updated_at
"""

INSERT_DRAFT = """
    INSERT INTO drafts (
        provider_draft_id,
        account_id,
        to_recipients,
        cc_recipients,
        bcc_recipients,
        subject,
        body
    )
    VALUES (
        %(provider_draft_id)s,
        %(account_id)s,
        %(to_recipients)s,
        %(cc_recipients)s,
        %(bcc_recipients)s,
        %(subject)s,
        %(body)s
    )
    RETURNING
        provider_draft_id,
        account_id,
        to_recipients,
        cc_recipients,
        bcc_recipients,
        subject,
        body,
        created_at,
        updated_at
"""

# Listing queries co-aggregate the draft attachments in a single round trip
# (subquery + JSON aggregation) so the listing endpoint does not fan out
# into one ``list_by_draft`` per row. Per D-03 each draft holds at most 25
# attachments so the JSON payload stays bounded. The ``blob`` column is
# deliberately excluded — never pull binaries on a list. The shared SELECT
# clause + attachments aggregate live in a private fragment to avoid
# duplicating the JSON shape between the by-account and by-mailbox
# variants; only the FROM/JOIN and WHERE differ between them.
_LIST_DRAFTS_SELECT = """
    SELECT d.provider_draft_id, d.account_id, d.to_recipients, d.cc_recipients,
           d.bcc_recipients, d.subject, d.body, d.created_at, d.updated_at,
           COALESCE(
               (SELECT json_agg(
                   json_build_object(
                       'draft_attachment_id', da.draft_attachment_id,
                       'filename',            da.filename,
                       'mime_type',           da.mime_type,
                       'size',                da.size,
                       'position',            da.position,
                       'provider_attachment_id', da.provider_attachment_id
                   ) ORDER BY da.position
               )
               FROM draft_attachments da
               WHERE da.account_id        = d.account_id
                 AND da.provider_draft_id = d.provider_draft_id),
               '[]'::json
           ) AS attachments
    FROM drafts d
"""

LIST_DRAFTS_BY_ACCOUNT = _LIST_DRAFTS_SELECT + """
    WHERE d.account_id = %(account_id)s
    ORDER BY d.created_at DESC
"""

LIST_DRAFTS_BY_MAILBOX = _LIST_DRAFTS_SELECT + """
    JOIN accounts a ON d.account_id = a.account_id
    WHERE a.mailbox_id = %(mailbox_id)s
    ORDER BY d.created_at DESC
"""

# Used with execute_values(cur, UPSERT_DRAFTS_BATCH, rows); positional %s required.
UPSERT_DRAFTS_BATCH = """
    INSERT INTO drafts (
        provider_draft_id, account_id, to_recipients, cc_recipients,
        bcc_recipients, subject, body, created_at, updated_at
    )
    VALUES %s
    ON CONFLICT (provider_draft_id, account_id) DO UPDATE SET
        to_recipients = EXCLUDED.to_recipients,
        cc_recipients = EXCLUDED.cc_recipients,
        bcc_recipients = EXCLUDED.bcc_recipients,
        subject       = EXCLUDED.subject,
        body          = EXCLUDED.body,
        updated_at    = now()
        -- created_at intentionally excluded; preserve original on upsert
"""

DELETE_DRAFTS_MISSING_FOR_ACCOUNT = """
    -- keep_ids may be empty, which intentionally deletes all drafts for the account
    DELETE FROM drafts
    WHERE account_id = %(account_id)s
      AND NOT (provider_draft_id = ANY(%(keep_ids)s))
"""

# Returns the deleted ``provider_draft_id`` so the repository can
# distinguish hit (RETURNING fires once) from miss (``cur.fetchone()`` is
# ``None``) without a second SELECT. On miss the caller treats it as a
# 404, not a server error — DELETE on a non-existent row is not failure.
DELETE_DRAFT = """
    DELETE FROM drafts
    WHERE provider_draft_id = %(provider_draft_id)s
      AND account_id = %(account_id)s
    RETURNING provider_draft_id
"""
