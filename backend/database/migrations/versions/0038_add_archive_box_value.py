"""
Add ARCHIVE to the box and previous_box CHECK constraints in email_metadata.

``ARCHIVE`` is a new mutually-exclusive location for the ``box`` column
(like ``SENT`` / ``SPAM`` / ``TRASH``), backing the new "Archived" view.
Both constraints must be extended:

- ``box``: an archived message lives in ``box = 'ARCHIVE'``.
- ``previous_box``: the Archived view offers "Move to trash", and
  ``MOVE_TO_TRASH_BATCH`` copies the current ``box`` into ``previous_box``;
  archiving then trashing would write ``previous_box = 'ARCHIVE'``, which
  the pre-0038 constraint rejects. Extending it also lets
  ``RESTORE_FROM_TRASH_BATCH`` (``COALESCE(previous_box, 'ALL_MAIL')``)
  restore such a message back to ``ARCHIVE``.

No data backfill / cache truncate is needed: the reclassification of
already-archived provider mail into ``ARCHIVE`` happens gradually on the
next per-account sync (Gmail recomputes box from labels in
``_resolve_labels``; Outlook classifies by ``parentFolderId``).
"""

from __future__ import annotations

from alembic import op


revision = "0038_add_archive_box_value"
down_revision = "0037_generalize_users_auth_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE email_metadata "
        "DROP CONSTRAINT IF EXISTS email_metadata_box_check"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "ADD CONSTRAINT email_metadata_box_check "
        "CHECK (box IN ('ALL_MAIL', 'SENT', 'SPAM', 'TRASH', 'DELETED', 'ARCHIVE'))"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "DROP CONSTRAINT IF EXISTS email_metadata_previous_box_check"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "ADD CONSTRAINT email_metadata_previous_box_check "
        "CHECK (previous_box IS NULL OR previous_box IN ('ALL_MAIL', 'SENT', 'SPAM', 'ARCHIVE'))"
    )


def downgrade() -> None:
    # Relocate rows carrying the value that is about to disappear BEFORE
    # reverting the constraints, otherwise the old CHECK rejects them. This
    # reclassification is lossy: an archived row becomes indistinguishable from
    # an ALL_MAIL one (the ARCHIVE marker is gone). Alembic's implicit
    # transaction makes the UPDATEs + constraint swap atomic, so a mid-way
    # failure rolls back cleanly.
    op.execute(
        "UPDATE email_metadata SET box = 'ALL_MAIL' WHERE box = 'ARCHIVE'"
    )
    op.execute(
        "UPDATE email_metadata SET previous_box = 'ALL_MAIL' WHERE previous_box = 'ARCHIVE'"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "DROP CONSTRAINT IF EXISTS email_metadata_box_check"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "ADD CONSTRAINT email_metadata_box_check "
        "CHECK (box IN ('ALL_MAIL', 'SENT', 'SPAM', 'TRASH', 'DELETED'))"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "DROP CONSTRAINT IF EXISTS email_metadata_previous_box_check"
    )
    op.execute(
        "ALTER TABLE email_metadata "
        "ADD CONSTRAINT email_metadata_previous_box_check "
        "CHECK (previous_box IS NULL OR previous_box IN ('ALL_MAIL', 'SENT', 'SPAM'))"
    )
