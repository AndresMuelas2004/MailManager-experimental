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
    patch_emails_build_manager,
)


def _content_url(mailbox_id: str, message_id: str, account_id: str) -> str:
    return f"{_MAILBOX_URL}/{mailbox_id}/emails/{message_id}/content?account_id={account_id}"


def _seed_metadata(
    cur, account_id: str, provider_message_id: str, *,
    received_at: str = "now()", is_read: bool = False, box: str = "ALL_MAIL",
) -> None:
    """Insert one ``email_metadata`` row directly. ``received_at`` is an SQL
    expression (``now()`` or a literal timestamptz) so prefetch-window tests can
    place a row inside / outside the 48h window deterministically."""
    cur.execute(
        f"""
        INSERT INTO email_metadata (
            provider_message_id, account_id, thread_id, from_email,
            from_name, subject, received_at, is_read, box
        )
        VALUES (%(pmid)s, %(aid)s::uuid, %(thr)s, %(fe)s, %(fn)s, %(subj)s,
                {received_at}, %(read)s, %(box)s)
        """,
        {
            "pmid": provider_message_id, "aid": account_id, "thr": f"t-{provider_message_id}",
            "fe": "sender@example.com", "fn": "Sender", "subj": f"subj-{provider_message_id}",
            "read": is_read, "box": box,
        },
    )


def _patch_content_manager(monkeypatch, *, html_body=None, text_body=None, **client_kwargs):
    """Patch ``build_manager_for_accounts`` so every account's ``FakeEmailClient``
    returns the given body from the unified content read. Used by the prefetch
    tests to prove the body lands in ``email_content``."""
    from tests.shared.email_fakes import FakeEmailClient
    from core.email import EmailManager

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
                    email_content=EmailContent(html_body=html_body, text_body=text_body),
                    **client_kwargs,
                )
            )
        return manager

    patch_emails_build_manager(monkeypatch, _build)


