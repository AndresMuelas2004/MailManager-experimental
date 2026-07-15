"""
Invalidate the ``email_content`` cache after disabling premailer's network access.

The inbound sanitiser now runs premailer with ``allow_network=False``: it no
longer downloads ``<link rel="stylesheet">`` targets, so sender-controlled
downloads can never be planted into the sanitised body again (they previously
leaked as visible markup when the downloaded resource was an HTML page, and
constituted an unguarded server-side fetch of sender URLs). Bodies cached
before this change may still carry that injected content, so they are cleared
once and repopulate on demand via cache-aside (the same one-shot invalidation
pattern as migrations 0014-0019, 0024, 0035, 0040, 0043). In a fresh bootstrap
the table is already empty, so it is a no-op.
"""
from __future__ import annotations

from alembic import op


revision = "0044_invalidate_email_content_cache_premailer_no_network"
down_revision = "0043_image_proxy_cache_and_invalidate_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation; the cleared rows repopulate on demand via
    # cache-aside, so there is nothing to revert.
    pass
