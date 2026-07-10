"""Integration tests for the background initial mass backfill.

Covers migration 0041 (table + index + FK type/cascade), the status endpoint,
the sync guard (active accounts excluded, completed accounts skip ghost
reconciliation), and the enqueue-on-connect wiring driven end-to-end through
the real OAuth callback. Provider APIs are faked; the DB is real (per-test
rollback).
"""

from __future__ import annotations

import psycopg2.extras
import pytest

from api.routers.routers_helpers import require_session
from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
)


def _insert_backfill_job(
    isolated_db,
    *,
    account_id: str,
    mailbox_id: str,
    status: str = "pending",
    provider: str = "gmail",
    target_total: int = 100000,
    fetched_count: int = 0,
) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO account_backfill_jobs
                (account_id, mailbox_id, provider, status, target_total, fetched_count)
            VALUES (%(account_id)s::uuid, %(mailbox_id)s::uuid, %(provider)s,
                    %(status)s, %(target_total)s, %(fetched_count)s)
            """,
            {
                "account_id": account_id,
                "mailbox_id": mailbox_id,
                "provider": provider,
                "status": status,
                "target_total": target_total,
                "fetched_count": fetched_count,
            },
        )


def _job_row(isolated_db, account_id: str):
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM account_backfill_jobs WHERE account_id = %s::uuid",
            (account_id,),
        )
        return cur.fetchone()


def _email_count(isolated_db, account_id: str) -> int:
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_metadata WHERE account_id = %s::uuid",
            (account_id,),
        )
        return cur.fetchone()[0]


# ==================================================================
# Migration 0041 — table, index, FK type + cascade
# ==================================================================


def test_migration_0041_creates_table_and_partial_index(test_client, isolated_db):
    with isolated_db.cursor() as cur:
        cur.execute("SELECT to_regclass('public.account_backfill_jobs')")
        assert cur.fetchone()[0] is not None
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'account_backfill_jobs'",
        )
        indexes = {row[0] for row in cur.fetchall()}
    assert "idx_backfill_jobs_active" in indexes


def test_account_id_column_is_uuid(test_client, isolated_db):
    # Load-bearing: the FK to accounts(account_id) UUID requires the SAME type —
    # a TEXT column would fail to create the FK (regression guard).
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'account_backfill_jobs' AND column_name = 'account_id'",
        )
        assert cur.fetchone()[0] == "uuid"


def test_fk_cascade_deletes_job_when_account_deleted(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_backfill_job(isolated_db, account_id=aid, mailbox_id=mid)
    assert _job_row(isolated_db, aid) is not None

    with isolated_db.cursor() as cur:
        cur.execute("DELETE FROM accounts WHERE account_id = %s::uuid", (aid,))

    # ON DELETE CASCADE removed the job alongside the account.
    assert _job_row(isolated_db, aid) is None


# ==================================================================
# GET /mailboxes/{mailbox_id}/backfill-status
# ==================================================================


def test_status_returns_seeded_job(test_client, setup_mailbox_and_account, isolated_db):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_backfill_job(
        isolated_db, account_id=aid, mailbox_id=mid,
        status="running", fetched_count=1234, target_total=100000,
    )

    resp = test_client.get(f"{_MAILBOX_URL}/{mid}/backfill-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    assert len(data["accounts"]) == 1
    entry = data["accounts"][0]
    assert entry["account_id"] == aid
    assert entry["status"] == "running"
    assert entry["fetched_count"] == 1234
    assert entry["target_total"] == 100000
    assert entry["done"] is False


def test_status_omits_accounts_without_job(test_client, setup_mailbox_and_account):
    mid, _aid = setup_mailbox_and_account(test_client)
    resp = test_client.get(f"{_MAILBOX_URL}/{mid}/backfill-status")
    assert resp.status_code == 200
    # An account with no backfill job is simply absent.
    assert resp.json() == {"accounts": [], "active": False}


def test_status_completed_job_is_done_and_inactive(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_backfill_job(isolated_db, account_id=aid, mailbox_id=mid, status="completed")

    data = test_client.get(f"{_MAILBOX_URL}/{mid}/backfill-status").json()
    assert data["active"] is False
    assert data["accounts"][0]["done"] is True


def test_status_foreign_mailbox_returns_403(test_client):
    # The seeded Gmail mailbox is owned by SEEDED_USER, not TEST_USER — a foreign
    # (but existing) mailbox is 403 forbidden, NOT 404.
    resp = test_client.get(f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/backfill-status")
    assert resp.status_code == 403


def test_status_missing_mailbox_returns_404(test_client):
    resp = test_client.get(
        f"{_MAILBOX_URL}/00000000-0000-4000-a000-0000000000ff/backfill-status",
    )
    assert resp.status_code == 404


def test_status_without_session_returns_401(test_client_base, isolated_db, app):
    override = app.dependency_overrides.pop(require_session, None)
    try:
        resp = test_client_base.get(
            f"{_MAILBOX_URL}/00000000-0000-4000-a000-0000000000aa/backfill-status",
        )
        assert resp.status_code == 401
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override


# ==================================================================
# POST /sync-metadata — backfill guard
# ==================================================================


def test_sync_excludes_account_under_active_backfill(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_backfill_job(isolated_db, account_id=aid, mailbox_id=mid, status="running")

    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata?account_id={aid}")
    assert resp.status_code == 200
    data = resp.json()
    # The backfill owns the initial load: neutral 200, nothing synced, and the
    # 500-message bootstrap never ran (no rows persisted for the account).
    assert data["total_synced"] == 0
    assert data["accounts"] == []
    assert _email_count(isolated_db, aid) == 0


def test_completed_backfill_skips_ghost_reconciliation(
    configurable_test_client, isolated_db,
):
    client, config = configurable_test_client
    mb = client.post(_MAILBOX_URL, json={"display_name": "Backfill MB"}).json()
    mid = mb["mailbox_id"]
    acc = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "cfg-gmail"},
    ).json()
    aid = acc["account_id"]

    from tests.shared.email_fakes import build_metadata

    # Phase 1: incremental sync lands 3 rows.
    config["metadata"] = [build_metadata("m1"), build_metadata("m2"), build_metadata("m3")]
    config["is_full_sync"] = False
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200
    assert _email_count(isolated_db, aid) == 3

    # The account has a COMPLETED backfill.
    _insert_backfill_job(isolated_db, account_id=aid, mailbox_id=mid, status="completed")

    # Phase 2: a full sync (cursor expired) returning only m1. Without gating,
    # m2/m3 would be treated as ghosts and mass-deleted; the completed-backfill
    # gate skips reconciliation and preserves the history.
    config["metadata"] = [build_metadata("m1")]
    config["is_full_sync"] = True
    config["existing_message_ids"] = ["m1"]
    assert client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata").status_code == 200

    assert _email_count(isolated_db, aid) == 3


# ==================================================================
# Enqueue-on-connect (driven through the real OAuth callback)
# ==================================================================


def _run_connect_callback(client, mid, aid):
    resp = client.post(f"{_MAILBOX_URL}/{mid}/accounts/{aid}/connect")
    assert resp.status_code == 200
    state = resp.json()["state"]
    callback = client.get("/auth/google/callback", params={"state": state, "code": "auth-code"})
    assert callback.status_code == 200
    assert "Account connected" in callback.text


def test_connect_enqueues_pending_job_for_new_account(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
    monkeypatch.setenv("BACKFILL_MAX_EMAILS_PER_ACCOUNT", "100000")
    mid, aid = setup_mailbox_and_account(test_client)

    _run_connect_callback(test_client, mid, aid)

    row = _job_row(isolated_db, aid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["target_total"] == 100000
    assert row["provider"] == "gmail"


def test_reconnect_does_not_enqueue(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
    mid, aid = setup_mailbox_and_account(test_client)
    # A previously-synced account (sync_cursor set) is a reconnection.
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE accounts SET sync_cursor = %s WHERE account_id = %s::uuid",
            ("hist-123", aid),
        )

    _run_connect_callback(test_client, mid, aid)

    assert _job_row(isolated_db, aid) is None


def test_connect_with_worker_disabled_does_not_enqueue(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # Worker off → no job enqueued; the account falls through to the classic
    # synchronous bootstrap on its first sync-metadata (§4.10).
    monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "false")
    mid, aid = setup_mailbox_and_account(test_client)

    _run_connect_callback(test_client, mid, aid)

    assert _job_row(isolated_db, aid) is None


def test_enqueue_revives_failed_job_but_not_running(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # The ON CONFLICT ... WHERE status='failed' revives only a failed job; a
    # running/pending/completed one is left untouched (idempotent reconnect).
    from database import account_backfill_store

    monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
    mid, aid = setup_mailbox_and_account(test_client)

    _insert_backfill_job(isolated_db, account_id=aid, mailbox_id=mid, status="failed")
    account_backfill_store.enqueue(aid, mid, "gmail", 100000)
    assert _job_row(isolated_db, aid)["status"] == "pending"  # revived

    # A running job is a no-op on re-enqueue.
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE account_backfill_jobs SET status = 'running' WHERE account_id = %s::uuid",
            (aid,),
        )
    account_backfill_store.enqueue(aid, mid, "gmail", 100000)
    assert _job_row(isolated_db, aid)["status"] == "running"  # untouched
