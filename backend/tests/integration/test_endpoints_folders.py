"""
Integration tests for the folder surface (carpetas-y-reglas).

Real routers + services + DB (per-test transaction rollback); the provider is
faked via ``FakeEmailClient`` (which the EmailManager routes through the Outlook
category branch for the per-email assign). Covers:

- ``/folders`` CRUD + case-insensitive name conflict + ownership 404
- per-email assign / unassign on the account-scoped route
  (``POST/DELETE /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/folders[/{fid}]``,
  mounted on ``favorites_router``)
- ``GET /folders/{id}/emails`` (unified listing reusing ``list_filtered`` with
  ``folder_id``)
"""

from __future__ import annotations

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL


_FOLDERS_URL = "/folders"
_RANDOM_UUID = "99999999-9999-4000-a000-999999999999"


def _create_folder(client, name="Universidad", color=None):
    body = {"name": name}
    if color is not None:
        body["color"] = color
    resp = client.post(_FOLDERS_URL, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_email(account_id, provider_message_id="m1", subject="Hello"):
    from api.services.services_helpers import persist_email_metadata_batch
    from tests.shared.email_fakes import build_metadata

    persist_email_metadata_batch(
        account_id, [build_metadata(provider_message_id=provider_message_id, subject=subject)],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_and_list_folder(test_client):
    created = _create_folder(test_client, "Universidad", color="#ff0000")
    assert created["name"] == "Universidad"
    assert created["color"] == "#ff0000"

    listed = test_client.get(_FOLDERS_URL)
    assert listed.status_code == 200
    assert any(f["folder_id"] == created["folder_id"] for f in listed.json())


def test_get_folder_by_id(test_client):
    created = _create_folder(test_client)
    resp = test_client.get(f"{_FOLDERS_URL}/{created['folder_id']}")
    assert resp.status_code == 200
    assert resp.json()["folder_id"] == created["folder_id"]


def test_rename_folder(test_client):
    created = _create_folder(test_client, "Old")
    resp = test_client.patch(f"{_FOLDERS_URL}/{created['folder_id']}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


def test_case_insensitive_name_conflict_is_409(test_client):
    _create_folder(test_client, "Universidad")
    resp = test_client.post(_FOLDERS_URL, json={"name": "universidad"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_name_conflict"


def test_missing_folder_is_404(test_client):
    resp = test_client.get(f"{_FOLDERS_URL}/{_RANDOM_UUID}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"


def test_delete_folder_then_get_is_404(test_client):
    # The follow-up 404 verification stays in the SAME test (common_mistakes #1
    # is E2E-only, but a destructive-then-verify pair reads well here too).
    created = _create_folder(test_client)
    deleted = test_client.delete(f"{_FOLDERS_URL}/{created['folder_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    gone = test_client.get(f"{_FOLDERS_URL}/{created['folder_id']}")
    assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Per-email assign / unassign + folder listing
# ---------------------------------------------------------------------------


def test_assign_list_and_unassign_email(test_client, setup_mailbox_and_account):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email(account_id, "m1")
    folder = _create_folder(test_client, "Facturas")
    fid = folder["folder_id"]

    assign = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/m1/folders",
        json={"folder_id": fid},
    )
    assert assign.status_code == 200, assign.text
    assert [f["folder_id"] for f in assign.json()["folders"]] == [fid]

    # The email now surfaces in the folder listing (unified across accounts).
    listing = test_client.get(f"{_FOLDERS_URL}/{fid}/emails")
    assert listing.status_code == 200
    page = listing.json()
    assert page["total"] >= 1
    assert any(item["provider_message_id"] == "m1" for item in page["items"])

    # Unassign and verify the chip is gone (same test, side-effect verification).
    unassign = test_client.delete(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/m1/folders/{fid}",
    )
    assert unassign.status_code == 200
    assert unassign.json()["folders"] == []

    empty = test_client.get(f"{_FOLDERS_URL}/{fid}/emails")
    assert empty.json()["total"] == 0


def test_assign_to_missing_email_is_404(test_client, setup_mailbox_and_account):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    folder = _create_folder(test_client)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/ghost/folders",
        json={"folder_id": folder["folder_id"]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def test_assign_with_foreign_folder_is_404(test_client, setup_mailbox_and_account):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_email(account_id, "m1")
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/emails/m1/folders",
        json={"folder_id": _RANDOM_UUID},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"


def test_folder_listing_of_foreign_folder_is_404(test_client):
    resp = test_client.get(f"{_FOLDERS_URL}/{_RANDOM_UUID}/emails")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"


def test_folder_search_query_below_min_length_is_422(test_client):
    folder = _create_folder(test_client)
    resp = test_client.get(f"{_FOLDERS_URL}/{folder['folder_id']}/emails", params={"q": "a"})
    assert resp.status_code == 422
