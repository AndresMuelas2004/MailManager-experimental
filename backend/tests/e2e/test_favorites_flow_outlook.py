"""
End-to-end favourites flow against real Outlook / Microsoft Graph (no fakes).

Mirror of ``test_favorites_flow_gmail.py`` for the Outlook provider.
The favourite is the message ``flag`` (`flag.flagStatus`); the toggle is
Provider-First and now retries transient throttling/5xx responses
honouring ``Retry-After`` (the retry path is transparent here — only a
transient provider hiccup would exercise it). Every toggle is reversed in
a ``finally`` block so the seeded test account is left untouched
(common_mistakes.md §1 keeps the 404 follow-up in the same test).
"""

from __future__ import annotations

import pytest

from ._favorites_helpers import (
    _assert_ok,
    _find_non_spam_trash_message,
    _select_is_favorite,
)
from .e2e_config import OUTLOOK_ACCOUNT_ID, OUTLOOK_MAILBOX_ID


def test_61_set_favorite_toggle_outlook(e2e_client):
    """Mark then unmark a real Outlook email; assert local persistence + 404 path."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(OUTLOOK_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Outlook emails available for favourite toggle")

    original = _select_is_favorite(OUTLOOK_ACCOUNT_ID, pmid)
    base = f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/emails/{pmid}/favorite"
    try:
        on = e2e_client.patch(base, json={"favorite": True})
        _assert_ok(on)
        body = on.json()
        assert body["is_favorite"] is True
        assert body["provider_message_id"] == pmid
        assert body["account_id"] == OUTLOOK_ACCOUNT_ID
        assert _select_is_favorite(OUTLOOK_ACCOUNT_ID, pmid) is True

        off = e2e_client.patch(base, json={"favorite": False})
        _assert_ok(off)
        assert off.json()["is_favorite"] is False
        assert _select_is_favorite(OUTLOOK_ACCOUNT_ID, pmid) is False

        missing = e2e_client.patch(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/emails/never-existed-pmid-e2e/favorite",
            json={"favorite": True},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "email_not_found"
    finally:
        # Restore the original provider + local state so the seeded account
        # is left untouched. The restore is asserted: a silently failed restore
        # would leave the sacred seeded account dirty while the test stays green.
        if original is not None:
            _assert_ok(e2e_client.patch(base, json={"favorite": original}))


def test_62_sync_favorites_outlook(e2e_client):
    """A single-account favourites reconciliation returns a coherent envelope."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))

    resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/favorites/sync",
        params={"account_id": OUTLOOK_ACCOUNT_ID},
    )
    _assert_ok(resp)
    body = resp.json()
    assert isinstance(body["total_synced"], int)
    assert body["total_synced"] >= 0
    assert len(body["accounts"]) == 1
    detail = body["accounts"][0]
    assert detail["account_id"] == OUTLOOK_ACCOUNT_ID
    assert detail["favorites_synced"] >= 0
    assert detail["favorites_synced"] <= body["total_synced"]


def test_63_listing_favorite_excludes_spam_and_trash_outlook(e2e_client):
    """``favorite=true`` lists favourites and excludes SPAM/TRASH by default."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(OUTLOOK_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Outlook emails available for favourite listing")

    original = _select_is_favorite(OUTLOOK_ACCOUNT_ID, pmid)
    base = f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/emails/{pmid}/favorite"
    try:
        _assert_ok(e2e_client.patch(base, json={"favorite": True}))

        resp = e2e_client.get(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails",
            params={"box": "ALL_MAIL", "favorite": "true",
                    "account_id": OUTLOOK_ACCOUNT_ID, "limit": 50,
                    "group_by_thread": "false"},
        )
        _assert_ok(resp)
        rows = resp.json()["items"]
        ids = [r["provider_message_id"] for r in rows]
        assert pmid in ids, "the just-favourited email must surface in the listing"
        assert all(r["box"] not in ("SPAM", "TRASH") for r in rows)
        assert all(r["is_favorite"] is True for r in rows)
    finally:
        if original is not None:
            _assert_ok(e2e_client.patch(base, json={"favorite": original}))
