"""Tests de integración — papelera: mover a la papelera, borrar y restaurar."""

from __future__ import annotations

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.integration.test_endpoints_sincronizacion import _setup_configurable
from tests.shared.email_fakes import build_metadata


# ==================================================================
# Trash management
# ==================================================================


def test_trash_delete_marks_as_deleted(configurable_test_client, isolated_db):
    """Delete action marks TRASH emails as DELETED in the database."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: sync 2 messages
    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Manually move m1 to TRASH in DB
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )

    # Delete m1 from trash
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "delete",
            "items": [{"provider_message_id": "m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    # Verify m1 is DELETED in DB
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == "DELETED"


def test_trash_restore_with_previous_box(configurable_test_client, isolated_db):
    """Restore action restores email to previous_box value."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: sync a message
    m1 = build_metadata(provider_message_id="m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Manually set to TRASH with previous_box = SENT
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH', previous_box = 'SENT' "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )

    # Restore m1
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": "m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    # Verify m1 is restored to SENT and previous_box is NULL
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box, previous_box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row[0] == "SENT"
        assert row[1] is None


def test_trash_restore_null_previous_box_defaults_to_all_mail(configurable_test_client, isolated_db):
    """When previous_box is NULL and fetch_messages_metadata returns nothing, defaults to ALL_MAIL."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Set to TRASH with no previous_box
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )

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
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == "ALL_MAIL"


def test_trash_restore_null_previous_box_discovers_real_box(configurable_test_client, isolated_db):
    """When previous_box is NULL, fetch_messages_metadata discovers the real box."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    config["metadata"] = [m1]
    # Configure fetch_messages_metadata to return SENT for m1
    config["fetch_messages_metadata_return"] = [
        build_metadata(provider_message_id="m1", box="SENT"),
    ]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Set to TRASH with no previous_box
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )

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
        assert row[0] == "SENT"
        assert row[1] is None


def test_trash_restore_updates_provider_message_id(configurable_test_client, isolated_db):
    """Restore works when the provider_message_id stays the same (default FakeEmailClient path)."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="old_m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'old_m1' AND account_id = %s::uuid",
            (aid,),
        )

    # FakeEmailClient.restore_from_trash returns {k: k} by default,
    # so provider_message_id stays 'old_m1' → 'old_m1'.
    # This test verifies the DB update path works even when ID stays the same.
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": "old_m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id, box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id = 'old_m1'",
            (aid,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[1] == "ALL_MAIL"


def test_trash_delete_partial_success(configurable_test_client, isolated_db):
    """Provider-first: if provider only deletes 1 of 2, DB only marks 1 as DELETED."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Move both to TRASH
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2')",
            (aid,),
        )

    # Provider only succeeds for m1
    config["delete_return"] = ["m1"]

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "delete",
            "items": [
                {"provider_message_id": "m1", "account_id": aid},
                {"provider_message_id": "m2", "account_id": aid},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id, box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2') "
            "ORDER BY provider_message_id",
            (aid,),
        )
        rows = cur.fetchall()
        result = {r[0]: r[1] for r in rows}
        assert result["m1"] == "DELETED"
        assert result["m2"] == "TRASH"


