"""
Integration tests for the Reply / Reply All / Forward context endpoint:
    - GET /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/reply-context

Exercises the real FastAPI app + real PostgreSQL (transaction-rolled-back)
with ``FakeEmailClient`` replacing the provider's reply-context fetch.

The endpoint never mutates state; it composes the reply prefill data
(recipients, subject, quoted body, threading metadata) from the
``ReplyContext`` returned by the provider client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg2.extras

from api.services import drafts_service, emails_service
from core.email import EmailManager
from core.email.email_client import ReplyContext
from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.shared.email_fakes import FakeEmailClient


def _reply_context_url(mailbox_id: str, account_id: str, pmid: str, action: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/"
        f"{pmid}/reply-context?action={action}"
    )


def _seed_email_metadata(
    isolated_db,
    *,
    account_id: str,
    provider_message_id: str,
    box: str = "ALL_MAIL",
) -> None:
    """Insert a minimal ``email_metadata`` row so the service's
    ``exists`` pre-check returns ``True``."""
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box)
            VALUES (%(pmid)s, %(aid)s::uuid, %(thr)s, %(fe)s, %(fn)s,
                    %(subj)s, %(ts)s::timestamptz, false, %(box)s)
            """,
            {
                "pmid": provider_message_id,
                "aid": account_id,
                "thr": "thr-1",
                "fe": "sender@x.com",
                "fn": "Sender",
                "subj": "Hello",
                "ts": "2026-05-23T14:32:00+00:00",
                "box": box,
            },
        )


def _build_reply_context(
    *,
    provider_message_id: str = "m1",
    from_email: str = "sender@x.com",
    from_name: str = "Sender",
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    reply_to: list[str] | None = None,
    subject: str = "Hello",
    message_id: str = "orig@x",
    references: str = "",
    thread_id: str = "thr-1",
    box: str = "ALL_MAIL",
) -> ReplyContext:
    return ReplyContext(
        provider_message_id=provider_message_id,
        thread_id=thread_id,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to or [],
        to_recipients=to_recipients or ["test@me.com"],
        cc_recipients=cc_recipients or [],
        subject=subject,
        body_html=None,
        body_text="hello body",
        received_at=datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc),
        message_id=message_id,
        references=references,
        box=box,
    )


def _override_manager_with_reply_context(
    monkeypatch, reply_context: ReplyContext, *, account_email: str | None = None,
):
    """Patch ``build_manager_for_accounts`` so the FakeEmailClient returns
    the injected ``ReplyContext`` from ``fetch_reply_context``."""

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                fetch_reply_context_return=reply_context,
            ))
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)
    monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

    if account_email:
        # The service reads ``account.get("email_address")`` to filter
        # the current user from Reply All CC.
        original_get = emails_service.account_store.get

        def _get(mailbox_id, account_id):
            row = original_get(mailbox_id, account_id)
            if row is not None:
                row = dict(row)
                row["email_address"] = account_email
            return row

        monkeypatch.setattr(emails_service.account_store, "get", _get)


