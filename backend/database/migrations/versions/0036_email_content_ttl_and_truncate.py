"""
Add a sliding-TTL ``last_accessed_at`` column to ``email_content`` and
truncate the cache once.

The full email body is immutable, so the TTL is pure space eviction, not
freshness eviction: a row whose body has not been opened in 30 days is
purged during a sync (``PURGE_EXPIRED_FOR_ACCOUNTS``), mirroring the
attachment-blob TTL (``email_attachments.last_accessed_at`` /
``PURGE_EXPIRED_BLOBS``). Reading the body refreshes ``last_accessed_at``
so frequently-read mail stays cached.

``NOT NULL DEFAULT now()`` (unlike the nullable attachment column): every
``email_content`` row IS a cache entry created at persist time, so it has
always been "accessed" — this keeps the purge predicate and the partial
index free of an ``IS NOT NULL`` guard.

The ``TRUNCATE`` is a one-shot cache invalidation (precedent: migrations
0014–0019, 0024, 0035): the unification of body+attachments into a single
provider read changed the persist path, and the content re-caches on
demand on the next view. It is not reverted on ``downgrade``.
"""
from __future__ import annotations

from alembic import op


revision = "0036_email_content_ttl_and_truncate"
down_revision = "0035_invalidate_email_content_cache_background_attr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE email_content "
        "ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now();"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_content_last_accessed "
        "ON email_content (account_id, last_accessed_at);"
    )
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_email_content_last_accessed;")
    op.execute("ALTER TABLE email_content DROP COLUMN IF EXISTS last_accessed_at;")
    # The TRUNCATE is a one-shot cache invalidation; the cleared rows
    # re-inflate transparently via the cache-aside path on next view.
