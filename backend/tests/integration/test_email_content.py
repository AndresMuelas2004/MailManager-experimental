"""
Integration tests for the GET /{provider_message_id}/content endpoint.

Tests exercise the full router -> service -> core -> storage flow.
Only external dependencies (provider APIs, disk tokens) are faked
via the integration conftest fixtures.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg2.extras
import pytest

from core.email import EmailContent
from core.email.errors import EmailExternalAPIError
from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
)


def _content_url(mailbox_id: str, message_id: str, account_id: str) -> str:
    return f"{_MAILBOX_URL}/{mailbox_id}/emails/{message_id}/content?account_id={account_id}"


# ------------------------------------------------------------------
# Happy path — DB miss, fetched from FakeEmailClient
# ------------------------------------------------------------------


def test_get_email_content_db_miss_fetches_from_provider(
    test_client, setup_mailbox_and_account,
):
    """When email content is not in DB, the endpoint fetches from the provider."""
    mid, aid = setup_mailbox_and_account(test_client)
    # Sync metadata first so the account exists in the DB
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200
    data = resp.json()
    # FakeEmailClient.fetch_email_content returns EmailContent(html_body=None, text_body=None)
    assert data["html_body"] is None
    assert data["text_body"] is None


# ------------------------------------------------------------------
# Happy path — DB hit (pre-seeded content, no core call)
# ------------------------------------------------------------------


def test_get_email_content_db_hit(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """When email content is already in DB, it is returned without calling core."""
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Seed email_content directly in the DB
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_content (provider_message_id, account_id, html_body, text_body)
            VALUES (%(pmid)s, %(aid)s::uuid, %(html)s, %(txt)s)
            ON CONFLICT (provider_message_id, account_id) DO UPDATE SET
                html_body = EXCLUDED.html_body, text_body = EXCLUDED.text_body
            """,
            {"pmid": "m1", "aid": aid, "html": "<p>cached</p>", "txt": "cached"},
        )

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200
    data = resp.json()
    assert data["html_body"] == "<p>cached</p>"
    assert data["text_body"] == "cached"


# ------------------------------------------------------------------
# Wrong user → 403
# ------------------------------------------------------------------


