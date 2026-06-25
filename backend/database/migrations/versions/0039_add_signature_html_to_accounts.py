"""
Add signature_html column to accounts table.
"""

from __future__ import annotations

from alembic import op


revision = "0039_add_signature_html_to_accounts"
down_revision = "0038_add_archive_box_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE accounts "
        "ADD COLUMN IF NOT EXISTS signature_html TEXT DEFAULT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE accounts "
        "DROP COLUMN IF EXISTS signature_html"
    )