def _fetch_content_row(cur, account_id: str, provider_message_id: str):
    """Return ``(html_body, text_body, last_accessed_at)`` for a cached row, or None."""
    cur.execute(
        "SELECT html_body, text_body, last_accessed_at FROM email_content "
        "WHERE account_id = %s AND provider_message_id = %s",
        (account_id, provider_message_id),
    )
    return cur.fetchone()


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
    # FakeEmailClient.fetch_content_with_attachments returns the default
    # EmailContent(html_body=None, text_body=None). ``sample_metadata`` is dated
    # 2024 (outside the 48h prefetch window) so the post-sync prefetch caches
    # nothing and this GET is a genuine cache MISS.
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
            INSERT INTO users (user_id, auth_provider, provider_sub, email, name)
            VALUES (%(uid)s, %(provider)s, %(sub)s, %(email)s, %(name)s)
            """,
            {
                "uid": other_user_id,
                "provider": "google",
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
def test_get_email_content_provider_error_returns_502(
    failing_test_client, setup_mailbox_and_account,
):
    """A provider failure on the cache-miss read is translated to 502.

    After the D4 unification the viewer makes a SINGLE provider call —
    ``fetch_content_with_attachments`` (body + attachments fused) — so there is
    now exactly ONE provider failure point. The fake raises it via
    ``fetch_content_exc``. The old companion test that injected
    ``list_message_attachments_exc`` was removed: the viewer no longer calls
    ``list_message_attachments`` (that method survives only for the Outlook
    Forward path), so that injection is no longer read here and would assert a
    200 instead of a 502.
    """
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

    patch_emails_build_manager(monkeypatch, _build)

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
    # Neither ``LIST_EMAIL_ATTACHMENTS_BY_MESSAGE`` nor
    # ``_load_email_attachments_out`` filters inline rows: BOTH the inline
    # and the downloadable attachment surface in the response (hiding inline
    # parts is a frontend concern). This pins the actual contract so an
    # inline-filter regression introduced at this layer is caught — and
    # proves the cache-hit branch reads the dedicated table.
    filenames = {a["filename"] for a in data["attachments"]}
    assert "attached.pdf" in filenames
    assert "logo.png" in filenames


def test_get_email_content_cache_hit_surfaces_derived_attachment_flags(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """The derived ``is_downloaded`` (a blob row EXISTS) and
    ``is_unavailable`` (``unavailable_at`` set, D-17) flags must reflect DB
    state. Every other attachment test only ever sees the false/false case;
    this pins the true cases the frontend relies on to decide whether a
    download is available or already cached.
    """
    from uuid import uuid4 as _uuid

    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    downloaded_id = str(_uuid())
    unavailable_id = str(_uuid())
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
        cur.execute(
            """
            INSERT INTO email_attachments (
                attachment_id, account_id, provider_message_id, part_id,
                provider_attachment_id, filename, mime_type, size,
                content_id, is_inline, position
            )
            VALUES
                (%(did)s, %(acc)s, 'm1', '1', NULL, 'downloaded.pdf',
                 'application/pdf', 100, NULL, FALSE, 0),
                (%(uid)s, %(acc)s, 'm1', '2', NULL, 'gone.pdf',
                 'application/pdf', 100, NULL, FALSE, 1)
            """,
            {"did": downloaded_id, "uid": unavailable_id, "acc": aid},
        )
        # downloaded.pdf has a cached blob → is_downloaded must be true.
        cur.execute(
            """
            INSERT INTO email_attachment_blobs
                (attachment_id, blob, blob_storage_kind, blob_ref, fetched_at)
            VALUES (%(aid)s, %(blob)s, 'db', NULL, now())
            """,
            {"aid": downloaded_id, "blob": psycopg2.Binary(b"PDFDATA")},
        )
        # gone.pdf was stamped unavailable (D-17) → is_unavailable must be true.
        cur.execute(
            "UPDATE email_attachments SET unavailable_at = now() "
            "WHERE attachment_id = %(aid)s",
            {"aid": unavailable_id},
        )

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200, resp.text
    by_name = {a["filename"]: a for a in resp.json()["attachments"]}
    assert by_name["downloaded.pdf"]["is_downloaded"] is True
    assert by_name["downloaded.pdf"]["is_unavailable"] is False
    assert by_name["gone.pdf"]["is_downloaded"] is False
    assert by_name["gone.pdf"]["is_unavailable"] is True


def test_get_email_content_cache_miss_sanitizes_persisted_filename(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """B-SANITIZE: a provider attachment whose filename carries path
    traversal / reserved characters is persisted with a sanitised name
    (same D-20 treatment drafts already get). The resolved ``mime_type``
    is persisted verbatim (resolution happens in the provider client).
    """
    from core.email import AttachmentMetadata
    from tests.shared.email_fakes import FakeEmailClient
    from core.email import EmailManager

    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    dangerous = AttachmentMetadata(
        provider_message_id="m1",
        part_id="0.1",
        provider_attachment_id=None,
        filename="../../etc/passwd.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=2048,
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
                    list_message_attachments_return=([dangerous], {}),
                )
            )
        return manager

    patch_emails_build_manager(monkeypatch, _build)

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200, resp.text

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT filename, mime_type FROM email_attachments "
            "WHERE account_id = %s AND provider_message_id = %s",
            (aid, "m1"),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    persisted = rows[0]["filename"]
    assert ".." not in persisted
    assert "/" not in persisted
    assert persisted.endswith(".xlsx")
    # mime_type is persisted exactly as the provider client resolved it.
    assert rows[0]["mime_type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ==================================================================
# Sliding TTL — a cache HIT refreshes ``last_accessed_at`` (but not the body).
# ==================================================================


def test_get_email_content_cache_hit_refreshes_last_accessed(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """Opening a cached body counts as an access: the HIT branch bumps
    ``last_accessed_at`` to ~now so frequently-read mail never expires. A row
    seeded with a 60-day-old ``last_accessed_at`` must come back fresh."""
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_content (
                provider_message_id, account_id, html_body, text_body,
                fetched_at, last_accessed_at
            )
            VALUES (%(pmid)s, %(aid)s::uuid, %(html)s, %(txt)s,
                    now() - INTERVAL '60 days', now() - INTERVAL '60 days')
            """,
            {"pmid": "m1", "aid": aid, "html": "<p>cached</p>", "txt": "cached"},
        )

    resp = test_client.get(_content_url(mid, "m1", aid))
    assert resp.status_code == 200

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT fetched_at, last_accessed_at, "
            "       now() - last_accessed_at AS recency "
            "FROM email_content WHERE account_id = %s AND provider_message_id = %s",
            (aid, "m1"),
        )
        row = cur.fetchone()
    # last_accessed_at jumped to ~now (well under a minute old).
    assert row["recency"].total_seconds() < 60
    # The body is immutable — a read is not a re-fetch, so fetched_at stays
    # ~60 days old (the touch must NOT bump it, or the E2E HIT assertions break).
    fetched_age = (row["last_accessed_at"] - row["fetched_at"]).total_seconds()
    assert fetched_age > 59 * 24 * 3600  # still ~60 days between fetch and now


# ==================================================================
# Sync-time content prefetch — the post-response BackgroundTask pre-caches the
# body of recent (<=48h) unread ALL_MAIL mail. TestClient runs BackgroundTasks
# synchronously after the response, so the effect is observable in the same call.
# ==================================================================


