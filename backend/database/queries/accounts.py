"""
Account SQL statements.
"""

from __future__ import annotations

LIST_ACCOUNTS_BY_MAILBOX = """
    SELECT account_id, mailbox_id, provider, display_label, config, email_address, signature_html, created_at
    FROM accounts
    WHERE mailbox_id = %(mailbox_id)s
    ORDER BY created_at
"""

GET_ACCOUNT = """
    SELECT account_id, mailbox_id, provider, display_label, config, email_address, signature_html, created_at
    FROM accounts
    WHERE mailbox_id = %(mailbox_id)s AND account_id = %(account_id)s
"""

# Single-JOIN ownership lookup used by cross-account flows where the
# service has only the account id and the authenticated user id (it
# does NOT receive the mailbox id from the request). ``GET_ACCOUNT``
# is keyed by ``(mailbox_id, account_id)`` and would force the service
# to first resolve the mailbox, which leaks the anti-leak policy
# D-22 (a foreign mailbox collapses to 403 while the account itself
# should uniformly 404). The JOIN here returns ``None`` for missing
# OR foreign accounts in one round trip, and the service converts
# the absence into ``AccountNotFound`` (HTTP 404) — same pattern as
# the email-attachments ownership chain.
GET_ACCOUNT_BY_ID_FOR_USER = """
    SELECT a.account_id, a.mailbox_id, a.provider, a.display_label,
           a.config, a.email_address, a.signature_html, a.created_at,
           m.owner_user_id
    FROM accounts a
    INNER JOIN mailboxes m ON m.mailbox_id = a.mailbox_id
    WHERE a.account_id    = %(account_id)s
      AND m.owner_user_id = %(user_id)s
"""

# Single-JOIN listing of every account the user owns across all of
# their mailboxes. Replaces the prior O(1 + N_mailboxes) pattern of
# ``mailbox_store.list_by_owner`` + one ``account_store.list_by_mailbox``
# per mailbox used by the virtual-mailbox ownership pre-check. Same
# JOIN shape as ``GET_ACCOUNT_BY_ID_FOR_USER`` minus the account_id
# filter.
LIST_ACCOUNT_IDS_BY_USER = """
    SELECT a.account_id
    FROM accounts a
    INNER JOIN mailboxes m ON m.mailbox_id = a.mailbox_id
    WHERE m.owner_user_id = %(user_id)s
"""

# Count of every account the user owns across all of their mailboxes. Backs
# the per-user account limit guard (create_account) and GET /accounts/quota.
# Counts ALL account rows including those with an expired token (decision 3A):
# a stale account still occupies storage and counts until deleted. Same JOIN
# shape as ``LIST_ACCOUNT_IDS_BY_USER`` reduced to a COUNT.
COUNT_ACCOUNTS_BY_USER = """
    SELECT COUNT(*) AS account_count
    FROM accounts a
    INNER JOIN mailboxes m ON m.mailbox_id = a.mailbox_id
    WHERE m.owner_user_id = %(user_id)s
"""

UPSERT_ACCOUNT = """
    INSERT INTO accounts (account_id, mailbox_id, provider, display_label, config, signature_html)
    VALUES (%(account_id)s, %(mailbox_id)s, %(provider)s, %(display_label)s, %(config)s::jsonb, %(signature_html)s)
    ON CONFLICT (account_id) DO UPDATE SET
        display_label = EXCLUDED.display_label,
        config = EXCLUDED.config,
        signature_html = EXCLUDED.signature_html
    RETURNING account_id, mailbox_id, provider, display_label, config, email_address, signature_html, created_at
"""

DELETE_ACCOUNT = """
    DELETE FROM accounts
    WHERE mailbox_id = %(mailbox_id)s AND account_id = %(account_id)s
"""

# ---------------------------------------------------------------------------
# Token operations (columns live in accounts since migration 0005)
# ---------------------------------------------------------------------------

SELECT_TOKENS_BY_CONTEXT = """
    SELECT
        account_id,
        access_token,
        refresh_token,
        access_token_encrypted,
        refresh_token_encrypted,
        encryption_key_id,
        expiry,
        scopes
    FROM accounts
    WHERE account_id = %(account_id)s
      AND mailbox_id = %(mailbox_id)s
      AND provider = %(provider)s
"""

UPSERT_TOKENS_ENCRYPTED = """
    UPDATE accounts
       SET access_token = NULL,
           refresh_token = NULL,
           access_token_encrypted = %(access_token_encrypted)s,
           refresh_token_encrypted = %(refresh_token_encrypted)s,
           encryption_key_id = %(encryption_key_id)s,
           expiry = %(expiry)s,
           scopes = %(scopes)s,
           email_address = COALESCE(%(email_address)s, email_address),
           tokens_updated_at = now()
     WHERE account_id = %(account_id)s
       AND mailbox_id = %(mailbox_id)s
       AND provider = %(provider)s
"""

UPSERT_TOKENS_PLAINTEXT = """
    UPDATE accounts
       SET access_token = %(access_token)s,
           refresh_token = %(refresh_token)s,
           access_token_encrypted = NULL,
           refresh_token_encrypted = NULL,
           encryption_key_id = NULL,
           expiry = %(expiry)s,
           scopes = %(scopes)s,
           email_address = COALESCE(%(email_address)s, email_address),
           tokens_updated_at = now()
     WHERE account_id = %(account_id)s
       AND mailbox_id = %(mailbox_id)s
       AND provider = %(provider)s
"""

BACKFILL_LEGACY_TOKENS = """
    UPDATE accounts
       SET access_token = NULL,
           refresh_token = NULL,
           access_token_encrypted = %(access_token_encrypted)s,
           refresh_token_encrypted = %(refresh_token_encrypted)s,
           encryption_key_id = %(encryption_key_id)s,
           tokens_updated_at = now()
     WHERE account_id = %(account_id)s
       AND mailbox_id = %(mailbox_id)s
       AND provider = %(provider)s
"""

# ---------------------------------------------------------------------------
# Sync cursor operations
# ---------------------------------------------------------------------------

GET_SYNC_CURSOR = """
    SELECT sync_cursor FROM accounts
    WHERE account_id = %(account_id)s AND mailbox_id = %(mailbox_id)s
"""

# Batch variant: all sync cursors for a mailbox in a single round trip
# (covered by idx_accounts_mailbox_id). Avoids the N+1 of GET_SYNC_CURSOR
# once per account during a metadata sync.
GET_SYNC_CURSORS_BY_MAILBOX = """
    SELECT account_id, sync_cursor FROM accounts
    WHERE mailbox_id = %(mailbox_id)s
"""

UPDATE_SYNC_CURSOR = """
    UPDATE accounts SET sync_cursor = %(sync_cursor)s
    WHERE account_id = %(account_id)s AND mailbox_id = %(mailbox_id)s
"""

