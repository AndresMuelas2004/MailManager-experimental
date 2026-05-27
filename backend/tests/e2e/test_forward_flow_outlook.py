"""
End-to-end Forward flow against real Outlook.

Outlook ``createForward`` inherits attachments from the original
message server-side (§6.2 / §15.2 — confirmed). The frontend still
calls ``copy-from-email`` uniformly across providers; for Outlook the
service detects the provider and returns ``copied_count=0`` as a
no-op — the attachments are already part of the provider draft.

Single test per file (common_mistakes.md §1).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import pytest

from ._forward_helpers import bootstrap_attachment_message
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


def _fetch_message_with_attachments(account_id: str) -> str | None:
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


def test_50_forward_flow_outlook(e2e_client):
    """End-to-end Forward flow against real Outlook."""
    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)
    candidate_pmid = _fetch_message_with_attachments(OUTLOOK_ACCOUNT_ID)
    if candidate_pmid is None:
        candidate_pmid = bootstrap_attachment_message(
            e2e_client, OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID,
        )
    if candidate_pmid is None:
        pytest.skip("No Outlook inbox message with attachments available")

    # 1. GET /reply-context?action=forward.
    ctx_resp = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
        f"/emails/{candidate_pmid}/reply-context?action=forward",
    )
    _assert_ok(ctx_resp)
    ctx = ctx_resp.json()
    assert ctx["reply_kind"] == "forward"
    assert ctx["subject"].lower().startswith(("fwd:", "fw:", "rv:", "reenv:"))
    assert ctx["to_recipients"] == []
    assert "Mensaje reenviado" in ctx["body"]

    # 2. POST /drafts — Outlook routes through Graph createForward,
    # inheriting the original's attachments server-side.
    ts = datetime.now(timezone.utc).isoformat()
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": f"{ctx['subject']} — E2E {ts}",
            "body": ctx["body"],
            "reply_kind": "forward",
            "reply_to_message_id": candidate_pmid,
            "reply_to_account_id": OUTLOOK_ACCOUNT_ID,
            "thread_id": ctx["thread_id"],
            "in_reply_to": ctx["in_reply_to"],
            "references_header": ctx["references"],
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        # 3. POST .../copy-from-email — for Outlook this is a no-op:
        # the inherited attachments are already on the provider draft.
        copy_resp = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/copy-from-email",
            json={
                "source_account_id": OUTLOOK_ACCOUNT_ID,
                "source_provider_message_id": candidate_pmid,
            },
        )
        _assert_ok(copy_resp)
        copy_data = copy_resp.json()
        # Outlook no-op contract: zero NEW copies but the response still
        # carries the current attachments list (createForward already
        # persisted them in draft_attachments at create time).
        assert copy_data["copied_count"] == 0

        # 4. Send the forward draft.
        send_resp = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        send_data = send_resp.json()
        assert send_data["status"] == "sent"
        assert send_data["provider"] == "outlook"
        # ImmutableId: send keeps the id stable.
        assert send_data["provider_message_id"] == provider_draft_id

        # 5. Local draft row + draft_attachments purged.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
                cur.execute(
                    "SELECT COUNT(*) FROM draft_attachments "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        _delete_draft_row_locally(provider_draft_id, OUTLOOK_ACCOUNT_ID)
