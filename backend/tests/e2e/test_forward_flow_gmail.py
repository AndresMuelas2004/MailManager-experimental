"""
End-to-end Forward flow against real Gmail.

Gmail has no ``createForward`` primitive (§4.1, §15.1 forward-gmail
research notes) — Forward = ``drafts.create`` + ``threadId`` +
``In-Reply-To`` / ``References`` headers in the MIME, with attachments
re-uploaded via ``copy-from-email``.

Single test per file (common_mistakes.md §1): the follow-up GET (404
after send) lives in the same test function.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import psycopg2
import pytest

from ._forward_helpers import bootstrap_attachment_message
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


def _fetch_message_with_attachments(account_id: str) -> str | None:
    """Find one message in the local DB that already has downloadable
    attachments — the copy-from-email path needs an email with
    attachments to copy from.

    Returns ``provider_message_id`` or ``None`` when no candidate exists.
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.provider_message_id "
                "FROM email_metadata m "
                "WHERE m.account_id = %s AND m.has_attachments = TRUE "
                "ORDER BY m.received_at DESC NULLS LAST LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
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


def test_49_forward_flow_gmail(e2e_client):
    """End-to-end Forward flow against real Gmail."""
    # 0. Find an inbox message with downloadable attachments. If the
    # account doesn't have one yet, prime the cache by pulling content
    # for one row (which classifies its attachments via the cache-aside
    # path). The test still skips if no attachment-bearing message
    # exists.
    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)
    candidate_pmid = _fetch_message_with_attachments(GMAIL_ACCOUNT_ID)
    if candidate_pmid is None:
        candidate_pmid = bootstrap_attachment_message(
            e2e_client, GMAIL_MAILBOX_ID, GMAIL_ACCOUNT_ID,
        )
    if candidate_pmid is None:
        pytest.skip("No Gmail inbox message with attachments available")

    # 1. GET /reply-context?action=forward.
    ctx_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{candidate_pmid}/reply-context?action=forward",
    )
    _assert_ok(ctx_resp)
    ctx = ctx_resp.json()
    assert ctx["reply_kind"] == "forward"
    # Forward subject starts with ``Fwd:`` (or an already-existing FW prefix).
    assert ctx["subject"].lower().startswith(("fwd:", "fw:", "rv:", "reenv:"))
    # Forward pre-fills no recipients (the user adds them).
    assert ctx["to_recipients"] == []
    # The forward body is HTML: the "Mensaje reenviado" block + a <blockquote>.
    assert "Mensaje reenviado" in ctx["body"]
    assert "<blockquote" in ctx["body"]

    # 2. POST /drafts to create the Forward draft.
    ts = datetime.now(timezone.utc).isoformat()
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": f"{ctx['subject']} — E2E {ts}",
            "body": ctx["body"],
            "reply_kind": "forward",
            "reply_to_message_id": candidate_pmid,
            "reply_to_account_id": GMAIL_ACCOUNT_ID,
            "thread_id": ctx["thread_id"],
            "in_reply_to": ctx["in_reply_to"],
            "references_header": ctx["references"],
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        # 3. POST .../copy-from-email — for Gmail this downloads +
        # re-uploads each source attachment.
        copy_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/copy-from-email",
            json={
                "source_account_id": GMAIL_ACCOUNT_ID,
                "source_provider_message_id": candidate_pmid,
            },
        )
        _assert_ok(copy_resp)
        copy_data = copy_resp.json()
        # Gmail path: at least one attachment must have been copied.
        # (The source message was selected via has_attachments=TRUE.)
        assert copy_data["copied_count"] >= 1
        assert len(copy_data["attachments"]) >= 1

        # 4. Send the forward draft.
        send_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        send_data = send_resp.json()
        assert send_data["status"] == "sent"
        assert send_data["provider"] == "gmail"

        # 5. Local draft row + draft_attachments purged.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
                cur.execute(
                    "SELECT COUNT(*) FROM draft_attachments "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()

        # 6. The sent forward body must arrive as HTML at the provider. The
        # post-send metadata persist is best-effort, so poll a bounded number
        # of sync-metadata cycles until the sent message lands in
        # email_metadata, then fetch its rendered content and assert the quote
        # markup survived the multipart/alternative text/html leg (same
        # round-trip the reply flow verifies).
        sent_pmid = send_data["provider_message_id"]
        deadline = time.time() + 60
        synced = False
        while time.time() < deadline:
            conn = _db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM email_metadata "
                        "WHERE account_id = %s AND provider_message_id = %s",
                        (GMAIL_ACCOUNT_ID, sent_pmid),
                    )
                    synced = cur.fetchone() is not None
            finally:
                conn.close()
            if synced:
                break
            e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
            time.sleep(4)
        assert synced, "sent forward was never persisted to email_metadata"
        content_resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{sent_pmid}/content"
            f"?account_id={GMAIL_ACCOUNT_ID}",
        )
        _assert_ok(content_resp)
        html_body = content_resp.json().get("html_body")
        assert html_body is not None
        assert "blockquote" in html_body.lower()
    finally:
        _delete_draft_row_locally(provider_draft_id, GMAIL_ACCOUNT_ID)
