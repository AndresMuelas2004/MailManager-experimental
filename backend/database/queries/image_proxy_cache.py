"""
SQL string constants for the ``image_proxy_cache`` table.

The image proxy caches each remote email image once, keyed by the SHA-256
hex of its original URL. The key is global (not per-account): the same CDN
image referenced from many emails / accounts de-duplicates to one row, which
also minimises how often the sender's server is contacted.

The binary lives inline in ``image_bytes`` (BYTEA) rather than in a separate
blob table — proxied images are bounded (``_MAX_BYTES`` in the fetcher) and
served whole, so the two-table split used by ``email_attachments`` would be
over-engineering here. ``last_accessed_at`` drives a sliding-TTL admin purge
(``PURGE_EXPIRED``), the same shape as ``PURGE_EXPIRED_BLOBS``.
"""
from __future__ import annotations

GET_BY_URL_HASH = """
    SELECT content_type, image_bytes
    FROM image_proxy_cache
    WHERE url_hash = %(url_hash)s
"""

UPSERT_IMAGE = """
    INSERT INTO image_proxy_cache
        (url_hash, url, content_type, image_bytes)
    VALUES (%(url_hash)s, %(url)s, %(content_type)s, %(image_bytes)s)
    ON CONFLICT (url_hash) DO UPDATE SET
        content_type     = EXCLUDED.content_type,
        image_bytes      = EXCLUDED.image_bytes,
        fetched_at       = now(),
        last_accessed_at = now()
"""

# Sliding-TTL refresh on a cache HIT: touches ONLY ``last_accessed_at`` so a
# frequently-served image never expires. ``fetched_at`` stays put (the bytes
# are immutable; a serve is not a re-fetch), mirroring the email_content touch.
TOUCH_LAST_ACCESSED = """
    UPDATE image_proxy_cache
    SET last_accessed_at = now()
    WHERE url_hash = %(url_hash)s
"""

# Returns ``(url_hash, freed_bytes)`` per purged row so the admin endpoint can
# report aggregate stats, mirroring ``PURGE_EXPIRED_BLOBS``. ``last_accessed_at``
# is ``NOT NULL DEFAULT now()`` (every row IS a cache entry), so — unlike the
# attachment purge — no ``IS NOT NULL`` guard is needed here.
PURGE_EXPIRED = """
    DELETE FROM image_proxy_cache
    WHERE last_accessed_at < (now() - INTERVAL '30 days')
    RETURNING url_hash, OCTET_LENGTH(image_bytes) AS bytes
"""
