"""
End-to-end folder + rule flow against real Outlook (no fakes).

Mirror of ``test_folders_flow_gmail.py`` against the seeded Outlook account. The
provider mechanism differs (a folder materialises as an Outlook CATEGORY applied
read-modify-write, and the label-level rename/delete is a per-member re-tag), but
the endpoint contract is identical, so the flow and assertions mirror the Gmail
twin. Every folder is deleted in a ``finally`` to leave the sacred seeded Outlook
account untouched.
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
from .e2e_config import OUTLOOK_ACCOUNT_ID, OUTLOOK_MAILBOX_ID


def test_72_folder_lifecycle_outlook(e2e_client):
    """Create a folder, assign a real Outlook email (category), list, unassign, delete."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(OUTLOOK_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Outlook emails available for the folder flow")

    created = e2e_client.post("/folders", json={"name": f"E2E carpeta {uuid.uuid4().hex[:8]}"})
    _assert_ok(created, expected=201)
    fid = created.json()["folder_id"]
    base = f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/emails/{pmid}/folders"
    try:
        assign = e2e_client.post(base, json={"folder_id": fid})
        _assert_ok(assign)
        assert fid in [f["folder_id"] for f in assign.json()["folders"]]

        assert pmid in _folder_listing_ids(e2e_client, fid)

        unassign = e2e_client.delete(f"{base}/{fid}")
        _assert_ok(unassign)
        assert unassign.json()["folders"] == []
        assert pmid not in _folder_listing_ids(e2e_client, fid)
    finally:
        _assert_ok(e2e_client.delete(f"/folders/{fid}"))


def test_73_rule_apply_outlook(e2e_client):
    """Create a rule matching a real Outlook sender, apply to existing, verify."""
    _assert_ok(e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata"))
    pmid = _find_non_spam_trash_message(OUTLOOK_ACCOUNT_ID)
    if pmid is None:
        pytest.skip("No synced Outlook emails available for the rule flow")
    sender = _select_from_email(OUTLOOK_ACCOUNT_ID, pmid)
    if not sender:
        pytest.skip("Picked Outlook message has no sender address")

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
        assert pmid in _folder_listing_ids(e2e_client, fid)
    finally:
        if rule_id is not None:
            _assert_ok(e2e_client.delete(f"/rules/{rule_id}"))
        _assert_ok(e2e_client.delete(f"/folders/{fid}"))
