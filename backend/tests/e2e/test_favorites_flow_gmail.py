"""
End-to-end favourites flow against real Gmail (no fakes).

Flow exercised (favourites surface, non-interactive endpoints only):

    POST /emails/sync-metadata                                  (seed local copy)
      → PATCH /accounts/{aid}/emails/{pmid}/favorite            (mark / unmark)
      → POST  /favorites/sync                                   (reconcile)
      → GET   /emails?favorite=true                             (listing + exclusions)

The PATCH path is Provider-First (`STARRED` label applied at Gmail first,
then persisted locally) and idempotent, so every toggle is reversed in a
``finally`` block to leave the seeded test account exactly as it was.
The 404-on-missing-id follow-up stays in the same test as the toggle it
belongs to (common_mistakes.md §1).
"""

from __future__ import annotations

import pytest

from ._favorites_helpers import (
    _assert_ok,
    _find_non_spam_trash_message,
    _select_is_favorite,
)
from .e2e_config import GMAIL_ACCOUNT_ID, GMAIL_MAILBOX_ID


def test_58_set_favorite_toggle_gmail(e2e_client):
    """Mark then unmark a real Gmail email; assert local persistence + 404 path."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for favourite toggle")

    original = _select_is_favorite(GMAIL_ACCOUNT_ID, pmid)
    base = f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/emails/{pmid}/favorite"
    try:
        # Mark as favourite.
        on = e2e_client.patch(base, json={"favorite": True})
        _assert_ok(on)
        body = on.json()
        assert body["is_favorite"] is True
        assert body["provider_message_id"] == pmid
        assert body["account_id"] == GMAIL_ACCOUNT_ID
        assert _select_is_favorite(GMAIL_ACCOUNT_ID, pmid) is True

        # Unmark (idempotent toggle back).
        off = e2e_client.patch(base, json={"favorite": False})
        _assert_ok(off)
        assert off.json()["is_favorite"] is False
        assert _select_is_favorite(GMAIL_ACCOUNT_ID, pmid) is False

        # 404 on an unknown id — same logical contract, same test
        # (common_mistakes.md §1). The provider is never called (pre-check).
        missing = e2e_client.patch(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
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


def test_59_sync_favorites_gmail(e2e_client):
    """Reconciliation keeps a just-starred favourite AND returns a coherent envelope.

    Mirror of ``test_62_sync_favorites_outlook``: the sync is a full replacement
    (``is_favorite = pmid = ANY(provider_ids)``), so the survival assertion pins
    that the ids ``list_favorite_ids`` returns match the stored ones. Gmail ids
    are stable across endpoints, so this documents the contract the Outlook twin
    exists to interrogate.
    """
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for favourites sync")

    original = _select_is_favorite(GMAIL_ACCOUNT_ID, pmid)
    base = f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/emails/{pmid}/favorite"
    try:
        _assert_ok(e2e_client.patch(base, json={"favorite": True}))
        assert _select_is_favorite(GMAIL_ACCOUNT_ID, pmid) is True

        resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/favorites/sync",
            params={"account_id": GMAIL_ACCOUNT_ID},
        )
        _assert_ok(resp)
        body = resp.json()
        # total_synced is the rowcount across the account (>= the provider's
        # favourite count, since every row is rewritten in one statement).
        assert isinstance(body["total_synced"], int)
        assert body["total_synced"] >= 0
        assert len(body["accounts"]) == 1
        detail = body["accounts"][0]
        assert detail["account_id"] == GMAIL_ACCOUNT_ID
        assert detail["favorites_synced"] >= 0
        # The provider favourite count never exceeds the rows touched.
        assert detail["favorites_synced"] <= body["total_synced"]

        # The favourite starred at the provider moments ago survives the
        # full-replacement reconciliation (same test, same logical operation —
        # common_mistakes.md §1).
        assert _select_is_favorite(GMAIL_ACCOUNT_ID, pmid) is True
    finally:
        if original is not None:
            _assert_ok(e2e_client.patch(base, json={"favorite": original}))


def test_60_listing_favorite_excludes_spam_and_trash_gmail(e2e_client):
    """``favorite=true`` lists favourites and excludes SPAM/TRASH by default."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for favourite listing")

    original = _select_is_favorite(GMAIL_ACCOUNT_ID, pmid)
    base = f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/emails/{pmid}/favorite"
    try:
        _assert_ok(e2e_client.patch(base, json={"favorite": True}))

        resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
            params={"box": "ALL_MAIL", "favorite": "true",
                    "account_id": GMAIL_ACCOUNT_ID, "limit": 50,
                    "group_by_thread": "false"},
        )
        _assert_ok(resp)
        rows = resp.json()["items"]
        ids = [r["provider_message_id"] for r in rows]
        assert pmid in ids, "the just-favourited email must surface in the listing"
        # Default anchor (ALL_MAIL) excludes SPAM/TRASH.
        assert all(r["box"] not in ("SPAM", "TRASH") for r in rows)
        # Every listed row is genuinely a favourite.
        assert all(r["is_favorite"] is True for r in rows)
    finally:
        if original is not None:
            _assert_ok(e2e_client.patch(base, json={"favorite": original}))
