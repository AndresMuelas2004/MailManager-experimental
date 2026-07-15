"""
Create the ``image_proxy_cache`` table AND invalidate the ``email_content`` cache.

Two changes ship together in this migration:

- **New table ``image_proxy_cache``** — backs the remote-email-image proxy. One
  row per distinct remote image URL, keyed by the SHA-256 hex of the original
  URL (global — shared across accounts / users so a CDN image is fetched from
  the sender only once). The binary lives inline in ``image_bytes`` (BYTEA);
  ``last_accessed_at`` drives a sliding-TTL admin purge (index below).

- **``TRUNCATE email_content``** — the inbound sanitiser now rewrites remote
  image URLs to signed proxy sentinel URLs, so every cached HTML body changes
  shape. Clearing the cache forces a cache-aside refetch + re-sanitise on next
  view (the same one-shot invalidation pattern as migrations 0014-0019, 0024,
  0035, 0040). In a fresh bootstrap the table is already empty, so it is a no-op.

The ``email_content`` body TTL also drops from 30 to 7 days in this change set,
but that is a pure SQL-constant edit (``PURGE_EXPIRED_FOR_ACCOUNTS``), not a
schema change, so it needs no migration.
"""
from __future__ import annotations

from alembic import op


revision = "0043_image_proxy_cache_and_invalidate_content"
down_revision = "0042_create_draft_sync_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS image_proxy_cache (
            url_hash          TEXT         PRIMARY KEY,
            url               TEXT         NOT NULL,
            content_type      TEXT         NOT NULL,
            image_bytes       BYTEA        NOT NULL,
            fetched_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_accessed_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_proxy_cache_last_accessed "
        "ON image_proxy_cache (last_accessed_at);"
    )
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_image_proxy_cache_last_accessed;")
    op.execute("DROP TABLE IF EXISTS image_proxy_cache;")
    # The TRUNCATE is a one-shot cache invalidation; the cleared rows repopulate
    # on demand via cache-aside, so there is nothing to revert for it.
