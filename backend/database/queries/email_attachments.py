"""
SQL string constants for email_attachments / email_attachment_blobs.

Two tables back the attachments cache:

- ``email_attachments`` holds the metadata (filename, mime, size,
  position, ``part_id`` for Gmail or ``provider_attachment_id`` for
  Outlook, plus the ``unavailable_at`` flag for D-17). Composite FK to
  ``email_metadata(account_id, provider_message_id)`` with
  ``ON DELETE CASCADE`` and ``ON UPDATE CASCADE`` (the latter so
  Outlook's id mutations on move propagate transparently).

- ``email_attachment_blobs`` holds the binary in ``BYTEA`` (D-06b). It
  is intentionally a separate table so ``SELECT *`` over the metadata
  never accidentally pulls megabytes; the only query that reads
  ``blob`` is :py:data:`GET_EMAIL_ATTACHMENT_BLOB`.

The dual partial unique indexes encoded in migration 0023 enforce the
provider-specific cache key (D-06b-clave): Gmail keys by
``(account_id, provider_message_id, part_id)`` and Outlook by
``(account_id, provider_message_id, provider_attachment_id)``.
"""

# ---------------------------------------------------------------------------
# Listing & lookup
# ---------------------------------------------------------------------------

# ``is_downloaded`` is derived (D-10) so it stays consistent when blobs
# are purged by TTL. The EXISTS join is cheap because the FK is the
# blob's primary key.
LIST_EMAIL_ATTACHMENTS_BY_MESSAGE = """
    SELECT a.attachment_id,
           a.account_id,
           a.provider_message_id,
           a.part_id,
           a.provider_attachment_id,
           a.filename,
           a.mime_type,
           a.size,
           a.content_id,
           a.is_inline,
           a.position,
           a.created_at,
           a.last_accessed_at,
           a.unavailable_at,
           EXISTS (
               SELECT 1 FROM email_attachment_blobs b
               WHERE b.attachment_id = a.attachment_id
           ) AS is_downloaded
    FROM email_attachments a
    WHERE a.account_id          = %(account_id)s
      AND a.provider_message_id = %(provider_message_id)s
    ORDER BY a.position
"""

# Lookup for the download endpoint. The JOIN proves ownership chain
# ``attachment -> email_metadata -> account -> mailbox -> user``; if
# any link breaks, the row is filtered out and the service raises
# ``AttachmentNotFound`` (no leaking of foreign attachments via UUID
# guessing — D-22).
GET_EMAIL_ATTACHMENT_FOR_DOWNLOAD = """
    SELECT a.attachment_id,
           a.account_id,
           a.provider_message_id,
           a.part_id,
           a.provider_attachment_id,
           a.filename,
           a.mime_type,
           a.size,
           a.content_id,
           a.is_inline,
           a.position,
           a.unavailable_at
    FROM email_attachments a
    JOIN email_metadata em
      ON em.account_id          = a.account_id
     AND em.provider_message_id = a.provider_message_id
    JOIN accounts ac   ON ac.account_id = a.account_id
    JOIN mailboxes mb  ON mb.mailbox_id  = ac.mailbox_id
    WHERE a.attachment_id = %(attachment_id)s
      AND ac.mailbox_id   = %(mailbox_id)s
      AND ac.account_id   = %(account_id)s
      AND mb.owner_user_id = %(user_id)s
"""

GET_EMAIL_ATTACHMENT_BLOB = """
    SELECT blob
    FROM email_attachment_blobs
    WHERE attachment_id = %(attachment_id)s
"""

# ---------------------------------------------------------------------------
# Upserts & state transitions
# ---------------------------------------------------------------------------

# Used with ``execute_values(cur, UPSERT_EMAIL_ATTACHMENTS_BATCH, rows)``
# — positional placeholders (``%s``) per the psycopg2 contract. Two
# partial unique indexes back the conflict resolution: Gmail upserts
# resolve on ``(account_id, provider_message_id, part_id)`` and Outlook
# on ``(account_id, provider_message_id, provider_attachment_id)``. The
# ``ON CONFLICT`` clauses target each index by name so a single batch
# can carry rows from either provider safely.
UPSERT_EMAIL_ATTACHMENTS_GMAIL = """
    INSERT INTO email_attachments (
        attachment_id, account_id, provider_message_id, part_id,
        provider_attachment_id, filename, mime_type, size,
        content_id, is_inline, position
    )
    VALUES %s
    ON CONFLICT (account_id, provider_message_id, part_id)
        WHERE part_id IS NOT NULL
    DO UPDATE SET
        filename               = EXCLUDED.filename,
        mime_type              = EXCLUDED.mime_type,
        size                   = EXCLUDED.size,
        content_id             = EXCLUDED.content_id,
        is_inline              = EXCLUDED.is_inline,
        position               = EXCLUDED.position,
        provider_attachment_id = EXCLUDED.provider_attachment_id
    RETURNING attachment_id
"""

UPSERT_EMAIL_ATTACHMENTS_OUTLOOK = """
    INSERT INTO email_attachments (
        attachment_id, account_id, provider_message_id, part_id,
        provider_attachment_id, filename, mime_type, size,
        content_id, is_inline, position
    )
    VALUES %s
    ON CONFLICT (account_id, provider_message_id, provider_attachment_id)
        WHERE provider_attachment_id IS NOT NULL AND part_id IS NULL
    -- provider_attachment_id is the conflict key here; updating it would be
    -- circular. (The Gmail variant updates it because its conflict key is
    -- part_id — see UPSERT_EMAIL_ATTACHMENTS_GMAIL above for the asymmetry.)
    DO UPDATE SET
        filename   = EXCLUDED.filename,
        mime_type  = EXCLUDED.mime_type,
        size       = EXCLUDED.size,
        content_id = EXCLUDED.content_id,
        is_inline  = EXCLUDED.is_inline,
        position   = EXCLUDED.position
    RETURNING attachment_id
"""

INSERT_EMAIL_ATTACHMENT_BLOB = """
    INSERT INTO email_attachment_blobs (attachment_id, blob, blob_storage_kind, blob_ref, fetched_at)
    VALUES (%(attachment_id)s, %(blob)s, 'db', NULL, now())
    ON CONFLICT (attachment_id) DO UPDATE SET
        blob       = EXCLUDED.blob,
        fetched_at = now()
"""

MARK_EMAIL_ATTACHMENT_UNAVAILABLE = """
    UPDATE email_attachments
    SET unavailable_at = now()
    WHERE attachment_id = %(attachment_id)s
"""

TOUCH_EMAIL_ATTACHMENT_LAST_ACCESSED = """
    UPDATE email_attachments
    SET last_accessed_at = now()
    WHERE attachment_id = %(attachment_id)s
"""

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

# Returns ``(attachment_id, freed_bytes)`` per purged blob so the admin
# endpoint can report aggregate stats. ``OCTET_LENGTH`` is exact for
# ``BYTEA`` (no compression overhead is reported).
PURGE_EXPIRED_BLOBS = """
    DELETE FROM email_attachment_blobs b
    USING email_attachments a
    WHERE a.attachment_id = b.attachment_id
      AND a.last_accessed_at IS NOT NULL
      AND a.last_accessed_at < (now() - INTERVAL '30 days')
    RETURNING b.attachment_id, OCTET_LENGTH(b.blob) AS bytes
"""
