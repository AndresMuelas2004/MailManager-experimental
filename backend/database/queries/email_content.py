"""
SQL queries for email content persistence (business-logic only).
"""
from __future__ import annotations

GET_BY_MESSAGE_ID = """
    SELECT html_body, text_body, fetched_at
    FROM email_content
    WHERE provider_message_id = %(provider_message_id)s
      AND account_id = %(account_id)s
"""

UPSERT_EMAIL_CONTENT = """
    INSERT INTO email_content
        (provider_message_id, account_id, html_body, text_body, last_accessed_at)
    VALUES (%(provider_message_id)s, %(account_id)s, %(html_body)s, %(text_body)s, now())
    ON CONFLICT (provider_message_id, account_id) DO UPDATE SET
        html_body = EXCLUDED.html_body,
        text_body = EXCLUDED.text_body,
        fetched_at = now(),
        last_accessed_at = now()
"""

# Sliding-TTL refresh: a cache HIT on the body counts as an access. Touches
# ONLY ``last_accessed_at`` — never ``fetched_at`` (the content is immutable;
# a hit is not a re-fetch). Touching ``fetched_at`` here would break the E2E
# cache-hit assertions that ``fetched_at`` stays constant across reads.
TOUCH_LAST_ACCESSED = """
    UPDATE email_content SET last_accessed_at = now()
    WHERE account_id = %(account_id)s
      AND provider_message_id = %(provider_message_id)s
"""

# Per-account eviction of bodies idle for 7+ days. This TTL is deliberately
# SHORTER than the attachment-blob purge (``PURGE_EXPIRED_BLOBS``, 30 days) —
# the two no longer share a plazo. Scoped to the accounts synced in this
# request (auto-cleanup, no scheduler). Row count via ``cur.rowcount`` — no
# ``RETURNING`` needed.
PURGE_EXPIRED_FOR_ACCOUNTS = """
    DELETE FROM email_content
    WHERE account_id = ANY(%(account_ids)s::uuid[])
      AND last_accessed_at < (now() - INTERVAL '7 days')
"""
