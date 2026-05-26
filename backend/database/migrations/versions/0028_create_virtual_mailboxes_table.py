"""
Create the ``virtual_mailboxes`` table.

Virtual mailboxes (fake mailboxes) are user-defined filtered views over
the messages that already live in ``email_metadata``. They do NOT hold
emails of their own — at read time the service translates the stored
``scope`` + ``filter`` into a parameterised SELECT against
``email_metadata``.

Ownership lives on the **user**, not on a real mailbox, because the
``scope`` of a virtual mailbox can cross mailboxes (``scope_kind = 'all'``
or ``scope_kind = 'accounts'``). Deleting the user CASCADEs the rows.

``scope_payload`` and ``filter_payload`` are JSONB blobs interpreted by
the service layer. CHECK constraints enforce only the closed enum of
``scope_kind``; field-level validation of the payload happens in
Pydantic. Storing them as JSONB keeps the schema stable when the
filter language grows (new criteria like date ranges, attachments,
labels…) without a fresh migration each time.
"""
from __future__ import annotations

from alembic import op


revision = "0028_create_virtual_mailboxes_table"
down_revision = "0027_add_is_favorite_to_email_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS virtual_mailboxes (
            virtual_mailbox_id  UUID         PRIMARY KEY,
            owner_user_id       UUID         NOT NULL
                                 REFERENCES users(user_id) ON DELETE CASCADE,
            display_name        VARCHAR(120) NOT NULL,
            scope_kind          VARCHAR(20)  NOT NULL
                                 CHECK (scope_kind IN ('mailbox', 'all', 'accounts')),
            scope_payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
            filter_payload      JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_virtual_mailboxes_owner "
        "ON virtual_mailboxes(owner_user_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_virtual_mailboxes_owner;")
    op.execute("DROP TABLE IF EXISTS virtual_mailboxes;")