def test_trash_restore_changes_provider_message_id(configurable_test_client, isolated_db):
    """Restore updates the provider_message_id when the provider returns a new ID (Outlook)."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="old_m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'old_m1' AND account_id = %s::uuid",
            (aid,),
        )

    # Provider returns a new ID for the restored message
    config["restore_return"] = {"old_m1": "new_m1"}

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": "old_m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    with isolated_db.cursor() as cur:
        # new_m1 should exist with box = ALL_MAIL
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'new_m1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "ALL_MAIL"
        # old_m1 should no longer exist
        cur.execute(
            "SELECT count(*) FROM email_metadata "
            "WHERE provider_message_id = 'old_m1' AND account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == 0


def test_trash_delete_multi_account(configurable_test_client, isolated_db):
    """Delete across two accounts in a single request."""
    client, config, mid, _ = _setup_configurable(configurable_test_client)

    # Create a second account under the same mailbox
    acc2_resp = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "cfg-gmail-2"},
    )
    aid2 = acc2_resp.json()["account_id"]

    # Get aid1 from the first account
    accounts_resp = client.get(f"{_MAILBOX_URL}/{mid}/accounts")
    all_accounts = accounts_resp.json()
    aid1 = next(a["account_id"] for a in all_accounts if a["display_label"] == "cfg-gmail")

    # Sync m1 for account 1
    config["metadata"] = [build_metadata(provider_message_id="m1")]
    client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Sync m2 for account 2 (same metadata list, different account syncs it)
    config["metadata"] = [build_metadata(provider_message_id="m2")]
    client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    # Move both to TRASH
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm1'",
            (aid1,),
        )
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm2'",
            (aid2,),
        )

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "delete",
            "items": [
                {"provider_message_id": "m1", "account_id": aid1},
                {"provider_message_id": "m2", "account_id": aid2},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 2

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm1'",
            (aid1,),
        )
        assert cur.fetchone()[0] == "DELETED"
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm2'",
            (aid2,),
        )
        assert cur.fetchone()[0] == "DELETED"


# ==================================================================
# Move to trash
# ==================================================================


def test_move_to_trash_happy_path(configurable_test_client, isolated_db):
    """move-to-trash sets box='TRASH' and previous_box in the database."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1", box="ALL_MAIL")
    m2 = build_metadata(provider_message_id="m2", box="SENT")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={
            "items": [
                {"provider_message_id": "m1", "account_id": aid},
                {"provider_message_id": "m2", "account_id": aid},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 2

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box, previous_box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row[0] == "TRASH"
        assert row[1] == "ALL_MAIL"

        cur.execute(
            "SELECT box, previous_box FROM email_metadata "
            "WHERE provider_message_id = 'm2' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row[0] == "TRASH"
        assert row[1] == "SENT"


def test_move_to_trash_multi_account(configurable_test_client, isolated_db):
    """move-to-trash across two accounts in a single request."""
    client, config, mid, _ = _setup_configurable(configurable_test_client)

    acc2_resp = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "cfg-gmail-2"},
    )
    aid2 = acc2_resp.json()["account_id"]

    accounts_resp = client.get(f"{_MAILBOX_URL}/{mid}/accounts")
    all_accounts = accounts_resp.json()
    aid1 = next(a["account_id"] for a in all_accounts if a["display_label"] == "cfg-gmail")

    config["metadata"] = [build_metadata(provider_message_id="m1")]
    client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    config["metadata"] = [build_metadata(provider_message_id="m2")]
    client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={
            "items": [
                {"provider_message_id": "m1", "account_id": aid1},
                {"provider_message_id": "m2", "account_id": aid2},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 2

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm1'",
            (aid1,),
        )
        assert cur.fetchone()[0] == "TRASH"
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id = 'm2'",
            (aid2,),
        )
        assert cur.fetchone()[0] == "TRASH"


def test_move_to_trash_partial_success(configurable_test_client, isolated_db):
    """Provider-first: if provider only trashes 1 of 2, DB only updates 1."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    config["move_to_trash_return"] = {"m1": "m1"}

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={
            "items": [
                {"provider_message_id": "m1", "account_id": aid},
                {"provider_message_id": "m2", "account_id": aid},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id, box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2') "
            "ORDER BY provider_message_id",
            (aid,),
        )
        rows = cur.fetchall()
        result = {r[0]: r[1] for r in rows}
        assert result["m1"] == "TRASH"
        assert result["m2"] == "ALL_MAIL"


def test_move_to_trash_already_in_trash(configurable_test_client, isolated_db):
    """Moving emails already in TRASH should result in affected == 0."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Manually set m1 to TRASH in DB
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )

    # Try to move m1 to trash again — SQL filter excludes TRASH rows
    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={
            "items": [{"provider_message_id": "m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 0


def test_move_to_trash_changes_provider_message_id(configurable_test_client, isolated_db):
    """When the provider returns a new ID (e.g. Outlook), the DB stores the new ID with box=TRASH."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="old_m1")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    config["move_to_trash_return"] = {"old_m1": "new_m1"}

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/move-to-trash",
        json={
            "items": [{"provider_message_id": "old_m1", "account_id": aid}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    with isolated_db.cursor() as cur:
        # new_m1 should exist with box = TRASH
        cur.execute(
            "SELECT box FROM email_metadata "
            "WHERE provider_message_id = 'new_m1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "TRASH"
        # old_m1 should no longer exist
        cur.execute(
            "SELECT count(*) FROM email_metadata "
            "WHERE provider_message_id = 'old_m1' AND account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == 0


def test_trash_restore_partial_success(configurable_test_client, isolated_db):
    """Provider-first: if provider only restores 1 of 2, DB only restores 1."""
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    config["metadata"] = [m1, m2]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Move both to TRASH
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET box = 'TRASH' "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2')",
            (aid,),
        )

    # Provider only restores m1
    config["restore_return"] = {"m1": "m1"}

    resp = client.post(
        f"{_MAILBOX_URL}/{mid}/emails/trash",
        json={
            "action": "restore",
            "items": [
                {"provider_message_id": "m1", "account_id": aid},
                {"provider_message_id": "m2", "account_id": aid},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT provider_message_id, box FROM email_metadata "
            "WHERE account_id = %s::uuid AND provider_message_id IN ('m1', 'm2') "
            "ORDER BY provider_message_id",
            (aid,),
        )
        rows = cur.fetchall()
        result = {r[0]: r[1] for r in rows}
        assert result["m1"] == "ALL_MAIL"
        assert result["m2"] == "TRASH"
