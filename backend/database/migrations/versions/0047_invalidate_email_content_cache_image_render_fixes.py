"""
Invalidate the ``email_content`` cache after the image-rendering fixes.

Four changes alter the bytes the inbound sanitiser produces, and the first one
also corrupted data that is baked into the cache and cannot self-heal:

1. Attribute values serialised with single quotes were double-escaped by bleach
   (``&amp;`` → ``&amp;amp;``), which happens to EVERY inline ``style`` that
   carries both a double quote (``font-family:"X"``, ``url("…")``) and a remote
   image URL. The image rewrite then signed that literal ``&amp;`` into the
   proxy sentinel, so the proxy asks the CDN for a query string with an
   ``amp;`` parameter — a permanently broken image, since the signature and the
   URL are stored together in the cached body.
2. ``!important`` is no longer stripped from the preserved ``<style>`` block, so
   responsive ``@media`` rules win over the inlined desktop styles again.
3. Protocol-relative image URLs (``//cdn/…``) are now proxied instead of being
   loaded straight from the sender (IP leak).
4. ``<body background="…">`` is preserved as a wrapper ``background-image``.

Bodies cached before this change keep the broken sentinels and the stripped
``!important``, so they are cleared once and repopulate on demand via
cache-aside (the same one-shot invalidation pattern as migrations 0014-0019,
0024, 0035, 0040, 0043, 0044, 0046). In a fresh bootstrap the table is already
empty, so it is a no-op.
"""
from __future__ import annotations

from alembic import op


revision = "0047_invalidate_email_content_cache_image_render_fixes"
down_revision = "0046_invalidate_email_content_cache_style_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation; the cleared rows repopulate on demand via
    # cache-aside, so there is nothing to revert.
    pass
