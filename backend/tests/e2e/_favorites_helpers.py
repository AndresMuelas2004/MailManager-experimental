"""Shared helpers for the favourites E2E flow (Gmail + Outlook mirrors).

Both ``test_favorites_flow_gmail.py`` and ``test_favorites_flow_outlook.py``
drive the same favourites surface, so the DB-side helpers live here to avoid
byte-for-byte duplication between the two provider mirrors (mirrors the
``_forward_helpers.py`` pattern already used by the forward flow).
"""

from __future__ import annotations

import os

import psycopg2


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _assert_ok(response, *, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


def _find_non_spam_trash_message(account_id: str) -> str | None:
    """Pick a real synced message that is NOT in SPAM/TRASH/DELETED."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box NOT IN ('SPAM', 'TRASH', 'DELETED') "
                "AND from_email IS NOT NULL AND from_email <> '' "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _select_is_favorite(account_id: str, provider_message_id: str) -> bool | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT is_favorite FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            row = cur.fetchone()
            return None if row is None else bool(row[0])
    finally:
        conn.close()
