"""
SQL string constants for draft persistence.
"""

# Reply / forward columns (migration 0029) — opt-in fields read by the
# send path so the outgoing message keeps the thread. ``COALESCE`` is
# critical in :py:data:`UPSERT_DRAFTS_BATCH` (see below): ``sync_drafts``
# pulls drafts back from the provider with these fields ``NULL`` and
# would otherwise overwrite the locally-set reply metadata. The query
# constants in this module are the single SQL surface for those columns
# — every read/write goes through them.
GET_DRAFT = """
    SELECT provider_draft_id, account_id, to_recipients, cc_recipients, bcc_recipients,
           subject, body, created_at, updated_at,
           reply_kind, reply_to_message_id, reply_to_account_id,
           thread_id, in_reply_to, references_header
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
        updated_at,
        reply_kind,
        reply_to_message_id,
        reply_to_account_id,
        thread_id,
        in_reply_to,
        references_header
"""

INSERT_DRAFT = """
    INSERT INTO drafts (
        provider_draft_id,
        account_id,
        to_recipients,
        cc_recipients,
        bcc_recipients,
        subject,
        body,
        reply_kind,
        reply_to_message_id,
        reply_to_account_id,
        thread_id,
        in_reply_to,
        references_header
    )
    VALUES (
        %(provider_draft_id)s,
        %(account_id)s,
        %(to_recipients)s,
        %(cc_recipients)s,
        %(bcc_recipients)s,
        %(subject)s,
        %(body)s,
        %(reply_kind)s,
        %(reply_to_message_id)s,
        %(reply_to_account_id)s,
        %(thread_id)s,
        %(in_reply_to)s,
        %(references_header)s
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
        updated_at,
        reply_kind,
        reply_to_message_id,
        reply_to_account_id,
        thread_id,
        in_reply_to,
        references_header
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
           d.reply_kind, d.reply_to_message_id, d.reply_to_account_id,
           d.thread_id, d.in_reply_to, d.references_header,
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
#
# COALESCE for the reply metadata columns is load-bearing: ``sync_drafts``
# pulls drafts back from the provider without those fields (Gmail /
# Outlook do not expose reply_kind / thread_id / In-Reply-To as draft
# properties). Without COALESCE, the sync would clobber locally-persisted
# reply metadata on every refresh. COALESCE preserves the existing
# non-NULL local value whenever the EXCLUDED value is NULL — which is
# exactly the right direction for sync-vs-local-write conflicts.
UPSERT_DRAFTS_BATCH = """
    INSERT INTO drafts (
        provider_draft_id, account_id, to_recipients, cc_recipients,
        bcc_recipients, subject, body, created_at, updated_at,
        reply_kind, reply_to_message_id, reply_to_account_id,
        thread_id, in_reply_to, references_header
    )
    VALUES %s
    ON CONFLICT (provider_draft_id, account_id) DO UPDATE SET
        to_recipients = EXCLUDED.to_recipients,
        cc_recipients = EXCLUDED.cc_recipients,
        bcc_recipients = EXCLUDED.bcc_recipients,
        subject       = EXCLUDED.subject,
        body          = EXCLUDED.body,
        updated_at    = now(),
        -- preserve local reply metadata when the EXCLUDED row (sync from
        -- provider) carries NULLs — see module docstring.
        reply_kind          = COALESCE(EXCLUDED.reply_kind, drafts.reply_kind),
        reply_to_message_id = COALESCE(EXCLUDED.reply_to_message_id, drafts.reply_to_message_id),
        reply_to_account_id = COALESCE(EXCLUDED.reply_to_account_id, drafts.reply_to_account_id),
        thread_id           = COALESCE(EXCLUDED.thread_id, drafts.thread_id),
        in_reply_to         = COALESCE(EXCLUDED.in_reply_to, drafts.in_reply_to),
        references_header   = COALESCE(EXCLUDED.references_header, drafts.references_header)
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
