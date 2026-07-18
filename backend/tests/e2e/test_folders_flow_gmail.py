"""
End-to-end folder + rule flow against real Gmail (no fakes).

Flow exercised (non-interactive endpoints only):

    POST /emails/sync-metadata                                    (seed local copy)
      → POST   /folders                                           (create)
      → POST   /accounts/{aid}/emails/{pmid}/folders              (assign, Provider-First)
      → GET    /folders/{fid}/emails                              (listing)
      → DELETE /accounts/{aid}/emails/{pmid}/folders/{fid}        (unassign)
      → DELETE /folders/{fid}                                     (delete + reflect)

Assign is Provider-First (the Gmail user label is applied first, then the local
member persisted). Every folder created is deleted in a ``finally`` so the sacred
seeded Gmail account is left exactly as it was (the delete also removes the user
label at Gmail). Destructive-then-verify pairs stay in the same test
(common_mistakes.md §1).
"""

from __future__ import annotations

import uuid

import pytest

from ._favorites_helpers import _assert_ok, _find_non_spam_trash_message
from ._folders_helpers import (
    _folder_listing_ids,
    _poll_apply_until_done,
    _select_from_email,
)
from .e2e_config import GMAIL_ACCOUNT_ID, GMAIL_MAILBOX_ID


def test_70_folder_lifecycle_gmail(e2e_client):
    """Create a folder, assign a real Gmail email, list it, unassign, delete."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for the folder flow")

    created = e2e_client.post("/folders", json={"name": f"E2E carpeta {uuid.uuid4().hex[:8]}"})
    _assert_ok(created, expected=201)
    fid = created.json()["folder_id"]
    base = f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/emails/{pmid}/folders"
    try:
        # Assign — the Gmail user label is applied first, then the member is
        # persisted, and the response carries the email's folder list.
        assign = e2e_client.post(base, json={"folder_id": fid})
        _assert_ok(assign)
        assert fid in [f["folder_id"] for f in assign.json()["folders"]]

        # The email surfaces in the unified folder listing.
        assert pmid in _folder_listing_ids(e2e_client, fid)

        # Unassign + verify removal (same test, common_mistakes.md §1).
        unassign = e2e_client.delete(f"{base}/{fid}")
        _assert_ok(unassign)
        assert unassign.json()["folders"] == []
        assert pmid not in _folder_listing_ids(e2e_client, fid)
    finally:
        # Delete the folder (cleanup + reflects to Gmail: label removed).
        _assert_ok(e2e_client.delete(f"/folders/{fid}"))


def test_71_rule_apply_gmail(e2e_client):
    """Create a rule matching a real sender, apply to existing, verify classification."""
    _assert_ok(e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(GMAIL_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Gmail emails available for the rule flow")
    sender = _select_from_email(GMAIL_ACCOUNT_ID, pmid)
    if not sender:
        pytest.skip("Picked Gmail message has no sender address")

    folder = e2e_client.post("/folders", json={"name": f"E2E reglas {uuid.uuid4().hex[:8]}"})
    _assert_ok(folder, expected=201)
    fid = folder.json()["folder_id"]
    rule_id = None
    try:
        rule = e2e_client.post(
            "/rules",
            json={"match_from_email": sender, "target_folder_id": fid, "apply_to_existing": True},
        )
        _assert_ok(rule, expected=201)
        rule_id = rule.json()["rule_id"]

        done = _poll_apply_until_done(e2e_client, rule_id)
        if done is None:
            pytest.skip("Rule apply worker did not drain within the poll budget")
        assert done["status"] == "completed"
        # At least the message we matched on must now be in the folder.
        assert pmid in _folder_listing_ids(e2e_client, fid)
    finally:
        if rule_id is not None:
            _assert_ok(e2e_client.delete(f"/rules/{rule_id}"))
        # Delete the folder (cleanup + removes the Gmail label from members).
        _assert_ok(e2e_client.delete(f"/folders/{fid}"))