def test_sync_metadata_prefetches_recent_unread_inbox_content(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A recent unread ALL_MAIL message whose body is not yet cached gets its
    content prefetched during sync → a later GET /content is a pure cache hit."""
    mid, aid = setup_mailbox_and_account(test_client)

    # Seed an eligible target BEFORE the sync so the prefetch selects it.
    with isolated_db.cursor() as cur:
        _seed_metadata(cur, aid, "recent-unread-1", received_at="now()")

    # The unified provider read returns a body for the prefetch to persist.
    _patch_content_manager(monkeypatch, html_body="<p>prefetched body</p>", text_body="prefetched body")

    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # The body must already be in email_content (prefetched off the response).
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _fetch_content_row(cur, aid, "recent-unread-1")
    assert row is not None
    assert row["html_body"] == "<p>prefetched body</p>"


def test_sync_metadata_prefetch_respects_window_box_and_read_flags(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Only unread + recent(<=48h) + ALL_MAIL + not-yet-cached messages are
    prefetched. Read mail, >48h mail, and SENT/SPAM mail are all skipped."""
    mid, aid = setup_mailbox_and_account(test_client)

    with isolated_db.cursor() as cur:
        _seed_metadata(cur, aid, "ok-recent-unread", received_at="now()")
        _seed_metadata(cur, aid, "skip-read", received_at="now()", is_read=True)
        _seed_metadata(
            cur, aid, "skip-old",
            received_at="now() - INTERVAL '72 hours'",
        )
        _seed_metadata(cur, aid, "skip-sent", received_at="now()", box="SENT")
        _seed_metadata(cur, aid, "skip-spam", received_at="now()", box="SPAM")

    _patch_content_manager(monkeypatch, html_body="<p>body</p>", text_body="body")

    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id FROM email_content WHERE account_id = %s::uuid",
            (aid,),
        )
        cached = {r[0] for r in cur.fetchall()}
    # Only the eligible message was prefetched.
    assert "ok-recent-unread" in cached
    assert cached.isdisjoint({"skip-read", "skip-old", "skip-sent", "skip-spam"})


def test_sync_metadata_prefetch_skips_already_cached_message(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A recent unread inbox message whose body is ALREADY cached is excluded
    from the prefetch (served from cache, never re-fetched). The pre-existing
    cached body is left untouched even though the provider would return a
    different one."""
    mid, aid = setup_mailbox_and_account(test_client)

    with isolated_db.cursor() as cur:
        _seed_metadata(cur, aid, "already-cached", received_at="now()")
        cur.execute(
            """
            INSERT INTO email_content (provider_message_id, account_id, html_body, text_body)
            VALUES ('already-cached', %(aid)s::uuid, %(html)s, %(txt)s)
            """,
            {"aid": aid, "html": "<p>original cached</p>", "txt": "original"},
        )

    # If the prefetch wrongly re-fetched, it would overwrite with this body.
    _patch_content_manager(monkeypatch, html_body="<p>SHOULD NOT APPEAR</p>", text_body="nope")

    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _fetch_content_row(cur, aid, "already-cached")
    assert row["html_body"] == "<p>original cached</p>"


def test_sync_metadata_prefetch_best_effort_does_not_fail_sync(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A provider failure during the prefetch must NOT affect the sync response
    (200) — the prefetch is best-effort and runs off the response path. The
    eligible target is seeded recent so the prefetch branch genuinely executes
    (otherwise the 200 would be a false green that never exercised it)."""
    mid, aid = setup_mailbox_and_account(test_client)

    with isolated_db.cursor() as cur:
        _seed_metadata(cur, aid, "recent-but-fetch-fails", received_at="now()")

    # The unified read raises → the prefetch swallows it (per message).
    _patch_content_manager(
        monkeypatch,
        fetch_content_exc=EmailExternalAPIError("prefetch provider down"),
    )

    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    # Sync still succeeds despite the prefetch failure.
    assert resp.status_code == 200

    # No content row was persisted (the fetch failed before persist).
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _fetch_content_row(cur, aid, "recent-but-fetch-fails")
    assert row is None


# ==================================================================
# Sync-time purge — expired cached bodies (idle 30+ days) of the SYNCED
# accounts are evicted; a row of a non-synced account is untouched (per-account).
# ==================================================================


def test_sync_metadata_purges_expired_content_for_synced_account_only(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """The post-sync purge deletes ``email_content`` rows idle 30+ days for the
    synced account, but leaves a different (non-synced) account's expired row
    intact — the eviction is scoped per account, not global."""
    synced_mid, synced_aid = setup_mailbox_and_account(test_client)
    other_mid, other_aid = setup_mailbox_and_account(test_client)

    # Seed an EXPIRED cached body for each account (metadata row first — FK).
    with isolated_db.cursor() as cur:
        for acc in (synced_aid, other_aid):
            _seed_metadata(cur, acc, "expired-1", received_at="now()")
            cur.execute(
                """
                INSERT INTO email_content (
                    provider_message_id, account_id, html_body, text_body,
                    fetched_at, last_accessed_at
                )
                VALUES ('expired-1', %(aid)s::uuid, '<p>old</p>', 'old',
                        now() - INTERVAL '40 days', now() - INTERVAL '40 days')
                """,
                {"aid": acc},
            )

    # Avoid re-prefetching the just-purged row in the same sync: mark it read so
    # it is not an eligible prefetch target (keeps the assertion deterministic).
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_read = TRUE WHERE provider_message_id = 'expired-1'",
        )

    # Sync ONLY the first mailbox → only synced_aid is purged.
    resp = test_client.post(f"{_MAILBOX_URL}/{synced_mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        synced_row = _fetch_content_row(cur, synced_aid, "expired-1")
        other_row = _fetch_content_row(cur, other_aid, "expired-1")
    # The synced account's stale body was evicted; the other account's survives.
    assert synced_row is None
    assert other_row is not None
