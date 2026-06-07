"""
Integration tests for the received-email attachment download endpoint:

``GET /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/attachments/{attachment_id}``

The endpoint is a cache-aside binary stream (D-06). These tests exercise
the cache-HIT path (blob already in ``email_attachment_blobs``) so no
provider call is needed: the contract under test is that the response
carries a ``Content-Disposition`` with the real filename+extension and a
``Content-Type`` equal to the stored ``mime_type``. That header is what
the download-name bug broke downstream, and the fix exposes it to the
browser via CORS (covered by the dedicated CORS test below).
"""

from __future__ import annotations

import uuid

import psycopg2

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL


def _download_url(mailbox_id: str, account_id: str, message_id: str, attachment_id: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}"
        f"/emails/{message_id}/attachments/{attachment_id}"
    )


def _seed_attachment_with_blob(
    connection, account_id: str, provider_message_id: str,
    *, filename: str, mime_type: str, blob: bytes,
) -> str:
    """Insert an ``email_attachments`` row + its blob, return attachment_id.

    The caller must already have a metadata row for ``provider_message_id``
    (the download ownership JOIN walks email_attachments → email_metadata →
    accounts → mailboxes).
    """
    attachment_id = str(uuid.uuid4())
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_attachments
                (attachment_id, account_id, provider_message_id, part_id,
                 provider_attachment_id, filename, mime_type, size,
                 content_id, is_inline, position)
            VALUES (%s, %s, %s, '0.1', NULL, %s, %s, %s, NULL, false, 0)
            """,
            (attachment_id, account_id, provider_message_id, filename,
             mime_type, len(blob)),
        )
        cur.execute(
            "INSERT INTO email_attachment_blobs "
            "(attachment_id, blob, blob_storage_kind, blob_ref, fetched_at) "
            "VALUES (%s, %s, 'db', NULL, now())",
            (attachment_id, psycopg2.Binary(blob)),
        )
    return attachment_id


# ------------------------------------------------------------------
# Cache hit → streamed binary with correct download headers
# ------------------------------------------------------------------


def test_download_returns_content_disposition_with_real_filename(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """The download response carries the real name+extension on
    ``Content-Disposition`` and the stored mime on ``Content-Type``.
    """
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    blob = b"%PDF-1.4 cached attachment bytes"
    attachment_id = _seed_attachment_with_blob(
        isolated_db, aid, "m1",
        filename="contrato.pdf", mime_type="application/pdf", blob=blob,
    )

    resp = test_client.get(_download_url(mid, aid, "m1", attachment_id))
    assert resp.status_code == 200, resp.text
    # Body is the cached blob (cache hit — no provider call).
    assert resp.content == blob
    # The real filename+extension travels on Content-Disposition (both the
    # ASCII fallback form and the RFC 5987 UTF-8 form).
    disposition = resp.headers["content-disposition"]
    assert 'filename="contrato.pdf"' in disposition
    assert "filename*=UTF-8''contrato.pdf" in disposition
    # Content-Type is the stored mime_type, not a generic stream type.
    assert resp.headers["content-type"].startswith("application/pdf")
    # The download is forced and unsniffed.
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_download_unicode_filename_is_percent_encoded(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """A UTF-8 filename round-trips via the RFC 5987 ``filename*`` form."""
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    attachment_id = _seed_attachment_with_blob(
        isolated_db, aid, "m1",
        filename="Información.pdf", mime_type="application/pdf",
        blob=b"unicode-name-bytes",
    )

    resp = test_client.get(_download_url(mid, aid, "m1", attachment_id))
    assert resp.status_code == 200, resp.text
    import urllib.parse
    encoded = urllib.parse.quote("Información.pdf", safe="")
    assert f"filename*=UTF-8''{encoded}" in resp.headers["content-disposition"]


# ------------------------------------------------------------------
# Missing attachment → 404 (no provider leak — D-22)
# ------------------------------------------------------------------


def test_download_missing_attachment_returns_404(
    test_client, setup_mailbox_and_account,
):
    """An unknown attachment_id surfaces 404 ``attachment_not_found``."""
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.get(_download_url(mid, aid, "m1", str(uuid.uuid4())))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "attachment_not_found"


# ------------------------------------------------------------------
# CORS — Content-Disposition must be on Access-Control-Expose-Headers
# so a cross-origin browser fetch can read it (the root of the fix).
# ------------------------------------------------------------------


def test_cors_exposes_content_disposition_header(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """A request carrying a matching ``Origin`` gets an
    ``Access-Control-Expose-Headers`` listing ``Content-Disposition`` —
    without this, the cross-origin browser fetch cannot read the filename.
    """
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    attachment_id = _seed_attachment_with_blob(
        isolated_db, aid, "m1",
        filename="contrato.pdf", mime_type="application/pdf",
        blob=b"cors-bytes",
    )

    resp = test_client.get(
        _download_url(mid, aid, "m1", attachment_id),
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200, resp.text
    exposed = resp.headers.get("access-control-expose-headers", "")
    assert "Content-Disposition" in exposed
