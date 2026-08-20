"""
Invalidate the ``email_content`` cache after dropping the ``@font-face`` at-rule.

A web font's ``src: url(…)`` was the only remote reference the image rewrite
never proxied (the proxy serves ``image/*`` only), so every cached body that
still carries an ``@font-face`` makes the viewer fetch that font DIRECTLY from
whatever host the sender named — a read-tracking channel that bypasses the
proxy entirely. A sweep of the live corpus measured 108 such references across
31% of the cached emails, so this is not a rare residual.

Bodies cached before this change keep those raw references, and the sliding TTL
counts INACTIVITY (7 days), so a frequently opened email would never shed them
on its own. Clearing the table once forces a re-sanitise on next view via
cache-aside (the same one-shot invalidation pattern as migrations 0014-0019,
0024, 0035, 0040, 0043, 0044, 0046, 0047). In a fresh bootstrap the table is
already empty, so it is a no-op.
"""
from __future__ import annotations

from alembic import op


revision = "0048_invalidate_email_content_cache_drop_font_face"
down_revision = "0047_invalidate_email_content_cache_image_render_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation; the cleared rows repopulate on demand via
    # cache-aside, so there is nothing to revert.
    pass
