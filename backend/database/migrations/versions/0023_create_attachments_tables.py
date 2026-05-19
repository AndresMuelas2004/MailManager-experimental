"""
Create attachments tables and the has_attachments flag (D-09, D-10, D-11).

Three new tables:

- ``email_attachments``: per-attachment metadata for received emails.
  Composite FK to ``email_metadata(account_id, provider_message_id)``
  with ``ON DELETE CASCADE`` and ``ON UPDATE CASCADE`` so Outlook's id
  rewrites on move propagate.
  Two partial unique indexes encode the asymmetry between providers:
  Gmail keys by ``part_id`` (provider_attachment_id is NULL), Outlook
  keys by ``provider_attachment_id`` (part_id is NULL). The previous
  null-tolerant compound index left several rows colliding silently in
  Outlook.

- ``email_attachment_blobs``: separate table holding the binary in
  ``BYTEA`` so ``SELECT *`` on metadata never accidentally pulls
  megabytes into memory. Carries ``blob_storage_kind`` (``'db'`` |
  ``'fs'`` | ``'s3'``) and ``blob_ref`` for forward-compat with
  filesystem or object-store backends.

- ``draft_attachments``: per-attachment data for draft emails (composer
  state). Holds the binary inline (D-07 lazy push: provider doesn't
  see the attachment until send/save). ``provider_attachment_id`` is
  populated when Outlook's ``POST /attachments`` or ``createUploadSession``
  succeeds during ``send_draft`` so a partial failure can be resumed
  without re-uploading already-uploaded parts (D-27).

Adds ``has_attachments BOOLEAN NOT NULL DEFAULT FALSE`` to ``email_metadata``
so the inbox listing can render the clip icon without a JOIN. The flag is
maintained exclusively through the centralised helper
``recompute_has_attachments`` (services_helpers).
"""
from __future__ import annotations

from alembic import op


revision = "0023_create_attachments_tables"
down_revision = "0022_rename_drafts_body_html_to_body"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS email_attachments (
            attachment_id            UUID PRIMARY KEY,
            account_id               UUID NOT NULL,
            provider_message_id      VARCHAR(255) NOT NULL,
            part_id                  TEXT NULL,
            provider_attachment_id   TEXT NULL,
            filename                 TEXT NOT NULL,
            mime_type                TEXT NOT NULL,
            size                     BIGINT NOT NULL,
            content_id               TEXT NULL,
            is_inline                BOOLEAN NOT NULL DEFAULT FALSE,
            position                 INT NOT NULL DEFAULT 0,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_accessed_at         TIMESTAMPTZ NULL,
            unavailable_at           TIMESTAMPTZ NULL,
            CONSTRAINT email_attachments_metadata_fkey
                FOREIGN KEY (account_id, provider_message_id)
                REFERENCES email_metadata (account_id, provider_message_id)
                ON DELETE CASCADE
                ON UPDATE CASCADE
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_attachments_message
            ON email_attachments (account_id, provider_message_id);
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_email_attachments_gmail
            ON email_attachments (account_id, provider_message_id, part_id)
            WHERE part_id IS NOT NULL;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_email_attachments_outlook
            ON email_attachments (account_id, provider_message_id, provider_attachment_id)
            WHERE provider_attachment_id IS NOT NULL AND part_id IS NULL;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_attachment_blobs (
            attachment_id        UUID PRIMARY KEY,
            blob                 BYTEA NULL,
            blob_storage_kind    TEXT NOT NULL DEFAULT 'db',
            blob_ref             TEXT NULL,
            fetched_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT email_attachment_blobs_attachment_fkey
                FOREIGN KEY (attachment_id)
                REFERENCES email_attachments (attachment_id)
                ON DELETE CASCADE
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS draft_attachments (
            draft_attachment_id     UUID PRIMARY KEY,
            account_id              UUID NOT NULL,
            provider_draft_id       VARCHAR(255) NOT NULL,
            filename                TEXT NOT NULL,
            mime_type               TEXT NOT NULL,
            size                    BIGINT NOT NULL,
            content_id              TEXT NULL,
            is_inline               BOOLEAN NOT NULL DEFAULT FALSE,
            position                INT NOT NULL DEFAULT 0,
            blob                    BYTEA NULL,
            blob_storage_kind       TEXT NOT NULL DEFAULT 'db',
            blob_ref                TEXT NULL,
            provider_attachment_id  TEXT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT draft_attachments_draft_fkey
                FOREIGN KEY (account_id, provider_draft_id)
                REFERENCES drafts (account_id, provider_draft_id)
                ON DELETE CASCADE
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_draft_attachments_draft
            ON draft_attachments (account_id, provider_draft_id);
    """)

    op.execute("""
        ALTER TABLE email_metadata
        ADD COLUMN IF NOT EXISTS has_attachments BOOLEAN NOT NULL DEFAULT FALSE;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE email_metadata DROP COLUMN IF EXISTS has_attachments;")
    op.execute("DROP INDEX IF EXISTS idx_draft_attachments_draft;")
    op.execute("DROP TABLE IF EXISTS draft_attachments;")
    op.execute("DROP TABLE IF EXISTS email_attachment_blobs;")
    op.execute("DROP INDEX IF EXISTS idx_email_attachments_outlook;")
    op.execute("DROP INDEX IF EXISTS idx_email_attachments_gmail;")
    op.execute("DROP INDEX IF EXISTS idx_email_attachments_message;")
    op.execute("DROP TABLE IF EXISTS email_attachments;")
