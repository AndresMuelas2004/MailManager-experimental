"""
One-shot invalidation of the email_content cache after allow-listing the
legacy ``background`` HTML attribute on ``<table>`` / ``<td>`` / ``<th>``.

Email templates (AliExpress EDM product grids and similar) paint the
thumbnail through the ``background`` attribute of a table cell
(``<td background="https://…">``) instead of an ``<img>``. Until this
change the sanitiser's attribute allowlist did not include ``background``,
so bleach dropped it and only the placeholder ``background-color`` survived
— rendering every such cell as a grey box.

HTML cached before this change still has the attribute stripped, so the
grey boxes would persist on already-viewed emails. Truncating
``email_content`` forces a cache-aside refetch + re-sanitise on next view,
so the thumbnails render with the new allowlist.

Does not alter the schema. ``ON DELETE CASCADE`` from ``email_metadata`` is
unaffected — only the cached HTML rows are removed; metadata is intact and
repopulation is transparent.
"""
from __future__ import annotations

from alembic import op


revision = "0035_invalidate_email_content_cache_background_attr"
down_revision = "0034_convert_drafts_body_to_html"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation. The cleared rows cannot be re-inflated
    # without re-fetching from the provider, which the cache-aside pattern
    # already handles on demand.
    pass
