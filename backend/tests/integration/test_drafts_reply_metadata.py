"""
Integration tests for the reply / forward metadata invariants in drafts:

    1. POST /mailboxes/{mid}/accounts/{aid}/drafts persists every reply
       field into the ``drafts`` table (no schema drop).
    2. POST /mailboxes/{mid}/drafts/sync does NOT clobber locally-set
       reply metadata when the provider returns rows without those
       fields — the COALESCE clauses in ``UPSERT_DRAFTS_BATCH`` are
       load-bearing.
    3. POST /mailboxes/{mid}/accounts/{aid}/drafts/{pdid}/send reads the
       reply metadata from the local row and propagates it to the
       provider's send_draft_with_attachments call.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg2.extras

from api.services import drafts_service
from core.email import DraftMetadata, EmailManager
from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.shared.email_fakes import FakeEmailClient


def _create_draft_url(mailbox_id: str, account_id: str) -> str:
    return f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/drafts"


def _sync_drafts_url(mailbox_id: str, account_id: str | None = None) -> str:
    url = f"{_MAILBOX_URL}/{mailbox_id}/drafts/sync"
    if account_id is not None:
        url += f"?account_id={account_id}"
    return url


def _send_draft_url(mailbox_id: str, account_id: str, draft_id: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/drafts/"
        f"{draft_id}/send"
    )


def _patch_fake_clients(monkeypatch, **fake_kwargs):
    """Replace ``build_manager_for_accounts`` so every test client uses
    the same kwargs (e.g. ``fetch_drafts_return``)."""

    captured: list[FakeEmailClient] = []

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            client = FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **fake_kwargs,
            )
            captured.append(client)
            manager.add_client(client)
        return manager

    monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
    return captured


def test_create_draft_persists_reply_fields(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Every reply / forward field on the request body lands in the
    matching ``drafts`` column."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _patch_fake_clients(monkeypatch)
    resp = test_client.post(
        _create_draft_url(mailbox_id, account_id),
        json={
            "to_recipients": ["to@x.com"],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": "Re: Hello",
            "body": "body",
            "reply_kind": "reply",
            "reply_to_message_id": "orig-msg-1",
            "thread_id": "thr-1",
            "in_reply_to": "<orig@x>",
            "references_header": "<prev@x> <orig@x>",
        },
    )
    assert resp.status_code == 200, resp.text

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT reply_kind, reply_to_message_id, thread_id, in_reply_to, "
            "references_header FROM drafts WHERE account_id = %s::uuid",
            (account_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["reply_kind"] == "reply"
    assert row["reply_to_message_id"] == "orig-msg-1"
    assert row["thread_id"] == "thr-1"
    assert row["in_reply_to"] == "<orig@x>"
    assert row["references_header"] == "<prev@x> <orig@x>"


def test_create_draft_without_reply_fields_stores_nulls(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A compose-from-scratch draft leaves the reply columns NULL so
    the COALESCE clause in sync's UPSERT preserves them on future syncs."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _patch_fake_clients(monkeypatch)
    resp = test_client.post(
        _create_draft_url(mailbox_id, account_id),
        json={"to_recipients": ["to@x.com"], "subject": "Hello", "body": "b"},
    )
    assert resp.status_code == 200, resp.text

    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT reply_kind, reply_to_message_id, reply_to_account_id, "
            "thread_id, in_reply_to, references_header "
            "FROM drafts WHERE account_id = %s::uuid",
            (account_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["reply_kind"] is None
    assert row["reply_to_message_id"] is None
    assert row["reply_to_account_id"] is None
    assert row["thread_id"] is None
    assert row["in_reply_to"] is None
    assert row["references_header"] is None


def test_sync_drafts_preserves_local_reply_metadata(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """The COALESCE in UPSERT_DRAFTS_BATCH preserves locally-persisted
    reply metadata even when the provider returns the same draft with
    NULL reply fields (sync pulls from provider where these fields
    don't exist as draft properties)."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Insert a reply-derived draft directly.
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO drafts (
                provider_draft_id, account_id, to_recipients, cc_recipients,
                bcc_recipients, subject, body,
                reply_kind, reply_to_message_id, thread_id, in_reply_to,
                references_header
            )
            VALUES (
                'drf-1', %(aid)s::uuid, %(tor)s, %(empty)s, %(empty)s,
                'Re: Hello', 'body',
                'reply', 'orig-1', 'thr-1', '<orig@x>',
                '<prev@x> <orig@x>'
            )
            """,
            {"aid": account_id, "tor": ["to@x.com"], "empty": []},
        )

    # Sync returns the same draft id with NULL reply metadata (provider
    # doesn't expose them as draft properties).
    fake_drafts = [
        DraftMetadata(
            provider_draft_id="drf-1",
            to_recipients=["to@x.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="updated body",  # sync says the body was updated
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ),
    ]
    _patch_fake_clients(monkeypatch, fetch_drafts_return=fake_drafts)

    resp = test_client.post(_sync_drafts_url(mailbox_id, account_id))
    assert resp.status_code == 200, resp.text

    # Local reply metadata MUST still be set — the COALESCE preserved them.
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT reply_kind, reply_to_message_id, thread_id, in_reply_to, "
            "references_header, body FROM drafts "
            "WHERE provider_draft_id = 'drf-1' AND account_id = %s::uuid",
            (account_id,),
        )
        row = cur.fetchone()
    assert row is not None
    # Body was updated by the sync.
    assert row["body"] == "updated body"
    # But reply metadata survives unchanged — this is the COALESCE invariant.
    assert row["reply_kind"] == "reply"
    assert row["reply_to_message_id"] == "orig-1"
    assert row["thread_id"] == "thr-1"
    assert row["in_reply_to"] == "<orig@x>"
    assert row["references_header"] == "<prev@x> <orig@x>"


def test_send_draft_propagates_reply_metadata_from_row(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """The send endpoint reads reply metadata from the local row (NOT
    from the request body — there is no body) and forwards it to the
    provider's send_draft_with_attachments call."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Pre-seed a reply-derived draft.
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO drafts (
                provider_draft_id, account_id, to_recipients, cc_recipients,
                bcc_recipients, subject, body,
                reply_kind, reply_to_message_id, thread_id, in_reply_to,
                references_header
            )
            VALUES (
                'drf-send-1', %(aid)s::uuid, %(tor)s, %(empty)s, %(empty)s,
                'Re: Hello', 'body',
                'reply', 'orig-1', 'thr-1', '<orig@x>',
                '<prev@x>'
            )
            """,
            {"aid": account_id, "tor": ["to@x.com"], "empty": []},
        )

    clients = _patch_fake_clients(monkeypatch)

    resp = test_client.post(_send_draft_url(mailbox_id, account_id, "drf-send-1"))
    assert resp.status_code == 200, resp.text
    # The provider's send call carries the metadata read from the row.
    assert len(clients) == 1
    kwargs_list = clients[0].send_draft_with_attachments_reply_kwargs
    assert len(kwargs_list) == 1
    kw = kwargs_list[0]
    assert kw["in_reply_to"] == "<orig@x>"
    assert kw["references"] == "<prev@x>"
    assert kw["thread_id"] == "thr-1"
