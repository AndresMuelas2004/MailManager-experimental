"""
End-to-end conversation viewer flow against real Outlook / Graph (no fakes).

Single test per logical flow (common_mistakes.md §1): the lazy-sync
side-effect verification stays inside the same test as the conversation
fetch.

Flow exercised:

    POST /emails/sync-metadata                      (seed local copy)
      → GET  /emails?group_by_thread=true           (rows come grouped)
      → GET  /emails/{pmid}/conversation            (full chain, ascending)
      → DB inspection                               (lazy-sync side effect)
      → GET  /emails/{pmid}/conversation (reopen)   (served again)

Outlook's ``conversationId`` is base64 (``+`` / ``/`` / ``=``) and the
ImmutableId can contain ``/``, so the message id is percent-encoded into
the path. The endpoint reconstructs the thread via
``$filter=conversationId eq '...'`` across Sent / Junk / Deleted.
"""

from __future__ import annotations

import os
import urllib.parse

import psycopg2
import pytest

from .e2e_config import OUTLOOK_ACCOUNT_ID, OUTLOOK_MAILBOX_ID


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _assert_ok(response, *, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


def _conversation_path(mailbox_id: str, account_id: str, provider_message_id: str) -> str:
    # The ImmutableId can contain '/', so it must be percent-encoded to stay a
    # single path segment (the production frontend does the same).
    return (
        f"/mailboxes/{mailbox_id}/accounts/{account_id}"
        f"/emails/{urllib.parse.quote(provider_message_id, safe='')}/conversation"
    )


def _find_multi_message_thread(account_id: str) -> tuple[str, str, int] | None:
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


def test_52_conversation_grouped_listing_outlook(e2e_client):
    """``group_by_thread=true`` returns conversation-collapsed rows."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))

    resp = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": OUTLOOK_ACCOUNT_ID,
                "group_by_thread": "true", "limit": 50},
    )
    _assert_ok(resp)
    rows = resp.json()["items"]
    if not rows:
        pytest.skip("No synced Outlook emails available for grouped listing")
    assert all(r["thread_message_count"] >= 1 for r in rows)
    threaded = [r["thread_id"] for r in rows if r["thread_id"]]
    assert len(threaded) == len(set(threaded)), "a thread must collapse to one row"


def test_53_conversation_chain_and_lazy_sync_outlook(e2e_client):
    """Full conversation chain for a multi-message thread + lazy-sync side effect."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    found = _find_multi_message_thread(OUTLOOK_ACCOUNT_ID)
    if found is None:
        pytest.skip("No multi-message Outlook thread available in the test account")
    rep_pmid, thread_id, _local_count = found

    resp = e2e_client.get(_conversation_path(OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID, rep_pmid))
    _assert_ok(resp)
    body = resp.json()
    assert body["thread_id"] == thread_id
    messages = body["messages"]
    assert len(messages) >= 2
    received = [m["received_at"] for m in messages]
    assert received == sorted(received)
    assert all(m["thread_id"] == thread_id for m in messages)
    assert all(m["has_attachments"] is False for m in messages)
    chain_ids = {m["provider_message_id"] for m in messages}

    # Lazy-sync side effect (same test, common_mistakes §1).
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata WHERE account_id = %s",
                (OUTLOOK_ACCOUNT_ID,),
            )
            persisted = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert chain_ids.issubset(persisted), "lazy sync must persist every thread member"

    # Reopen returns the same chain.
    reopen = e2e_client.get(_conversation_path(OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID, rep_pmid))
    _assert_ok(reopen)
    assert {m["provider_message_id"] for m in reopen.json()["messages"]} == chain_ids


def test_54_conversation_single_message_outlook(e2e_client):
    """A conversation fetch on a synced message returns a non-empty chain.

    Note (Outlook ID-encoding asymmetry): the id Graph returns for a message
    via the delta sync path differs from the id the same message carries in
    the ``$filter=conversationId`` response (different id flavours), so this
    test does NOT assert the picked ``pmid`` is echoed back verbatim — the
    contract here is that the base row resolves to a thread and the chain
    comes back ordered, every member tagged with the same thread_id.
    """
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_any_message(OUTLOOK_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Outlook emails available for single-message conversation")

    resp = e2e_client.get(_conversation_path(OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID, pmid))
    _assert_ok(resp)
    body = resp.json()
    messages = body["messages"]
    assert len(messages) >= 1
    received = [m["received_at"] for m in messages]
    assert received == sorted(received)
    # When the base message has a real thread, every returned member shares it.
    if body["thread_id"]:
        assert all(m["thread_id"] == body["thread_id"] for m in messages)


def test_55_conversation_missing_message_returns_404_outlook(e2e_client):
    """A provider_message_id with no local row collapses to 404 email_not_found."""
    resp = e2e_client.get(
        _conversation_path(OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID, "never-existed-pmid-e2e"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"
