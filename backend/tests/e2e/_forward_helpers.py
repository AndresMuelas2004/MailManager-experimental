"""Shared bootstrap helpers for the forward-flow E2E tests.

The forward tests need an inbox message that carries at least one
downloadable attachment. When the test account does not have one
(fresh cleanup, first run on a new machine), the helper here
self-bootstraps by sending an email with an attachment from the
account to itself and polling the sync endpoint until the message
shows up with ``has_attachments = TRUE``. Both fixtures (Gmail and
Outlook) share this code path.

The leading underscore keeps pytest from collecting this file.
"""
from __future__ import annotations

import os
import time
import uuid

import psycopg2


_BOOTSTRAP_TIMEOUT_S = 120
_BOOTSTRAP_POLL_INTERVAL_S = 4
_BOOTSTRAP_ATTACHMENT_FILENAME = "e2e-forward-bootstrap.pdf"
_BOOTSTRAP_ATTACHMENT_BYTES = b"%PDF-1.4 e2e forward bootstrap payload"
_BOOTSTRAP_ATTACHMENT_MIME = "application/pdf"


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _account_email(account_id: str) -> str | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email_address FROM accounts WHERE account_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def fetch_attachment_message_id(account_id: str) -> str | None:
    """Return the latest local message_id flagged with attachments, or None."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id "
                "FROM email_metadata "
                "WHERE account_id = %s AND has_attachments = TRUE "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _row_exists(account_id: str, provider_message_id: str) -> bool:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _force_has_attachments(account_id: str, provider_message_id: str) -> None:
    """Workaround: bootstrap-only flag flip for messages we just sent.

    The cache-aside ``GET /content`` flow occasionally fails to populate
    ``email_attachments`` for self-addressed Outlook sends (the SENT
    folder copy reports no attachments via Graph despite the draft
    having uploaded one). We know the message carries the attachment
    because we just uploaded it on the originating draft, so we flip
    the flag manually here. The downstream forward flow always reads
    the attachment list from the provider in real time, so the local
    inconsistency does not propagate beyond the bootstrap.
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE email_metadata SET has_attachments = TRUE "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
        conn.commit()
    finally:
        conn.close()


def _has_attachments_flag(account_id: str, provider_message_id: str) -> bool:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT has_attachments FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            row = cur.fetchone()
            return bool(row and row[0])
    finally:
        conn.close()


def bootstrap_attachment_message(
    e2e_client, mailbox_id: str, account_id: str,
) -> str | None:
    """Ensure the account has a message flagged with attachments locally.

    Fast path: if one already exists locally (``has_attachments=TRUE``),
    return it. Otherwise send a self-addressed email with a PDF
    attachment, poll the sync endpoint until the just-sent
    ``provider_message_id`` is persisted in ``email_metadata``, prime
    the content cache so the cache-aside flow runs, and finally flip
    ``has_attachments`` to TRUE (forced — see
    :py:func:`_force_has_attachments` for the workaround rationale).
    Returns ``None`` when the bootstrap exceeds the timeout.
    """
    existing = fetch_attachment_message_id(account_id)
    if existing is not None:
        return existing

    own_email = _account_email(account_id)
    if not own_email:
        return None

    subject = f"E2E forward bootstrap {uuid.uuid4()}"
    create_resp = e2e_client.post(
        f"/mailboxes/{mailbox_id}/accounts/{account_id}/drafts",
        json={
            "to_recipients": [own_email],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": subject,
            "body": "E2E forward bootstrap payload.",
        },
    )
    if create_resp.status_code != 200:
        return None
    provider_draft_id = create_resp.json().get("provider_draft_id")
    if not provider_draft_id:
        return None

    upload_resp = e2e_client.post(
        f"/mailboxes/{mailbox_id}/accounts/{account_id}"
        f"/drafts/{provider_draft_id}/attachments",
        files={
            "file": (
                _BOOTSTRAP_ATTACHMENT_FILENAME,
                _BOOTSTRAP_ATTACHMENT_BYTES,
                _BOOTSTRAP_ATTACHMENT_MIME,
            ),
        },
    )
    if upload_resp.status_code not in (200, 201):
        return None

    send_resp = e2e_client.post(
        f"/mailboxes/{mailbox_id}/accounts/{account_id}"
        f"/drafts/{provider_draft_id}/send",
    )
    if send_resp.status_code != 200:
        return None
    sent_pmid = send_resp.json().get("provider_message_id") or ""
    if not sent_pmid:
        return None

    deadline = time.time() + _BOOTSTRAP_TIMEOUT_S
    while time.time() < deadline:
        e2e_client.post(f"/mailboxes/{mailbox_id}/emails/sync-metadata")
        if _row_exists(account_id, sent_pmid):
            content_resp = e2e_client.get(
                f"/mailboxes/{mailbox_id}/emails/{sent_pmid}/content"
                f"?account_id={account_id}",
            )
            if content_resp.status_code == 200:
                if not _has_attachments_flag(account_id, sent_pmid):
                    _force_has_attachments(account_id, sent_pmid)
                return sent_pmid
        time.sleep(_BOOTSTRAP_POLL_INTERVAL_S)

    return None
