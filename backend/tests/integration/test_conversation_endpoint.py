"""
Integration tests for the conversation viewer endpoint.

``GET /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/conversation`` is a
read + lazy-sync flow (not Provider-First): it reads the base message row
from the DB, fetches the thread from the provider (faked here), best-effort
persists the thread's messages into ``email_metadata``, and returns the
chain mapped from the provider's fresh state, ordered oldest-first. The
provider boundary is faked via :class:`FakeEmailClient`; the DB and the
full router→service→DB→core wiring are real.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.email.email_manager import EmailManager
from core.email.errors import EmailExternalAPIError
from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    patch_emails_build_manager,
)
from tests.shared.email_fakes import FakeEmailClient, build_conversation_message


def _seed_base_row(isolated_db, account_id: str, *, provider_message_id: str,
                   thread_id: str | None, received_at: str = "2026-05-01T09:00:00+00:00",
                   box: str = "ALL_MAIL") -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, to_email, to_name)
            VALUES (%(pmid)s, %(aid)s, %(thread)s, 'sender@x.com', 'Sender',
                    'Base subject', %(received)s, FALSE, %(box)s, 'me@x.com', 'Me')
            """,
            {
                "pmid": provider_message_id, "aid": account_id,
                "thread": thread_id, "received": received_at, "box": box,
            },
        )


def _patch_conversation_manager(monkeypatch, members):
    """Build a manager whose FakeEmailClient returns *members* (or raises)."""
    def _build_manager(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            kwargs = {"auth_return": {"access_token": "tok", "refresh_token": "ref"}}
            if isinstance(members, Exception):
                kwargs["fetch_conversation_exc"] = members
            else:
                kwargs["fetch_conversation_return"] = members
            manager.add_client(FakeEmailClient(label, **kwargs))
        return manager

    patch_emails_build_manager(monkeypatch, _build_manager)


def _conversation_url(mailbox_id: str, account_id: str, provider_message_id: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}"
        f"/emails/{provider_message_id}/conversation"
    )


def test_threadless_base_returns_single_message_conversation(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_base_row(isolated_db, account_id, provider_message_id="solo", thread_id="")
    # Even if the provider would return something, the threadless path must
    # NOT call it — make the fake explode to prove the short-circuit.
    _patch_conversation_manager(monkeypatch, RuntimeError("provider must not be called"))

    resp = test_client.get(_conversation_url(mailbox_id, account_id, "solo"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == ""
    assert len(body["messages"]) == 1
    assert body["messages"][0]["provider_message_id"] == "solo"
    assert body["messages"][0]["mailbox_id"] == mailbox_id


def test_conversation_returns_ascending_chain_and_lazy_syncs_new_messages(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Only the base message is synced locally; the thread carries an extra
    # message the app never saw.
    _seed_base_row(isolated_db, account_id, provider_message_id="base",
                   thread_id="thr-1", received_at="2026-05-01T10:00:00+00:00")
    members = [
        build_conversation_message(
            provider_message_id="newer", thread_id="thr-1",
            received_at=datetime(2026, 5, 1, 11, 0), box="SENT", is_read=True,
        ),
        build_conversation_message(
            provider_message_id="base", thread_id="thr-1",
            received_at=datetime(2026, 5, 1, 10, 0), box="ALL_MAIL", is_read=True,
        ),
    ]
    _patch_conversation_manager(monkeypatch, members)

    resp = test_client.get(_conversation_url(mailbox_id, account_id, "base"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "thr-1"
    # Oldest-first ordering, mapped from the provider members.
    assert [m["provider_message_id"] for m in body["messages"]] == ["base", "newer"]
    # has_attachments is always False in the viewer envelope (B.lazy).
    assert all(m["has_attachments"] is False for m in body["messages"])

    # Side effect (same test, common_mistakes §1): the lazy sync persisted the
    # previously-unseen "newer" message into its box (SENT) — a subsequent
    # listing now surfaces it.
    sent = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "SENT", "account_id": account_id},
    ).json()
    assert "newer" in {r["provider_message_id"] for r in sent["items"]}


def test_conversation_reopen_serves_persisted_chain(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_base_row(isolated_db, account_id, provider_message_id="base",
                   thread_id="thr-2", received_at="2026-05-01T10:00:00+00:00")
    members = [
        build_conversation_message(provider_message_id="base", thread_id="thr-2",
                                   received_at=datetime(2026, 5, 1, 10, 0)),
        build_conversation_message(provider_message_id="extra", thread_id="thr-2",
                                   received_at=datetime(2026, 5, 1, 11, 0)),
    ]
    _patch_conversation_manager(monkeypatch, members)

    first = test_client.get(_conversation_url(mailbox_id, account_id, "base"))
    assert first.status_code == 200
    # Reopening returns the same chain (the second open re-reads the provider
    # members; both messages are present each time).
    second = test_client.get(_conversation_url(mailbox_id, account_id, "base"))
    assert second.status_code == 200
    assert [m["provider_message_id"] for m in second.json()["messages"]] == [
        "base", "extra",
    ]


def test_conversation_response_carries_reconciled_id_for_drifted_member(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # Outlook id drift: the conversation endpoint returns the SAME physical
    # message (identical received_at / from_email / subject) under a different
    # id than the one sync stored. The lazy-sync must UPDATE the stored row
    # (no duplicate), and the response must carry the STORED id — the frontend
    # drives content / favourite / reply-context / box moves through the
    # response ids, so a verbatim drifted id would 404 against the local copy.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "outlook")
    _seed_base_row(isolated_db, account_id, provider_message_id="stable",
                   thread_id="thr-3", received_at="2026-05-01T10:00:00+00:00")
    members = [
        build_conversation_message(
            provider_message_id="drifted", thread_id="thr-3",
            received_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
            from_email="sender@x.com", subject="Base subject",
        ),
    ]
    _patch_conversation_manager(monkeypatch, members)

    resp = test_client.get(_conversation_url(mailbox_id, account_id, "stable"))
    assert resp.status_code == 200, resp.text
    assert [m["provider_message_id"] for m in resp.json()["messages"]] == ["stable"]

    # Same test, side effect (common_mistakes §1): no duplicate row landed —
    # the thread still holds exactly the stored row, under the stable id.
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            SELECT provider_message_id FROM email_metadata
            WHERE account_id = %(aid)s AND thread_id = 'thr-3'
            """,
            {"aid": account_id},
        )
        assert [r[0] for r in cur.fetchall()] == ["stable"]


def test_conversation_missing_base_message_returns_404(
    test_client, setup_mailbox_and_account, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _patch_conversation_manager(monkeypatch, [])
    resp = test_client.get(_conversation_url(mailbox_id, account_id, "never-existed"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def test_conversation_missing_account_returns_404(
    test_client, setup_mailbox_and_account, monkeypatch,
):
    mailbox_id, _ = setup_mailbox_and_account(test_client, "gmail")
    _patch_conversation_manager(monkeypatch, [])
    resp = test_client.get(_conversation_url(mailbox_id, "nonexistent", "base"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_conversation_missing_mailbox_returns_404(test_client):
    resp = test_client.get(_conversation_url("nonexistent", "acc", "base"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


def test_conversation_provider_failure_returns_502(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_base_row(isolated_db, account_id, provider_message_id="base", thread_id="thr-x")
    # A provider-side fetch failure (e.g. Gmail 404 on a deleted thread)
    # surfaces as 502 — no artificial single-message fallback.
    _patch_conversation_manager(monkeypatch, EmailExternalAPIError("thread fetch failed"))
    resp = test_client.get(_conversation_url(mailbox_id, account_id, "base"))
    assert resp.status_code == 502
