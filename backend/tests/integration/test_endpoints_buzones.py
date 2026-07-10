"""Tests de integración — endpoints de buzones (mailboxes): CRUD y carreras."""

from __future__ import annotations

from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_USER_ID as _SEEDED_USER,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
)


# ------------------------------------------------------------------
# Mailboxes
# ------------------------------------------------------------------

def test_create_mailbox(test_client):
    resp = test_client.post(_MAILBOX_URL, json={"display_name": "My Mailbox"})
    assert resp.status_code == 200
    data = resp.json()
    assert "mailbox_id" in data
    assert data["display_name"] == "My Mailbox"


def test_list_mailboxes(seeded_test_client):
    resp = seeded_test_client.get(_MAILBOX_URL)
    assert resp.status_code == 200
    data = resp.json()
    names = {m["display_name"] for m in data}
    assert "Gmail inventada" in names
    assert "Outlook inventada" in names
    gmail_mb = next(m for m in data if m["mailbox_id"] == _SEEDED_GMAIL_MAILBOX)
    assert gmail_mb["owner_user_id"] == _SEEDED_USER
    assert gmail_mb["display_name"] == "Gmail inventada"


def test_get_mailbox(seeded_test_client):
    resp = seeded_test_client.get(f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mailbox_id"] == _SEEDED_GMAIL_MAILBOX
    assert data["display_name"] == "Gmail inventada"
    assert data["owner_user_id"] == _SEEDED_USER


def test_update_mailbox(test_client):
    created = test_client.post(_MAILBOX_URL, json={"display_name": "Original"}).json()
    mid = created["mailbox_id"]
    resp = test_client.patch(f"{_MAILBOX_URL}/{mid}", json={"display_name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed"
    # The rename persisted: a fresh GET reflects the new name.
    assert test_client.get(f"{_MAILBOX_URL}/{mid}").json()["display_name"] == "Renamed"


def test_update_mailbox_empty_name_returns_422(test_client):
    created = test_client.post(_MAILBOX_URL, json={"display_name": "X"}).json()
    mid = created["mailbox_id"]
    resp = test_client.patch(f"{_MAILBOX_URL}/{mid}", json={"display_name": ""})
    assert resp.status_code == 422


def test_update_mailbox_name_too_long_returns_422(test_client):
    created = test_client.post(_MAILBOX_URL, json={"display_name": "X"}).json()
    mid = created["mailbox_id"]
    resp = test_client.patch(f"{_MAILBOX_URL}/{mid}", json={"display_name": "a" * 121})
    assert resp.status_code == 422


def test_update_mailbox_nonexistent_returns_404(test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{fake_mailbox_id}", json={"display_name": "Renamed"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


def test_update_mailbox_race_returns_404(test_client, monkeypatch):
    # Ownership pre-check passes, but the row vanishes before the UPDATE
    # (store.update returns None). The service must surface 404, not 200.
    from api.services import mailboxes_service

    created = test_client.post(_MAILBOX_URL, json={"display_name": "Race"}).json()
    mid = created["mailbox_id"]
    monkeypatch.setattr(mailboxes_service.mailbox_store, "update", lambda *a, **k: None)
    resp = test_client.patch(f"{_MAILBOX_URL}/{mid}", json={"display_name": "Renamed"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


def test_delete_mailbox(test_client):
    created = test_client.post(_MAILBOX_URL, json={"display_name": "ToDelete"}).json()
    mid = created["mailbox_id"]
    resp = test_client.delete(f"{_MAILBOX_URL}/{mid}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert test_client.get(f"{_MAILBOX_URL}/{mid}").status_code == 404


def test_delete_mailbox_race_returns_404(test_client, monkeypatch):
    # Ownership pre-check passes, but the row vanishes before the DELETE
    # (store.delete reports zero rows). The service must surface 404, not 200.
    from api.services import mailboxes_service

    created = test_client.post(_MAILBOX_URL, json={"display_name": "RaceDel"}).json()
    mid = created["mailbox_id"]
    monkeypatch.setattr(mailboxes_service.mailbox_store, "delete", lambda *a, **k: False)
    resp = test_client.delete(f"{_MAILBOX_URL}/{mid}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"
