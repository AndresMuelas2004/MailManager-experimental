"""Tests de integración — sincronización de metadatos y reconciliación del pipeline."""

from __future__ import annotations

from core.email.email_client import LabelUpdate

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.shared.email_fakes import build_metadata


# ------------------------------------------------------------------
# Emails — sync-metadata
# ------------------------------------------------------------------

def test_sync_email_metadata(test_client, setup_mailbox_and_account, sample_metadata):
    mid, _ = setup_mailbox_and_account(test_client)
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200
    data = resp.json()
    expected_count = len(sample_metadata)
    assert isinstance(data["total_synced"], int)
    assert data["total_synced"] == expected_count
    assert isinstance(data["accounts"], list)
    assert len(data["accounts"]) == 1
    detail = data["accounts"][0]
    assert "account_id" in detail
    assert "provider" in detail
    assert isinstance(detail["emails_synced"], int)
    assert detail["emails_synced"] == expected_count
    assert data["total_synced"] == detail["emails_synced"]
    # Full success carries an empty ``failed_accounts`` (added by Option A).
    assert data["failed_accounts"] == []


def test_sync_email_metadata_persists_to_db(test_client, setup_mailbox_and_account, isolated_db):
    mid, _ = setup_mailbox_and_account(test_client)
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200
    total_synced = resp.json()["total_synced"]
    assert total_synced > 0

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_metadata WHERE account_id IN "
            "(SELECT account_id FROM accounts WHERE mailbox_id = %s::uuid)",
            (mid,),
        )
        row_count = cur.fetchone()[0]
    assert row_count == total_synced


def test_sync_email_metadata_single_account(test_client, setup_mailbox_and_account, sample_metadata):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata?account_id={aid}")
    assert resp.status_code == 200
    data = resp.json()
    expected_count = len(sample_metadata)
    assert data["total_synced"] == expected_count
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == aid


# ==================================================================
# Sync pipeline: advanced scenarios
# ==================================================================


def _setup_configurable(client_and_config, provider="gmail"):
    """Create mailbox + account using the configurable_test_client."""
    client, config = client_and_config
    mb = client.post(_MAILBOX_URL, json={"display_name": "Cfg MB"})
    mailbox_id = mb.json()["mailbox_id"]
    acc = client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts",
        json={"provider": provider, "display_label": f"cfg-{provider}"},
    )
    account_id = acc.json()["account_id"]
    return client, config, mailbox_id, account_id


def test_sync_reconciliation_deletes_ghosts_from_db(
    configurable_test_client, isolated_db,
):
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: incremental sync with 3 messages
    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    m3 = build_metadata(provider_message_id="m3")
    config["metadata"] = [m1, m2, m3]
    config["is_full_sync"] = False
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200
    assert resp.json()["total_synced"] == 3

    # Phase 2: full sync that only returns m1; m2 and m3 are ghosts
    config["metadata"] = [m1]
    config["is_full_sync"] = True
    config["existing_message_ids"] = ["m1"]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_metadata WHERE account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == 1


def test_sync_deletes_removes_messages_from_db(
    configurable_test_client, isolated_db,
):
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: seed 3 messages
    m1 = build_metadata(provider_message_id="m1")
    m2 = build_metadata(provider_message_id="m2")
    m3 = build_metadata(provider_message_id="m3")
    config["metadata"] = [m1, m2, m3]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Phase 2: delete m1 via SyncResult.deletes
    config["metadata"] = []
    config["deletes"] = ["m1"]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_metadata WHERE account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT provider_message_id FROM email_metadata WHERE account_id = %s::uuid",
            (aid,),
        )
        remaining = {row[0] for row in cur.fetchall()}
        assert "m1" not in remaining


def test_sync_label_updates_modifies_db_records(
    configurable_test_client, isolated_db,
):
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: insert m1 with is_read=False, box=ALL_MAIL
    m1 = build_metadata(provider_message_id="m1", is_read=False, box="ALL_MAIL")
    config["metadata"] = [m1]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    # Phase 2: update m1 labels
    config["metadata"] = []
    config["label_updates"] = [LabelUpdate("m1", is_read=True, box="SENT")]
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT is_read, box FROM email_metadata "
            "WHERE provider_message_id = 'm1' AND account_id = %s::uuid",
            (aid,),
        )
        row = cur.fetchone()
        assert row[0] is True
        assert row[1] == "SENT"


def test_sync_persists_and_updates_cursor(
    configurable_test_client, isolated_db,
):
    client, config, mid, aid = _setup_configurable(configurable_test_client)

    # Phase 1: sync with cursor_v1
    config["sync_cursor_return"] = "cursor_v1"
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT sync_cursor FROM accounts WHERE account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == "cursor_v1"

    # Phase 2: sync with cursor_v2
    config["metadata"] = []
    config["sync_cursor_return"] = "cursor_v2"
    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT sync_cursor FROM accounts WHERE account_id = %s::uuid",
            (aid,),
        )
        assert cur.fetchone()[0] == "cursor_v2"
