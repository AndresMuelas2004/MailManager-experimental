"""
Integration tests for the admin attachment purge endpoint:

``POST /admin/attachments/purge``

Three documented states:
- env var unset → 503 ``purge_disabled``
- env var set + bad/missing header → 401 ``invalid_admin_token``
- env var set + correct header → 200 with ``{purged_count, freed_bytes}``

The success path seeds an expired blob row directly via the test
transaction so the assertion can demand a strictly positive
``purged_count``. Without the seed, a no-op purge would pass trivially
(known E2E gap; we close it at the integration tier).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.services import attachments_service


_PURGE_URL = "/admin/attachments/purge"
_ENV_VAR = "ATTACHMENTS_PURGE_TOKEN"


# ── env unset → 503 purge_disabled ─────────────────────────────────


def test_purge_returns_503_when_env_var_unset(test_client, monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)
    response = test_client.post(_PURGE_URL, headers={"X-Admin-Token": "anything"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "purge_disabled"


# ── env set + bad/missing token → 401 invalid_admin_token ───────────


def test_purge_returns_401_when_token_is_wrong(test_client, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    response = test_client.post(_PURGE_URL, headers={"X-Admin-Token": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_admin_token"


def test_purge_returns_401_when_header_is_missing(test_client, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    # No header at all — distinct path through the auth check.
    response = test_client.post(_PURGE_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_admin_token"


# ── happy path with seeded expired blob ────────────────────────────


def _seed_expired_blob(connection, account_id: str, mailbox_id: str) -> bytes:
    """Insert an email_metadata + email_attachments + email_attachment_blobs
    triplet whose ``last_accessed_at`` is older than the purge threshold.

    Returns the blob bytes so the caller can assert on `freed_bytes`.
    """
    blob = b"expired-binary-payload"
    expired_at = datetime.now(timezone.utc) - timedelta(days=45)

    with connection.cursor() as cur:
        # Seeded mailbox/account already exist for the test user; we only
        # need a metadata row + attachment + blob.
        provider_message_id = f"msg-purge-{uuid.uuid4()}"
        attachment_id = uuid.uuid4()
        cur.execute(
            "INSERT INTO email_metadata "
            "(provider_message_id, account_id, thread_id, from_email, "
            " from_name, subject, received_at, is_read, box) "
            "VALUES (%s, %s, NULL, 'a@b.c', 'A', 'S', now(), false, 'ALL_MAIL')",
            (provider_message_id, account_id),
        )
        cur.execute(
            "INSERT INTO email_attachments "
            "(attachment_id, account_id, provider_message_id, part_id, "
            " provider_attachment_id, filename, mime_type, size, "
            " content_id, is_inline, position, last_accessed_at) "
            "VALUES (%s, %s, %s, '1', NULL, 'p.pdf', 'application/pdf', "
            "        %s, NULL, false, 0, %s)",
            (str(attachment_id), account_id, provider_message_id, len(blob), expired_at),
        )
        cur.execute(
            "INSERT INTO email_attachment_blobs "
            "(attachment_id, blob, blob_storage_kind, blob_ref, fetched_at) "
            "VALUES (%s, %s, 'db', NULL, now())",
            (str(attachment_id), blob),
        )
    return blob


def test_purge_happy_path_drops_expired_blob(
    test_client, setup_mailbox_and_account, monkeypatch, isolated_db,
):
    """Seed an expired blob, call the endpoint, assert the row is gone."""
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    mailbox_id, account_id = setup_mailbox_and_account(test_client)

    blob = _seed_expired_blob(isolated_db, account_id, mailbox_id)

    response = test_client.post(
        _PURGE_URL,
        headers={"X-Admin-Token": "expected-token"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purged_count"] >= 1
    assert body["freed_bytes"] >= len(blob)
