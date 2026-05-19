"""
Rename drafts.body_html to drafts.body (D-31).

The column historically named ``body_html`` always stored plain text from
the composer's ``<textarea>``. With the introduction of attachments (D-31)
the API surface and provider clients are unified around ``body`` and the
body is sent as ``text/plain`` to both Gmail and Outlook. Renaming the
column at the storage layer keeps SQL, repositories, schemas and provider
clients aligned with the same name. The textual content of existing drafts
is preserved unchanged — only the column name changes.
"""
from __future__ import annotations

from alembic import op


revision = "0022_rename_drafts_body_html_to_body"
down_revision = "0021_email_content_fkey_on_update_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE drafts RENAME COLUMN body_html TO body;")


def downgrade() -> None:
    op.execute("ALTER TABLE drafts RENAME COLUMN body TO body_html;")
