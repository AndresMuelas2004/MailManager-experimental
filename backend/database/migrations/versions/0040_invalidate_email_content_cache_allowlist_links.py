"""
One-shot invalidation of the email_content cache after the sixth-round
rendering-pipeline extensions:

- **Allowlist widened**: HTML5 semantic/structural tags (``section``,
  ``article``, ``header``, ``footer``, ``figure``, ``caption``/``col``/
  ``colgroup``, …), legacy layout attributes (``tr bgcolor/height``,
  ``img hspace/vspace``, ``td/th nowrap``, ``table height``), the ``tel:``
  protocol, and modern CSS (flexbox family, logical margins/paddings,
  ``text-decoration-*``, ``object-fit``, ``direction``) now survive
  sanitisation instead of being stripped with their styling.
- **Link hardening**: every ``<a>`` in the sanitised body now carries
  ``target="_blank" rel="noopener noreferrer"`` and drops ``cid:``/``data:``
  hrefs (image-only protocols) — reverse-tabnabbing hardening for the
  popup-enabled sandboxed iframe.
- **CID matching normalised**: inline ``cid:`` references now resolve
  case-insensitively and percent-decoded, so images whose ``Content-ID``
  case differs from the HTML reference stop rendering as broken icons.

HTML cached before these changes was sanitised under the old rules (styling
stripped, links unhardened, mismatched CIDs unresolved). Truncating
``email_content`` forces a cache-aside refetch + re-sanitise on next view.

Does not alter the schema. ``ON DELETE CASCADE`` from ``email_metadata`` is
unaffected — only the cached HTML rows are removed; metadata is intact and
repopulation is transparent.
"""
from __future__ import annotations

from alembic import op


revision = "0040_invalidate_email_content_cache_allowlist_links"
down_revision = "0039_add_signature_html_to_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation. The cleared rows cannot be re-inflated
    # without re-fetching from the provider, which the cache-aside pattern
    # already handles on demand.
    pass