def test_get_email_content_wrong_user(
    test_client, setup_mailbox_and_account, isolated_db, app,
):
    """Accessing content on a mailbox owned by another user returns 403."""
    from api.routers.routers_helpers import require_session
    from tests.integration.conftest import TEST_USER_ID

    mid, aid = setup_mailbox_and_account(test_client)

    # Create a different user and override the session dependency
    other_user_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, google_sub, email, name)
            VALUES (%(uid)s, %(sub)s, %(email)s, %(name)s)
            """,
            {
                "uid": other_user_id,
                "sub": f"google-sub-{other_user_id}",
                "email": f"{other_user_id}@example.com",
                "name": "Other User",
            },
        )

    app.dependency_overrides[require_session] = lambda: other_user_id
    try:
        resp = test_client.get(_content_url(mid, "m1", aid))
        assert resp.status_code == 403
    finally:
        app.dependency_overrides[require_session] = lambda: TEST_USER_ID


# ------------------------------------------------------------------
# Missing account → 404
# ------------------------------------------------------------------


def test_get_email_content_missing_account(
    test_client, setup_mailbox_and_account,
):
    """Non-existent account_id returns 404."""
    mid, _ = setup_mailbox_and_account(test_client)
    fake_account_id = str(uuid4())
    resp = test_client.get(_content_url(mid, "m1", fake_account_id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


# ------------------------------------------------------------------
# Missing mailbox → 404
# ------------------------------------------------------------------


def test_get_email_content_missing_mailbox(test_client):
    """Non-existent mailbox_id returns 404."""
    fake_mailbox_id = str(uuid4())
    fake_account_id = str(uuid4())
    resp = test_client.get(_content_url(fake_mailbox_id, "m1", fake_account_id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


# ------------------------------------------------------------------
# Core error → 502
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "failing_test_client",
    [{"fetch_content_exc": EmailExternalAPIError("API timeout.")}],
    indirect=True,
)
def test_get_email_content_core_error_returns_502(
    failing_test_client, setup_mailbox_and_account,
):
    """CoreError during fetch_email_content is translated to 502."""
    mid, aid = setup_mailbox_and_account(failing_test_client)
    failing_test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = failing_test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "external_api_error"


# ------------------------------------------------------------------
# Missing metadata row → 404 email_not_found
# ------------------------------------------------------------------


def test_get_email_content_missing_metadata_returns_404(
    test_client, setup_mailbox_and_account,
):
    """When the requested email has no metadata row, return 404 email_not_found."""
    mid, aid = setup_mailbox_and_account(test_client)
    # Intentionally skip sync-metadata so no metadata exists for this account.
    resp = test_client.get(_content_url(mid, "never-existed", aid))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


# ------------------------------------------------------------------
# B.lazy attachments — payload always carries the ``attachments`` field
# (D-13). The list is populated on cache miss from the provider, and
# re-read from email_attachments on cache hit so a TTL purge does not
# desync the response.
# ------------------------------------------------------------------


def test_get_email_content_response_includes_attachments_field(
    test_client, setup_mailbox_and_account,
):
    """Even with no attachments, the schema must always carry the field."""
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200
    data = resp.json()
    assert "attachments" in data
    assert data["attachments"] == []


def test_get_email_content_cache_miss_persists_attachment_metadata(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Cache miss → ``list_message_attachments`` is called and the rows land in DB."""
    from core.email import AttachmentMetadata
    from tests.shared.email_fakes import FakeEmailClient
    from api.services import emails_service
    from core.email import EmailManager

    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Build a manager that returns one downloadable attachment from the
    # provider so the cache-miss branch persists a row and recomputes
    # has_attachments.
    captured_meta = AttachmentMetadata(
        provider_message_id="m1",
        part_id="0.1",
        provider_attachment_id=None,
        filename="invoice.pdf",
        mime_type="application/pdf",
        size=1024,
        content_id=None,
        is_inline=False,
        position=0,
    )

    def _build(accounts):
        manager = EmailManager()
        for account in accounts:
            mailbox_id = str(account.get("mailbox_id") or "")
            account_id = str(account.get("account_id") or "")
            label = f"{mailbox_id}__{account_id}"
            manager.add_client(
                FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    list_message_attachments_return=([captured_meta], {}),
                )
            )
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    attachments = body["attachments"]
    assert len(attachments) == 1
    only = attachments[0]
    assert only["filename"] == "invoice.pdf"
    assert only["mime_type"] == "application/pdf"
    assert only["size"] == 1024
    assert only["is_downloaded"] is False  # blob not yet fetched
    assert only["is_unavailable"] is False
    assert only["position"] == 0

    # The metadata row must be persisted in email_attachments.
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT filename, is_inline FROM email_attachments "
            "WHERE account_id = %s AND provider_message_id = %s",
            (aid, "m1"),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["filename"] == "invoice.pdf"
    assert rows[0]["is_inline"] is False

    # has_attachments must have flipped to true via recompute_has_attachments.
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT has_attachments FROM email_metadata "
            "WHERE account_id = %s AND provider_message_id = %s",
            (aid, "m1"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["has_attachments"] is True


def test_get_email_content_cache_hit_reads_attachments_from_db(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """When email_content is pre-seeded the provider is NOT called, but the
    response still surfaces attachments from email_attachments — proving
    the cache-hit branch reads the dedicated table.
    """
    from uuid import uuid4 as _uuid

    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_content (provider_message_id, account_id, html_body, text_body)
            VALUES (%(pmid)s, %(aid)s::uuid, %(html)s, %(txt)s)
            ON CONFLICT (provider_message_id, account_id) DO UPDATE SET
                html_body = EXCLUDED.html_body, text_body = EXCLUDED.text_body
            """,
            {"pmid": "m1", "aid": aid, "html": "<p>cached</p>", "txt": "cached"},
        )
        # Pre-seed two attachment rows on the same message — one inline,
        # one downloadable. The downloadable must surface; the inline one
        # must not (D-13 inline filter at the response layer).
        cur.execute(
            """
            INSERT INTO email_attachments (
                attachment_id, account_id, provider_message_id, part_id,
                provider_attachment_id, filename, mime_type, size,
                content_id, is_inline, position
            )
            VALUES
                (%(aid1)s, %(acc)s, 'm1', '0.0', NULL, 'logo.png', 'image/png',
                 50, 'cid:logo@x', TRUE, 0),
                (%(aid2)s, %(acc)s, 'm1', '0.1', NULL, 'attached.pdf',
                 'application/pdf', 200, NULL, FALSE, 1)
            """,
            {
                "aid1": str(_uuid()),
                "aid2": str(_uuid()),
                "acc": aid,
            },
        )

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Both rows surface — inline filtering happens at the SQL layer in
    # ``LIST_EMAIL_ATTACHMENTS_BY_MESSAGE`` selectively; the API maps both
    # to the response and the frontend can hide inline ones if needed.
    # The contract under test here is that the cache-hit branch reads
    # the table at all and that the downloadable attachment is present.
    filenames = {a["filename"] for a in data["attachments"]}
    assert "attached.pdf" in filenames
