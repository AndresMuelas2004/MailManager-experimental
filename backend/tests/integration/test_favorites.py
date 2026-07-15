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

from api.services import emails_service
from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_MAILBOX,
    SEEDED_USER_ID as _SEEDED_USER,
    patch_emails_build_manager,
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
    rows = resp.json()["items"]
    assert rows, "seeded data must include ALL_MAIL emails"
    assert all(row["is_favorite"] is False for row in rows)


def test_sync_persists_is_favorite_and_does_not_wipe(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # A synced/backfilled starred message lands with is_favorite=True in the DB
    # (proves the metadata upsert tuple aligns with its is_favorite column), and
    # a subsequent sync of the same still-starred message does NOT wipe it.
    from api.services.services_helpers import persist_email_metadata_batch
    from tests.shared.email_fakes import build_metadata

    _mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")

    persist_email_metadata_batch(
        account_id, [build_metadata(provider_message_id="fav1", is_favorite=True)],
    )
    assert _select_is_favorite(isolated_db, account_id, "fav1") is True

    persist_email_metadata_batch(
        account_id, [build_metadata(provider_message_id="fav1", is_favorite=True)],
    )
    assert _select_is_favorite(isolated_db, account_id, "fav1") is True


def test_incremental_label_update_reflects_out_of_band_star(
    configurable_test_client, isolated_db,
):
    # Gmail now propagates the star through the incremental label-update path, so
    # an out-of-band star of an OLD existing message surfaces on the next sync
    # WITHOUT any /favorites/sync call.
    from core.email import LabelUpdate
    from tests.shared.email_fakes import build_metadata

    client, config = configurable_test_client
    mid = client.post(_MAILBOX_URL, json={"display_name": "FavSyncMB"}).json()["mailbox_id"]
    aid = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "g"},
    ).json()["account_id"]

    # Phase 1: a non-favourite row is synced.
    config["metadata"] = [build_metadata("m1", is_favorite=False)]
    config["is_full_sync"] = False
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200
    assert _select_is_favorite(isolated_db, aid, "m1") is False

    # Phase 2: an incremental label-update stars the existing row.
    config["metadata"] = []
    config["label_updates"] = [LabelUpdate("m1", is_read=True, box="ALL_MAIL", is_favorite=True)]
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200
    assert _select_is_favorite(isolated_db, aid, "m1") is True


def test_incremental_label_update_null_favorite_preserves_stored(
    configurable_test_client, isolated_db,
):
    # An Outlook partial delta with no ``flag`` sends is_favorite=None. The
    # UPDATE_LABELS_BATCH COALESCE must leave the stored favourite untouched.
    from core.email import LabelUpdate
    from tests.shared.email_fakes import build_metadata

    client, config = configurable_test_client
    mid = client.post(_MAILBOX_URL, json={"display_name": "FavCoalesceMB"}).json()["mailbox_id"]
    aid = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "outlook", "display_label": "o"},
    ).json()["account_id"]

    # Phase 1: a FAVOURITE row is synced.
    config["metadata"] = [build_metadata("m1", is_favorite=True)]
    config["is_full_sync"] = False
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200
    assert _select_is_favorite(isolated_db, aid, "m1") is True

    # Phase 2: a partial label-update with is_favorite=None must NOT clear it.
    config["metadata"] = []
    config["label_updates"] = [LabelUpdate("m1", is_read=False, box="ALL_MAIL", is_favorite=None)]
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200
    assert _select_is_favorite(isolated_db, aid, "m1") is True


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
    body = resp.json()
    rows = body["items"]
    ids = [row["provider_message_id"] for row in rows]
    assert "gmail-allmail-001" in ids
    assert "gmail-trash-001" not in ids
    # total counts the favourite/box-filtered set: only the ALL_MAIL
    # favourite matches (the TRASH favourite is excluded by default).
    assert body["total"] == 1


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
    ids = [row["provider_message_id"] for row in resp.json()["items"]]
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
    rows = resp.json()["items"]
    ids = [row["provider_message_id"] for row in rows]
    assert "gmail-sent-001" in ids
    assert "gmail-allmail-001" not in ids
    assert all(row["box"] == "SENT" for row in rows)


