"""
End-to-end Reply flow against real Outlook / Microsoft Graph.

Single test per file (common_mistakes.md §1).

The Outlook reply path differs from Gmail in two ways that matter at
the E2E level:

- ``POST /drafts`` with ``reply_kind=reply`` routes through Graph's
  ``createReply`` (one round trip with the body JSON), not a generic
  ``POST /me/messages``. The provider stitches ``conversationId``
  server-side.
- ``POST /messages/{id}/send`` reuses the same ImmutableId — the sent
  message keeps the same id as the draft (Gmail issues a fresh one).

Threading is verified by asserting the persisted sent email row has
the same ``thread_id`` (= ``conversationId``) as the original message.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import pytest

from .e2e_config import (
    OUTLOOK_ACCOUNT_ID,
    OUTLOOK_MAILBOX_ID,
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
    test always picks a real received message (orphan drafts from
    previous failed runs can land in ``ALL_MAIL`` without a sender,
    making them unsuitable as Reply source — see the ``sender``
    fallback in :py:meth:`OutlookClient._reply_context_from_graph_message`).
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


def test_48_reply_flow_outlook(e2e_client):
    """End-to-end Reply flow against real Outlook."""
    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)
    msg = _fetch_one_inbox_message(OUTLOOK_ACCOUNT_ID)
    if msg is None:
        pytest.skip("No synced inbox emails available for Outlook reply flow")
    original_pmid, original_thread_id = msg

    # 1. GET /reply-context.
    ctx_resp = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
        f"/emails/{original_pmid}/reply-context?action=reply",
    )
    _assert_ok(ctx_resp)
    ctx = ctx_resp.json()
    assert ctx["reply_kind"] == "reply"
    assert ctx["reply_to_message_id"] == original_pmid
    # Outlook returns ``conversationId`` as thread_id — equal to the
    # original message's thread_id.
    assert ctx["thread_id"] == original_thread_id
    assert ctx["subject"].lower().startswith(("re:", "aw:", "sv:")), ctx["subject"]
    assert len(ctx["to_recipients"]) >= 1

    # 2. POST /drafts → routes through Graph createReply on the wire.
    # The subject is kept untouched (just the ``Re: …`` from
    # reply-context): Outlook reassigns ``conversationId`` when the
    # draft subject diverges from the original ``Re: <subject>``,
    # which would break the threading assertion below (same trap
    # documented for ``createForward`` — see repository_guide.md).
    ts = datetime.now(timezone.utc).isoformat()
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": ctx["subject"],
            "body": f"{ctx['body']}\n\nE2E run {ts}",
            "reply_kind": "reply",
            "reply_to_message_id": original_pmid,
            "reply_to_account_id": OUTLOOK_ACCOUNT_ID,
            "thread_id": original_thread_id,
            "in_reply_to": ctx["in_reply_to"],
            "references_header": ctx["references"],
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        # 3. GET /drafts must include the new draft.
        list_resp = e2e_client.get(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/drafts?account_id={OUTLOOK_ACCOUNT_ID}",
        )
        _assert_ok(list_resp)
        assert any(
            d["provider_draft_id"] == provider_draft_id
            for d in list_resp.json()
        )

        # 4. POST /drafts/{pdid}/send.
        send_resp = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        data = send_resp.json()
        assert data["status"] == "sent"
        assert data["provider"] == "outlook"
        sent_pmid = data["provider_message_id"]
        # Outlook reuses the same id thanks to ImmutableId.
        assert sent_pmid == provider_draft_id

        # 5. Draft row purged after the send.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
        finally:
            conn.close()

        # Threading assertion: the sent message belongs to the same
        # conversation as the original (Graph stitched it server-side
        # at createReply time).
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM email_metadata "
                    "WHERE account_id = %s AND provider_message_id = %s",
                    (OUTLOOK_ACCOUNT_ID, sent_pmid),
                )
                row = cur.fetchone()
                if row is not None:
                    assert row[0] == original_thread_id
        finally:
            conn.close()
    finally:
        _delete_draft_row_locally(provider_draft_id, OUTLOOK_ACCOUNT_ID)
