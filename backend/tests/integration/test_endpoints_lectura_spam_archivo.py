"""Tests de integración — estado de lectura, spam y archivo de correos."""

from __future__ import annotations

from core.email.email_client import SpamMoveResult

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.integration.test_endpoints_sincronizacion import _setup_configurable
from tests.shared.email_fakes import build_metadata


# ==================================================================
# Read status
# ==================================================================

def test_update_read_status(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    # Sync metadata first so messages exist in DB
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": aid, "provider_message_id": "m1"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "updated_count" in data
    assert isinstance(data["updated_count"], int)
    assert data["updated_count"] >= 1
    assert "accounts" in data
    assert isinstance(data["accounts"], list)
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == aid
    assert isinstance(data["accounts"][0]["updated"], int)


def test_update_read_status_persists_to_db(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # All three messages start with is_read=False; mark m1 as read
    test_client.patch(
        f"{_MAILBOX_URL}/{mid}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": aid, "provider_message_id": "m1"}],
        },
    )

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT is_read FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is True


def test_update_read_status_preserves_box(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Record the box value before the read-status update
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        box_before = cur.fetchone()[0]

    test_client.patch(
        f"{_MAILBOX_URL}/{mid}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": aid, "provider_message_id": "m1"}],
        },
    )

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        box_after = cur.fetchone()[0]
    assert box_after == box_before


def test_update_read_status_nonexistent_account_404(test_client, setup_mailbox_and_account):
    mid, _ = setup_mailbox_and_account(test_client)
    fake_account_id = "00000000-0000-4000-a000-000000000099"
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": fake_account_id, "provider_message_id": "m1"}],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_update_read_status_nonexistent_mailbox_404(test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    fake_account_id = "00000000-0000-4000-a000-000000000098"
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{fake_mailbox_id}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": fake_account_id, "provider_message_id": "m1"}],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


# ===== Emails -- spam =====


def test_move_to_spam(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["moved_count"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == aid


def test_move_to_spam_persists_box_to_db(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "SPAM"


def test_restore_from_spam(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # First move to spam
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    # Then restore
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/restore-from-spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["moved_count"] >= 1


def test_restore_from_spam_persists_box_to_db(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Move to spam first
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    # Restore
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/restore-from-spam",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "ALL_MAIL"


def test_spam_nonexistent_account_404(test_client, setup_mailbox_and_account):
    mid, _ = setup_mailbox_and_account(test_client)
    fake_account_id = "00000000-0000-4000-a000-000000000099"
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/spam",
        json={"items": [{"account_id": fake_account_id, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_spam_nonexistent_mailbox_404(test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    fake_account_id = "00000000-0000-4000-a000-000000000098"
    resp = test_client.post(
        f"{_MAILBOX_URL}/{fake_mailbox_id}/emails/spam",
        json={"items": [{"account_id": fake_account_id, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


def test_restore_from_spam_nonexistent_account_404(test_client, setup_mailbox_and_account):
    mid, _ = setup_mailbox_and_account(test_client)
    fake_account_id = "00000000-0000-4000-a000-000000000099"
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/restore-from-spam",
        json={"items": [{"account_id": fake_account_id, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_restore_from_spam_nonexistent_mailbox_404(test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    fake_account_id = "00000000-0000-4000-a000-000000000098"
    resp = test_client.post(
        f"{_MAILBOX_URL}/{fake_mailbox_id}/emails/restore-from-spam",
        json={"items": [{"account_id": fake_account_id, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


# ===== Emails -- archive =====


def test_archive_happy_path(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["moved_count"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == aid


def test_archive_persists_box_to_db(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "ARCHIVE"


def test_archive_multi_account(test_client):
    # A selection can span two accounts of the same mailbox; the router
    # groups by account and the response reports a per-account breakdown.
    mid = test_client.post(_MAILBOX_URL, json={"display_name": "ArchiveMulti"}).json()[
        "mailbox_id"
    ]
    aid1 = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "g1"},
    ).json()["account_id"]
    aid2 = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "outlook", "display_label": "o1"},
    ).json()["account_id"]
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={
            "items": [
                {"account_id": aid1, "provider_message_id": "m1"},
                {"account_id": aid2, "provider_message_id": "m1"},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {a["account_id"] for a in data["accounts"]} == {aid1, aid2}


def test_restore_from_archive_returns_to_all_mail(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Archive first.
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    # Then unarchive.
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/restore-from-archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["moved_count"] >= 1

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "ALL_MAIL"


def test_archived_email_excluded_from_all_mail_and_listed_in_archive(
    test_client, setup_mailbox_and_account,
):
    # The bandeja (box=ALL_MAIL) must no longer surface an archived message,
    # while box=ARCHIVE lists it. This pins the new box's listing contract.
    mid, aid = setup_mailbox_and_account(test_client)
    test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )

    all_mail = test_client.get(
        f"{_MAILBOX_URL}/{mid}/emails", params={"box": "ALL_MAIL"},
    ).json()
    archive = test_client.get(
        f"{_MAILBOX_URL}/{mid}/emails", params={"box": "ARCHIVE"},
    ).json()

    all_ids = {row["provider_message_id"] for row in all_mail["items"]}
    archive_ids = {row["provider_message_id"] for row in archive["items"]}
    assert "m1" not in all_ids
    assert "m1" in archive_ids


def test_archive_partial_success(configurable_test_client, isolated_db):
    # Provider-first on the SHARED box-move engine: if the provider archives
    # only 1 of 2 selected messages, the DB persists ARCHIVE for that one only;
    # the other keeps its prior box. Spam reuses this same engine, so one test
    # over the engine covers the provider-first filtering for both surfaces.
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Provider confirms only m1.
    config["move_to_archive_return"] = [SpamMoveResult(old_id="m1", new_id="m1")]

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={
            "items": [
                {"account_id": aid, "provider_message_id": "m1"},
                {"account_id": aid, "provider_message_id": "m2"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["moved_count"] == 1

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id, box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2') "
            "ORDER BY provider_message_id",
            (aid,),
        )
        result = {r[0]: r[1] for r in cur.fetchall()}
    assert result["m1"] == "ARCHIVE"
    assert result["m2"] == "ALL_MAIL"


def test_archive_then_trash_restore_returns_to_archive(configurable_test_client, isolated_db):
    # End-to-end validation of migration 0038's previous_box extension: archiving
    # then moving to trash writes previous_box='ARCHIVE' (the value the pre-0038
    # CHECK rejected); restoring from trash must return the message to ARCHIVE
    # via COALESCE(previous_box, 'ALL_MAIL').
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Archive m1 -> box=ARCHIVE.
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/archive",
        json={"items": [{"account_id": aid, "provider_message_id": "m1"}]},
    )
    assert resp.status_code == 200

    # Move to trash -> box=TRASH, previous_box=ARCHIVE (the row the migration's
    # previous_box CHECK extension was added to allow).
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={"items": [{"provider_message_id": "m1", "account_id": aid}]},
    )
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box, previous_box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row[0] == "TRASH"
    assert row[1] == "ARCHIVE"

    # Restore from trash -> box returns to ARCHIVE, previous_box cleared.
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": "m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box, previous_box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
    assert row[0] == "ARCHIVE"
    assert row[1] is None