def test_listing_favorite_with_in_sent_operator_returns_only_sent_favorites(
    seeded_test_client, isolated_db,
):
    """Favourites view (box=ALL_MAIL anchor) + ``in:sent`` in q: the in:
    override flips the effective box to SENT while the is_favorite filter
    stays, so only SENT favourites surface — never the ALL_MAIL one.

    This is the operator-driven sibling of
    ``test_listing_with_favorite_and_explicit_sent_box_returns_only_sent_favorites``:
    the SENT box is reached through the q operator, not the box param.
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
        # Favourites page always passes ALL_MAIL as the anchor box; the in:
        # override in q is what targets SENT.
        params={"box": "ALL_MAIL", "favorite": "true", "q": "in:sent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = [row["provider_message_id"] for row in body["items"]]
    assert "gmail-sent-001" in ids
    assert "gmail-allmail-001" not in ids
    assert all(row["box"] == "SENT" for row in body["items"])
    # total counts the favourite + SENT set only.
    assert body["total"] == 1


def test_sync_favorites_full_replace_for_account(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Provider returns ['m1','m3']; only those rows end up favourite."""
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

    patch_emails_build_manager(monkeypatch, _build_manager)

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


def test_sync_favorites_outlook_reconciles_drifted_id(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Outlook per-endpoint id drift: ``$filter=flag/flagStatus`` returns a
    different id ('D') than the delta-sync route already stored locally
    ('A') for the SAME physical message (matching received_at/from_email/
    subject). Without reconciliation, the naive full-replacement would wipe
    'A''s favourite (this is the live bug ``test_62_sync_favorites_outlook``
    in the e2e suite guards against). Proves the fix: the sync resolves 'D'
    back onto the stored 'A' via the physical identity and keeps it
    favourite — the drifted id is never persisted as a separate row.
    """
    from datetime import datetime, timezone
    from core.email.email_manager import EmailManager
    from tests.shared.email_fakes import FakeEmailClient, build_favorite_candidate

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "outlook")
    received_at = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, is_favorite)
            VALUES ('A', %s, 't', 'sender@example.com', 'Sender', 'subject',
                    %s, false, 'ALL_MAIL', false)
            """,
            (account_id, received_at),
        )

    candidate = build_favorite_candidate(provider_message_id="D", received_at=received_at)

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            label = f"{acc.get('mailbox_id')}__{acc.get('account_id')}"
            manager.add_client(FakeEmailClient(
                label,
                list_favorite_candidates_return=[candidate],
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync",
        params={"account_id": account_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accounts"][0]["favorites_synced"] == 1

    # The reconciled stable id 'A' survives the full-replacement — the
    # drifted id 'D' was never persisted as a separate row/favourite.
    assert _select_is_favorite(isolated_db, account_id, "A") is True
    assert _select_is_favorite(isolated_db, account_id, "D") is None


def test_set_favorite_race_zero_rows_returns_404(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Provider toggle succeeds but the row vanishes before the local UPDATE.

    ``update_favorite`` reports zero rows → 404 ``email_not_found`` instead of
    a silent 200 (never persist a state the provider/DB disagree on).
    """

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box)
            VALUES ('race-1', %s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL')
            """,
            (account_id,),
        )
    # exists() pre-check passes (row seeded), provider toggle succeeds (default
    # fake), but update_favorite returns falsy → race lost.
    monkeypatch.setattr(
        emails_service.email_metadata_store, "update_favorite",
        lambda _aid, _mid, _fav: False,
    )
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/race-1/favorite",
        json={"favorite": True},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def test_set_favorite_provider_failure_returns_502(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Provider rejects the toggle → 502 ``external_api_error`` (Provider-First).

    The provider failure surfaces as ``EmailExternalAPIError`` and
    ``translate_core_error`` maps it to ``external_api_error`` (502)
    *before* the ``FavoriteUpdateError`` fallback — that fallback only
    fires for internal/DB failures after a provider success. The
    function name keeps ``_returns_502`` because the HTTP status is
    correct; only the ``code`` differs from the historical docstring.
    """
    from core.email.email_manager import EmailManager
    from core.email.errors import EmailExternalAPIError
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box)
            VALUES ('prov-1', %s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL')
            """,
            (account_id,),
        )

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            label = f"{acc.get('mailbox_id')}__{acc.get('account_id')}"
            manager.add_client(FakeEmailClient(
                label,
                set_favorite_exc=EmailExternalAPIError("provider down"),
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/prov-1/favorite",
        json={"favorite": True},
    )
    # The provider error maps to 502; the specific external_api_error mapping
    # in translate_core_error wins over the FavoriteUpdateError fallback.
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "external_api_error"
    # Provider-First: the local flag must NOT have been flipped.
    assert _select_is_favorite(isolated_db, account_id, "prov-1") is False


def test_sync_favorites_multi_account_aggregates_total(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """``total_synced`` aggregates the rowcount across every account in the mailbox."""
    from core.email.email_manager import EmailManager
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id_1 = setup_mailbox_and_account(test_client, "gmail")
    acc2 = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts",
        json={"provider": "outlook", "display_label": "test-outlook-2"},
    )
    account_id_2 = acc2.json()["account_id"]

    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, is_favorite)
            VALUES
                ('a1', %(a1)s, 't', 'x@y.com', 'X', 's', now(), false, 'ALL_MAIL', false),
                ('a2', %(a1)s, 't', 'x@y.com', 'X', 's', now(), false, 'ALL_MAIL', false),
                ('b1', %(a2)s, 't', 'x@y.com', 'X', 's', now(), false, 'ALL_MAIL', false)
            """,
            {"a1": account_id_1, "a2": account_id_2},
        )

    returns = {account_id_1: ["a1"], account_id_2: ["b1"]}

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            aid = str(acc.get("account_id") or "")
            label = f"{acc.get('mailbox_id')}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                list_favorite_ids_return=returns.get(aid, []),
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.post(f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # rowcount across both accounts: 2 (acc1) + 1 (acc2) = 3.
    assert body["total_synced"] == 3
    assert len(body["accounts"]) == 2
    assert _select_is_favorite(isolated_db, account_id_1, "a1") is True
    assert _select_is_favorite(isolated_db, account_id_2, "b1") is True


def test_sync_favorites_unknown_account_returns_404(
    test_client, setup_mailbox_and_account,
):
    """Syncing an account that does not belong to the mailbox → 404."""
    mailbox_id, _account_id = setup_mailbox_and_account(test_client, "gmail")
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync",
        params={"account_id": "cccccccc-cccc-4000-a000-cccccccccccc"},
    )
    assert resp.status_code == 404


def test_sync_favorites_aborts_when_account_list_fails_returns_502(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """One account's ``list_favorite_ids`` raises ``EmailExternalAPIError``.

    The per-account error is accumulated in ``manager._last_errors`` and
    re-raised (non-auth branch) by ``raise_on_silent_auth_errors`` via
    ``translate_core_error`` → the whole sync aborts with
    ``external_api_error`` (502), not the ``favorite_sync_error`` fallback.
    """
    from core.email.email_manager import EmailManager
    from core.email.errors import EmailExternalAPIError
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id_1 = setup_mailbox_and_account(test_client, "gmail")
    acc2 = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts",
        json={"provider": "outlook", "display_label": "test-outlook-fail"},
    )
    account_id_2 = acc2.json()["account_id"]

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            aid = str(acc.get("account_id") or "")
            label = f"{acc.get('mailbox_id')}__{aid}"
            # The second account fails its listing; the first is healthy.
            exc = (
                EmailExternalAPIError("provider list down")
                if aid == account_id_2
                else None
            )
            manager.add_client(FakeEmailClient(
                label,
                list_favorite_ids_exc=exc,
                list_favorite_ids_return=["x1"] if exc is None else None,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.post(f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync")
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "external_api_error"


def test_set_favorite_account_not_connected_returns_409(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Silent auth failure on the toggle → 409 ``account_not_connected``.

    ``authenticate_all_silent`` captures the ``EmailAuthError`` into
    ``_last_errors``; the first ``raise_on_silent_auth_errors`` aggregates
    it into a single ``AccountNotConnected`` (409).
    """
    from core.email.email_manager import EmailManager
    from core.email.errors import EmailAuthError
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box)
            VALUES ('auth-1', %s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL')
            """,
            (account_id,),
        )

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            label = f"{acc.get('mailbox_id')}__{acc.get('account_id')}"
            manager.add_client(FakeEmailClient(
                label,
                auth_silent_exc=EmailAuthError("token revoked"),
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/auth-1/favorite",
        json={"favorite": True},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "account_not_connected"
    # Provider-First: the flag must not have flipped.
    assert _select_is_favorite(isolated_db, account_id, "auth-1") is False


def test_sync_favorites_account_not_connected_returns_409(
    test_client, setup_mailbox_and_account, monkeypatch,
):
    """Silent auth failure during sync → 409 ``account_not_connected``."""
    from core.email.email_manager import EmailManager
    from core.email.errors import EmailAuthError
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            label = f"{acc.get('mailbox_id')}__{acc.get('account_id')}"
            manager.add_client(FakeEmailClient(
                label,
                auth_silent_exc=EmailAuthError("token revoked"),
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync",
        params={"account_id": account_id},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "account_not_connected"


def test_set_favorite_missing_email_does_not_call_provider(
    test_client, setup_mailbox_and_account, monkeypatch,
):
    """The existence pre-check 404s WITHOUT spending a provider round trip.

    The pre-check short-circuits *before* the email manager is ever
    built, so the strongest observable proof is that
    ``build_manager_for_accounts`` is never invoked (no client, hence no
    provider call, can exist).
    """

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    build_calls = {"count": 0}

    def _build_manager(accounts):
        build_calls["count"] += 1
        raise AssertionError("provider manager must not be built on a 404 pre-check")

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/ghost-row/favorite",
        json={"favorite": True},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"
    # The provider path was never reached (pre-check short-circuit).
    assert build_calls["count"] == 0


def test_sync_favorites_ignores_unknown_provider_id_no_new_row(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Option A: a provider favourite id absent from ``email_metadata`` is

    silently skipped — the sync never imports a new metadata row.
    """
    from core.email.email_manager import EmailManager
    from tests.shared.email_fakes import FakeEmailClient

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, is_favorite)
            VALUES ('local-1', %s, 't', 'a@b.com', 'A', 's', now(), false, 'ALL_MAIL', false)
            """,
            (account_id,),
        )

    def _count_rows() -> int:
        with isolated_db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM email_metadata WHERE account_id = %s",
                (account_id,),
            )
            return int(cur.fetchone()[0])

    rows_before = _count_rows()

    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            label = f"{acc.get('mailbox_id')}__{acc.get('account_id')}"
            manager.add_client(FakeEmailClient(
                label,
                # 'local-1' exists locally; 'remote-only' does NOT.
                list_favorite_ids_return=["local-1", "remote-only"],
                auth_return={"access_token": "tok", "refresh_token": "ref"},
            ))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/favorites/sync",
        params={"account_id": account_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Provider reported 2 favourites, but only 1 row exists locally.
    assert body["accounts"][0]["favorites_synced"] == 2
    # No new row was created for the unknown id.
    assert _count_rows() == rows_before
    assert _select_is_favorite(isolated_db, account_id, "local-1") is True
    # The unknown id never materialised.
    assert _select_is_favorite(isolated_db, account_id, "remote-only") is None


def test_listing_with_favorite_and_explicit_spam_box_returns_spam_favorites(
    seeded_test_client, isolated_db,
):
    """``box=SPAM&favorite=true`` honours SPAM literally (sibling of TRASH/SENT)."""
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_favorite = TRUE "
            "WHERE account_id = %s AND provider_message_id = 'gmail-spam-001'",
            (_SEEDED_ACCOUNT,),
        )
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_MAILBOX}/emails",
        params={"box": "SPAM", "favorite": "true"},
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    ids = [row["provider_message_id"] for row in rows]
    assert "gmail-spam-001" in ids
    assert all(row["box"] == "SPAM" for row in rows)
