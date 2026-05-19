"""
One-shot invalidation of the email_content cache after the strict
inline-vs-attachment split (D-13).

Before this change, the rendering pipeline embedded every part marked
``Content-Disposition: inline`` (or Outlook ``isInline=true``) as a
``data:`` URL inside the cached HTML, regardless of whether the body
referenced its CID. The new strict rule (D-13) only inlines parts whose
``Content-Id`` is referenced by the body via ``cid:``; the rest are
promoted to downloadable attachments and listed in ``email_attachments``.

HTML cached before this change still carries the legacy ``data:`` URLs
for inline-marked-but-unreferenced parts AND would now also be listed in
the new attachments table — a duplicate state that's confusing for the
UI. Truncating ``email_content`` forces cache-aside refetch on next
view, producing a coherent split between inline (still in HTML) and
descargable (only in ``email_attachments``).

Does not alter the schema. ``ON DELETE CASCADE`` from ``email_metadata``
is unaffected — only the cached HTML rows are removed; metadata is
intact and repopulation is transparent.
"""
from __future__ import annotations

from alembic import op


revision = "0024_invalidate_email_content_cache_attachments_split"
down_revision = "0023_create_attachments_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation. The cleared rows cannot be re-inflated
    # without re-fetching from the provider, which the cache-aside pattern
    # already handles on demand.
    pass
