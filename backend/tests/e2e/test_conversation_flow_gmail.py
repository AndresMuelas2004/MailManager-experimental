"""
End-to-end conversation viewer flow against real Gmail (no fakes).

Single test per logical flow (common_mistakes.md §1): the lazy-sync
side-effect verification (a re-listing surfaces newly-persisted thread
members) stays inside the same test as the conversation fetch.

Flow exercised:

    POST /emails/sync-metadata                      (seed local copy)
      → GET  /emails?group_by_thread=true           (rows come grouped)
      → GET  /emails/{pmid}/conversation            (full chain, ascending)
      → GET  /emails?group_by_thread=false          (lazy-sync side effect)
      → GET  /emails/{pmid}/conversation (reopen)   (served again)

The endpoint reads the base message from the local DB, fetches the whole
thread from Gmail, lazily completes the local copy, and returns the chain
mapped from Gmail's fresh state ordered oldest-first.
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from .e2e_config import GMAIL_ACCOUNT_ID, GMAIL_MAILBOX_ID


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _assert_ok(response, *, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


def _find_multi_message_thread(account_id: str) -> tuple[str, str, int] | None:
    """Return ``(representative_pmid, thread_id, count)`` for the largest
    synced thread with more than one message in the same box, or ``None``."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id, COUNT(*) AS n
                FROM email_metadata
                WHERE account_id = %s AND thread_id IS NOT NULL AND thread_id <> ''
                  AND box = 'ALL_MAIL'
                GROUP BY thread_id
                HAVING COUNT(*) > 1
                ORDER BY n DESC
                LIMIT 1
                """,
                (account_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            thread_id, count = row[0], int(row[1])
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND thread_id = %s "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1",
                (account_id, thread_id),
            )
            rep = cur.fetchone()
            return (rep[0], thread_id, count) if rep else None
    finally:
        conn.close()


def _find_any_message(account_id: str) -> str | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box = 'ALL_MAIL' "
                "AND from_email IS NOT NULL AND from_email <> '' "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def test_48_conversation_grouped_listing_gmail(e2e_client):
    """``group_by_thread=true`` returns conversation-collapsed rows."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))

    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID,
                "group_by_thread": "true", "limit": 50},
    )
    _assert_ok(resp)
    body = resp.json()
    rows = body["items"]
    if not rows:
        pytest.skip("No synced Gmail emails available for grouped listing")
    # Every grouped row carries a thread_message_count >= 1; the grouped total
    # counts threads, so it is never larger than the raw message count.
    assert all(r["thread_message_count"] >= 1 for r in rows)
    assert all("provider_message_id" in r for r in rows)
    # Grouped rows are unique per (thread within box) — no duplicate thread_id
    # among rows that have a non-empty thread_id.
    threaded = [r["thread_id"] for r in rows if r["thread_id"]]
    assert len(threaded) == len(set(threaded)), "a thread must collapse to one row"


def test_49_conversation_chain_and_lazy_sync_gmail(e2e_client):
    """Full conversation chain for a multi-message thread + lazy-sync side effect."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    found = _find_multi_message_thread(GMAIL_ACCOUNT_ID)
    if found is None:
        pytest.skip("No multi-message Gmail thread available in the test account")
    rep_pmid, thread_id, _local_count = found

    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{rep_pmid}/conversation",
    )
    _assert_ok(resp)
    body = resp.json()
    assert body["thread_id"] == thread_id
    messages = body["messages"]
    # The thread has more than one message (the whole chain from Gmail, which
    # can exceed the per-box local count).
    assert len(messages) >= 2
    # Oldest-first ordering.
    received = [m["received_at"] for m in messages]
    assert received == sorted(received)
    # Every message belongs to this thread and the viewer leaves
    # has_attachments False (B.lazy).
    assert all(m["thread_id"] == thread_id for m in messages)
    assert all(m["has_attachments"] is False for m in messages)
    chain_ids = {m["provider_message_id"] for m in messages}

    # Lazy-sync side effect (same test, common_mistakes §1): every message of
    # the chain is now present locally — a subsequent ungrouped listing across
    # boxes surfaces the whole chain.
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata WHERE account_id = %s",
                (GMAIL_ACCOUNT_ID,),
            )
            persisted = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert chain_ids.issubset(persisted), "lazy sync must persist every thread member"

    # Reopen: a second fetch still returns the same chain (served fresh from
    # the provider each open; membership is stable).
    reopen = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{rep_pmid}/conversation",
    )
    _assert_ok(reopen)
    assert {m["provider_message_id"] for m in reopen.json()["messages"]} == chain_ids


def test_50_conversation_single_message_gmail(e2e_client):
    """A conversation fetch on any message returns at least that one message."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_any_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for single-message conversation")

    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{pmid}/conversation",
    )
    _assert_ok(resp)
    messages = resp.json()["messages"]
    # At minimum the message itself is present; the base message is always in
    # its own thread.
    assert len(messages) >= 1
    assert pmid in {m["provider_message_id"] for m in messages}


def test_51_conversation_missing_message_returns_404_gmail(e2e_client):
    """A provider_message_id with no local row collapses to 404 email_not_found."""
    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/never-existed-pmid-e2e/conversation",
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"
