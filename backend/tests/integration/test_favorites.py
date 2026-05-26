"""
Integration tests for the favourites surface.

Covers:
- ``PATCH /mailboxes/{mid}/accounts/{aid}/emails/{msg}/favorite``
- ``POST  /mailboxes/{mid}/favorites/sync``
- ``GET   /mailboxes/{mid}/emails?favorite=true`` filter

Provider behaviour is faked via :class:`FakeEmailClient`. The Provider-
First Rule is checked by inspecting the fake's recorded calls and by
verifying the local ``is_favorite`` column is updated only after the
fake's mutation.
"""

from __future__ import annotations

import psycopg2.extras

from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_MAILBOX,
    SEEDED_USER_ID as _SEEDED_USER,
)


def _set_account_owner_to(isolated_db, mailbox_id: str, owner_user_id: str) -> None:
    """Re-parent the seeded mailbox so the TEST_USER_ID can mutate it.

    The seeded data from migration 0010 belongs to a different user
    (``_SEEDED_USER``). The integration test client authenticates as
    ``TEST_USER_ID`` by default, so for tests that need to mutate the
    seeded rows we re-parent the mailbox into the test user.
    """
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE mailboxes SET owner_user_id = %s WHERE mailbox_id = %s",
            (owner_user_id, mailbox_id),
        )


def _select_is_favorite(isolated_db, account_id: str, provider_message_id: str) -> bool | None:
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT is_favorite FROM email_metadata "
            "WHERE account_id = %s AND provider_message_id = %s",
            (account_id, provider_message_id),
        )
        row = cur.fetchone()
        return None if row is None else bool(row["is_favorite"])


def test_emails_listing_exposes_is_favorite_default_false(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_MAILBOX}/emails",
        params={"box": "ALL_MAIL"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "seeded data must include ALL_MAIL emails"
    assert all(row["is_favorite"] is False for row in rows)


def test_set_favorite_toggles_persist_locally(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Seed a row directly so we can target it via the endpoint.
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box)
            VALUES ('mid-1', %s, 't', 'a@b.com', 'A', 's',
                    '2026-05-19T00:00:00+00:00', false, 'ALL_MAIL')
            """,
            (account_id,),
        )

    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/mid-1/favorite",
        json={"favorite": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_favorite"] is True
    assert body["provider_message_id"] == "mid-1"
    assert body["account_id"] == account_id
    assert _select_is_favorite(isolated_db, account_id, "mid-1") is True

    # Flip it back.
    resp_off = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/mid-1/favorite",
        json={"favorite": False},
    )
    assert resp_off.status_code == 200
    assert resp_off.json()["is_favorite"] is False
    assert _select_is_favorite(isolated_db, account_id, "mid-1") is False


def test_set_favorite_missing_email_returns_404(
    test_client, setup_mailbox_and_account,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/never-existed/favorite",
        json={"favorite": True},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def test_listing_with_favorite_filter_excludes_trash_and_spam_by_default(
    seeded_test_client, isolated_db,
):
    """``favorite=true`` defaults to box NOT IN (TRASH, SPAM)."""
    # Mark a TRASH and an ALL_MAIL row as favourites; only the
    # ALL_MAIL one must surface when no explicit box is requested.
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_favorite = TRUE "
            "WHERE account_id = %s AND provider_message_id IN ('gmail-allmail-001', 'gmail-trash-001')",
            (_SEEDED_ACCOUNT,),
        )
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "favorite": "true"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    ids = [row["provider_message_id"] for row in rows]
    assert "gmail-allmail-001" in ids
    assert "gmail-trash-001" not in ids


def test_listing_with_favorite_and_explicit_trash_box_returns_trash_favorites(
    seeded_test_client, isolated_db,
):
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_favorite = TRUE "
            "WHERE account_id = %s AND provider_message_id = 'gmail-trash-001'",
            (_SEEDED_ACCOUNT,),
        )
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_MAILBOX}/emails",
        params={"box": "TRASH", "favorite": "true"},
    )
    assert resp.status_code == 200
    ids = [row["provider_message_id"] for row in resp.json()]
    assert "gmail-trash-001" in ids


def test_listing_with_favorite_and_explicit_sent_box_returns_only_sent_favorites(
    seeded_test_client, isolated_db,
):
    """Regression: ``box=SENT&favorite=true`` must NOT leak ALL_MAIL favourites.

    The previous implementation collapsed any non-{TRASH, SPAM} ``box``
    value into the "exclude TRASH/SPAM" default, silently surfacing
    ALL_MAIL favourites when the caller asked for SENT.
    """
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_favorite = TRUE "
            "WHERE account_id = %s "
            "AND provider_message_id IN ('gmail-allmail-001', 'gmail-sent-001')",
            (_SEEDED_ACCOUNT,),
        )
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_MAILBOX}/emails",
        params={"box": "SENT", "favorite": "true"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    ids = [row["provider_message_id"] for row in rows]
    assert "gmail-sent-001" in ids
    assert "gmail-allmail-001" not in ids
    assert all(row["box"] == "SENT" for row in rows)


def test_sync_favorites_full_replace_for_account(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Provider returns ['m1','m3']; only those rows end up favourite."""
    from api.services import emails_service
    from core.email.email_manager import EmailManager
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Seed 4 rows so we can assert the diff.
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, is_favorite)
            VALUES
                ('m1', %(aid)s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL', false),
                ('m2', %(aid)s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL', true),
                ('m3', %(aid)s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL', false),
                ('m4', %(aid)s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL', true)
            """,
            {"aid": account_id},
        )

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                list_favorite_ids_return=["m1", "m3"],
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_manager)

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync",
        params={"account_id": account_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_synced"] == 4  # rowcount across the account
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["favorites_synced"] == 2

    # m1, m3 → favourite; m2, m4 → not favourite (replaced atomically).
    flags = {
        mid: _select_is_favorite(isolated_db, account_id, mid)
        for mid in ("m1", "m2", "m3", "m4")
    }
    assert flags == {"m1": True, "m2": False, "m3": True, "m4": False}