def test_reply_returns_pre_filled_data(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    _override_manager_with_reply_context(
        monkeypatch,
        _build_reply_context(
            from_email="ana@x.com",
            from_name="Ana",
            to_recipients=["test@me.com"],
            subject="Hello",
            message_id="orig@x",
        ),
        account_email="test@me.com",
    )
    resp = test_client.get(_reply_context_url(mailbox_id, account_id, "m1", "reply"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Reply: To = [from], CC = [].
    assert body["to_recipients"] == ["ana@x.com"]
    assert body["cc_recipients"] == []
    assert body["subject"] == "Re: Hello"
    assert body["thread_id"] == "thr-1"
    assert body["in_reply_to"] == "<orig@x>"
    assert "<orig@x>" in body["references"]
    assert body["reply_kind"] == "reply"
    assert body["reply_to_message_id"] == "m1"
    assert body["original_from_email"] == "ana@x.com"
    assert "escribió:" in body["body"]


def test_reply_all_excludes_current_account_from_cc(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    _override_manager_with_reply_context(
        monkeypatch,
        _build_reply_context(
            from_email="ana@x.com",
            to_recipients=["test@me.com", "carol@z.com"],
            cc_recipients=["dan@y.com"],
        ),
        account_email="test@me.com",
    )
    resp = test_client.get(
        _reply_context_url(mailbox_id, account_id, "m1", "reply_all"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_recipients"] == ["ana@x.com"]
    cc = body["cc_recipients"]
    assert "test@me.com" not in cc
    assert "carol@z.com" in cc
    assert "dan@y.com" in cc


def test_reply_honours_reply_to_header(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # R-10: Reply-To wins over From — newsletter / mailing-list pattern.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    _override_manager_with_reply_context(
        monkeypatch,
        _build_reply_context(
            from_email="bounce@list.com",
            reply_to=["editor@list.com"],
        ),
        account_email="test@me.com",
    )
    resp = test_client.get(_reply_context_url(mailbox_id, account_id, "m1", "reply"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_recipients"] == ["editor@list.com"]


def test_self_reply_sent_box_uses_original_to(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # When the user replies to their own SENT message, the To becomes
    # the original ``to`` list (would otherwise loop back to the user).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1", box="SENT",
    )
    _override_manager_with_reply_context(
        monkeypatch,
        _build_reply_context(
            from_email="test@me.com",
            to_recipients=["client@x.com"],
            box="SENT",
        ),
        account_email="test@me.com",
    )
    resp = test_client.get(_reply_context_url(mailbox_id, account_id, "m1", "reply"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_recipients"] == ["client@x.com"]


def test_forward_subject_prefixed_and_empty_recipients(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    _override_manager_with_reply_context(
        monkeypatch,
        _build_reply_context(subject="Hello"),
        account_email="test@me.com",
    )
    resp = test_client.get(_reply_context_url(mailbox_id, account_id, "m1", "forward"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "Fwd: Hello"
    # Forward pre-fills no recipients — the user adds them.
    assert body["to_recipients"] == []
    assert body["cc_recipients"] == []
    # Body uses block header form, not ``> `` quoting.
    assert "Mensaje reenviado" in body["body"]
    assert body["reply_kind"] == "forward"


def test_invalid_action_returns_502(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Unknown ``action`` query value collapses to EmailReplyContextError → 502.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    resp = test_client.get(
        _reply_context_url(mailbox_id, account_id, "m1", "invalid_kind"),
    )
    # FastAPI's Literal validation rejects unknown values at the router
    # boundary with a 422.
    assert resp.status_code == 422


def test_email_not_found_returns_404(
    test_client, setup_mailbox_and_account, monkeypatch,
):
    # No email_metadata row → 404 ``email_not_found`` BEFORE provider call.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _override_manager_with_reply_context(
        monkeypatch, _build_reply_context(), account_email="test@me.com",
    )
    resp = test_client.get(
        _reply_context_url(mailbox_id, account_id, "never-existed", "reply"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def test_unknown_account_returns_404(test_client, setup_mailbox_and_account):
    mailbox_id, _aid = setup_mailbox_and_account(test_client, "gmail")
    resp = test_client.get(
        _reply_context_url(mailbox_id, "00000000-0000-4000-a000-000000000999", "m1", "reply"),
    )
    assert resp.status_code == 404


def test_provider_failure_returns_502_reply_context_error(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # The provider's fetch_reply_context raises EmailReplyContextFetchError
    # → service translates to EmailReplyContextError (HTTP 502).
    from core.email.errors import EmailReplyContextFetchError
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                fetch_reply_context_exc=EmailReplyContextFetchError(
                    "Provider 500", detail={"reason": "provider_fetch_failed"},
                ),
            ))
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)
    resp = test_client.get(_reply_context_url(mailbox_id, account_id, "m1", "reply"))
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "email_reply_context_error"


def test_foreign_mailbox_returns_403(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # The mailbox belongs to another user → ensure_mailbox_access raises
    # Forbidden (HTTP 403).
    from uuid import uuid4
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email_metadata(
        isolated_db, account_id=account_id, provider_message_id="m1",
    )
    other_user = str(uuid4())
    other_mbx = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, google_sub, email)
            VALUES (%(uid)s, %(sub)s, %(email)s)
            """,
            {"uid": other_user, "sub": f"sub-{other_user[:8]}",
             "email": "other@e.com"},
        )
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mb)s, %(dn)s, %(uid)s)
            """,
            {"mb": other_mbx, "dn": "Foreign", "uid": other_user},
        )
    resp = test_client.get(
        _reply_context_url(other_mbx, account_id, "m1", "reply"),
    )
    assert resp.status_code == 403
