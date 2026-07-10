"""Tests de integración — envío de correos, validaciones y escenarios multi-cuenta."""

from __future__ import annotations

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL


def test_send_email(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid,
            "subject": "Hello",
            "body": "World",
            "recipients": ["dest@example.com"],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}


def test_send_email_persists_metadata(test_client, setup_mailbox_and_account, isolated_db):
    """After send, the sent email metadata is persisted in the database."""
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid,
            "subject": "Persisted Send",
            "body": "Body",
            "recipients": ["dest@example.com"],
        },
    )
    assert resp.status_code == 200
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_metadata "
            "WHERE account_id = %s::uuid AND box = 'SENT'",
            (aid,),
        )
        count = cur.fetchone()[0]
    assert count >= 1


def test_send_email_with_html_body_accepted(test_client, setup_mailbox_and_account):
    """The send endpoint accepts a rich-text HTML body (sanitised server-side)."""
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid,
            "subject": "HTML Send",
            "body": "<p>Hello <strong>world</strong></p>",
            "recipients": ["dest@example.com"],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}


def test_send_email_empty_body_returns_422(test_client, setup_mailbox_and_account):
    """``EmailSendRequest.body`` carries ``min_length=1`` — an empty body 422s."""
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid,
            "subject": "Empty",
            "body": "",
            "recipients": ["dest@example.com"],
        },
    )
    assert resp.status_code == 422


def test_send_email_body_over_cap_returns_422(test_client, setup_mailbox_and_account):
    """A body over the 1,000,000-char ``max_length`` collapses to a 422."""
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid,
            "subject": "Big",
            "body": "x" * 1_000_001,
            "recipients": ["dest@example.com"],
        },
    )
    assert resp.status_code == 422


# ==================================================================
# Multi-account scenarios
# ==================================================================

def _setup_mailbox_with_two_accounts(client) -> tuple[str, str, str]:
    """Create a mailbox with a gmail and an outlook account."""
    mid = client.post(_MAILBOX_URL, json={"display_name": "Multi"}).json()["mailbox_id"]
    aid1 = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "gmail-acc"},
    ).json()["account_id"]
    aid2 = client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "outlook", "display_label": "outlook-acc"},
    ).json()["account_id"]
    return mid, aid1, aid2


def test_multi_account_sync_metadata(test_client):
    mid, _, _ = _setup_mailbox_with_two_accounts(test_client)
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["accounts"]) == 2
    # Both accounts healthy → no failures reported.
    assert data["failed_accounts"] == []


def test_multi_account_sync_metadata_partial_failure(partial_failure_test_client):
    # Unified mailbox where ONE of two accounts has a dead token: the healthy
    # account still syncs and the endpoint returns 200 with the disconnected
    # account reported in ``failed_accounts`` (Option A), instead of the old
    # 409 that blocked the whole unified refresh.
    client, config = partial_failure_test_client
    mid, aid1, aid2 = _setup_mailbox_with_two_accounts(client)
    config["failing_account_ids"].add(aid2)

    resp = client.post(f"{_MAILBOX_URL}/{mid}/emails/sync-metadata")
    assert resp.status_code == 200
    data = resp.json()
    # Only the healthy account is reported as synced.
    assert {a["account_id"] for a in data["accounts"]} == {aid1}
    # The disconnected account travels in the 200, categorised as an auth loss.
    assert len(data["failed_accounts"]) == 1
    failure = data["failed_accounts"][0]
    assert failure["account_id"] == aid2
    assert failure["provider"] == "outlook"
    assert failure["reason"] == "account_not_connected"


def test_multi_account_send_targets_specific_account(test_client):
    mid, aid1, _ = _setup_mailbox_with_two_accounts(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={
            "account_id": aid1,
            "subject": "Targeted",
            "body": "Only aid1",
            "recipients": ["r@e.com"],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}


# ==================================================================
# Delete mailbox cascade
# ==================================================================

def test_delete_mailbox_removes_accounts(test_client, isolated_db):
    mid, aid1, aid2 = _setup_mailbox_with_two_accounts(test_client)
    test_client.delete(f"{_MAILBOX_URL}/{mid}")
    assert test_client.get(f"{_MAILBOX_URL}/{mid}/accounts/{aid1}").status_code == 404
    assert test_client.get(f"{_MAILBOX_URL}/{mid}/accounts/{aid2}").status_code == 404

    # 3H: Verify accounts are actually gone at DB level (CASCADE).
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM accounts WHERE mailbox_id = %s::uuid", (mid,),
        )
        assert cur.fetchone()[0] == 0
