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
import time
from datetime import datetime, timezone

import psycopg2
import pytest

from ._reply_helpers import bootstrap_reply_source
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
    # Bootstrap a source message whose body is retrievable from the provider
    # so the seeded reply quote (<blockquote>) is deterministic. The most
    # recent ALL_MAIL row is not safe on this account (body-less SENT shadow
    # copies) — see _reply_helpers for the full rationale.
    src = bootstrap_reply_source(e2e_client, OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID)
    if src is None:
        pytest.skip("Could not bootstrap an Outlook reply source message")
    original_pmid, original_thread_id = src

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
    # The reply body is HTML with the original inside a <blockquote>.
    assert "<blockquote" in ctx["body"]

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

        # Threading assertion (M18): the sent message row MUST be persisted
        # and belong to the same conversation as the original (Graph stitched
        # it server-side at createReply time). The post-send persist is
        # best-effort, so poll a bounded number of sync-metadata cycles, then
        # assert hard — the previous ``if row is not None`` verified nothing
        # whenever the persist soft-failed.
        deadline = time.time() + 60
        thread_id = None
        found = False
        while time.time() < deadline:
            conn = _db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT thread_id FROM email_metadata "
                        "WHERE account_id = %s AND provider_message_id = %s",
                        (OUTLOOK_ACCOUNT_ID, sent_pmid),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if row is not None:
                thread_id = row[0]
                found = True
                break
            e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
            time.sleep(4)
        assert found, "sent reply was never persisted to email_metadata"
        assert thread_id == original_thread_id

        # The sent body must be HTML at Graph. Fetch the sent message's content
        # through the cache-aside endpoint (it pulls the real Graph message,
        # whose ``body.contentType`` is HTML) and assert the rendered HTML
        # carries the quote markup — confirming the HTML body round-tripped.
        content_resp = e2e_client.get(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/{sent_pmid}/content"
            f"?account_id={OUTLOOK_ACCOUNT_ID}",
        )
        _assert_ok(content_resp)
        html_body = content_resp.json().get("html_body")
        assert html_body is not None
        assert "blockquote" in html_body.lower()
    finally:
        _delete_draft_row_locally(provider_draft_id, OUTLOOK_ACCOUNT_ID)
