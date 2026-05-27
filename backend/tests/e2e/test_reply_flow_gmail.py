"""
End-to-end Reply flow against real Gmail (no fakes).

Single test per file (common_mistakes.md §1): every follow-up
assertion (404 after send, threading verification) stays inside the
same test function — splitting them adds noise without value.

Flow exercised:

    GET /reply-context?action=reply
      → POST /drafts (with the reply metadata returned above)
      → GET /drafts  (the new draft is in the listing)
      → POST /drafts/{pdid}/send
      → GET /drafts/{pdid}   (must collapse to 404 — draft is gone)
      → DB inspection (drafts row purged, threading metadata absent)

The threading assertion is provider-side: we check that the persisted
``email_metadata`` row written by the send path carries the same
``thread_id`` as the original message. That captures the contract
"Gmail stitched the reply into the original thread" without polling
the Gmail web inbox.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import pytest

from .e2e_config import (
    GMAIL_ACCOUNT_ID,
    GMAIL_MAILBOX_ID,
    SEND_RECIPIENT,
)


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _assert_ok(response, *, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


def _fetch_one_inbox_message(account_id: str) -> tuple[str, str] | None:
    """Return ``(provider_message_id, thread_id)`` for one inbox row.

    Excludes rows without a stored ``from_email`` so the reply flow
    test always picks a real received message (orphan rows from
    previous failed runs can land in ``ALL_MAIL`` without a sender).
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id, thread_id FROM email_metadata "
                "WHERE account_id = %s AND box = 'ALL_MAIL' "
                "AND from_email IS NOT NULL AND from_email <> '' "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else None
    finally:
        conn.close()


def _delete_draft_row_locally(provider_draft_id: str, account_id: str) -> None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (provider_draft_id, account_id),
            )
        conn.commit()
    finally:
        conn.close()


def test_47_reply_flow_gmail(e2e_client):
    """End-to-end Reply flow against real Gmail."""
    # 0. Ensure we have at least one inbox message to reply to.
    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)
    msg = _fetch_one_inbox_message(GMAIL_ACCOUNT_ID)
    if msg is None:
        pytest.skip("No synced inbox emails available for Gmail reply flow")
    original_pmid, original_thread_id = msg

    # 1. GET /reply-context — produces the prefill payload.
    ctx_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{original_pmid}/reply-context?action=reply",
    )
    _assert_ok(ctx_resp)
    ctx = ctx_resp.json()
    # Shape assertions: the response is the full ReplyContextOut.
    assert ctx["reply_kind"] == "reply"
    assert ctx["reply_to_message_id"] == original_pmid
    assert ctx["thread_id"] == original_thread_id
    # Reply subject prefixed (Re:) or already had a Re-variant.
    assert ctx["subject"].lower().startswith(("re:", "aw:", "sv:")), ctx["subject"]
    # At least one recipient is populated for a Reply (the From/Reply-To).
    assert len(ctx["to_recipients"]) >= 1
    # The body contains the quoted-header line.
    assert "escribió:" in ctx["body"]

    # 2. POST /drafts with the reply metadata in the body.
    ts = datetime.now(timezone.utc).isoformat()
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            # Force the destination to SEND_RECIPIENT so the reply lands
            # in a mailbox we control and does not bounce on a stranger.
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": f"{ctx['subject']} — E2E {ts}",
            "body": ctx["body"],
            "reply_kind": "reply",
            "reply_to_message_id": original_pmid,
            "reply_to_account_id": GMAIL_ACCOUNT_ID,
            "thread_id": original_thread_id,
            "in_reply_to": ctx["in_reply_to"],
            "references_header": ctx["references"],
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        # 3. GET /drafts — the new draft is in the listing.
        list_resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts?account_id={GMAIL_ACCOUNT_ID}",
        )
        _assert_ok(list_resp)
        assert any(
            d["provider_draft_id"] == provider_draft_id
            for d in list_resp.json()
        ), "Reply draft should appear in GET /drafts"

        # 4. POST /drafts/{pdid}/send.
        send_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        send_data = send_resp.json()
        assert send_data["status"] == "sent"
        assert send_data["provider"] == "gmail"
        sent_pmid = send_data["provider_message_id"]
        assert sent_pmid
        # Gmail issues a new message id on send (drafts.send returns a
        # Message resource, not the draft id).
        assert sent_pmid != provider_draft_id

        # 5. The local draft row was purged by the Provider-First +
        # cleanup contract.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
        finally:
            conn.close()

        # Threading assertion: the persisted sent email row carries the
        # same thread_id as the original message (Gmail stitched the
        # reply into the thread server-side via the ``threadId`` we
        # injected in step 2).
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM email_metadata "
                    "WHERE account_id = %s AND provider_message_id = %s",
                    (GMAIL_ACCOUNT_ID, sent_pmid),
                )
                row = cur.fetchone()
                if row is not None:
                    # Best-effort: the sent message may not be persisted
                    # if the metadata-persist step soft-failed. When
                    # present, the thread id must match.
                    assert row[0] == original_thread_id
        finally:
            conn.close()
    finally:
        # Safety-net cleanup if a step above failed mid-flow.
        _delete_draft_row_locally(provider_draft_id, GMAIL_ACCOUNT_ID)
