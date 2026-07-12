"""Tests de integración — endpoints de cuentas: CRUD, conexión, configuración y firma."""

from __future__ import annotations

from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_GMAIL_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
    SEEDED_OUTLOOK_ACCOUNT_ID as _SEEDED_OUTLOOK_ACCOUNT,
    SEEDED_OUTLOOK_MAILBOX_ID as _SEEDED_OUTLOOK_MAILBOX,
)


# ------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------

def test_create_account(test_client):
    mb = test_client.post(_MAILBOX_URL, json={"display_name": "AccMB"}).json()
    mid = mb["mailbox_id"]
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "acc1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "account_id" in data
    assert data["provider"] == "gmail"


def test_list_accounts(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/accounts"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    account = data[0]
    assert account["account_id"] == _SEEDED_GMAIL_ACCOUNT
    assert account["provider"] == "gmail"
    assert account["display_label"] == "Gmail inventada - inventadoParaEndpointGet"
    assert account["email_address"] == "gmailinventada@gmail.com"
    assert account["mailbox_id"] == _SEEDED_GMAIL_MAILBOX


def test_get_account(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_OUTLOOK_MAILBOX}/accounts/{_SEEDED_OUTLOOK_ACCOUNT}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == _SEEDED_OUTLOOK_ACCOUNT
    assert data["provider"] == "outlook"
    assert data["display_label"] == "Outlook inventada - inventadoParaEndpointGet"
    assert data["email_address"] == "outlookinventada@outlook.com"
    assert data["mailbox_id"] == _SEEDED_OUTLOOK_MAILBOX


def test_update_account(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"display_label": "renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_label"] == "renamed"


def test_delete_account(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.delete(f"{_MAILBOX_URL}/{mid}/accounts/{aid}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


def test_connect_account_two_phase_flow(test_client, setup_mailbox_and_account):
    """POST /connect returns the authorization URL; the OAuth callback
    completes the connection and the state token is single-use."""
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/accounts/{aid}/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == aid
    assert data["authorization_url"].startswith("https://")
    assert data["state"]

    callback = test_client.get(
        "/auth/google/callback", params={"state": data["state"], "code": "auth-code"},
    )
    assert callback.status_code == 200
    assert "Account connected" in callback.text

    replay = test_client.get(
        "/auth/google/callback", params={"state": data["state"], "code": "auth-code"},
    )
    assert replay.status_code == 200
    assert "Connection failed" in replay.text


# ==================================================================
# Account quota + per-user limit (GET /accounts/quota, 409 on create)
# ==================================================================

_QUOTA_URL = "/accounts/quota"


def test_get_account_quota_reports_seeded_count(seeded_test_client, monkeypatch):
    # The seed (migration 0010) gives the seeded user exactly two accounts
    # (one gmail + one outlook), and the default cap is 15.
    monkeypatch.delenv("MAX_ACCOUNTS_PER_USER", raising=False)
    resp = seeded_test_client.get(_QUOTA_URL)
    assert resp.status_code == 200
    assert resp.json() == {"connected": 2, "limit": 15}


def test_get_account_quota_reflects_created_accounts(test_client, monkeypatch):
    # The default test user owns no accounts until one is created (the seed is
    # under a different user), so the counter is a live COUNT.
    monkeypatch.delenv("MAX_ACCOUNTS_PER_USER", raising=False)
    assert test_client.get(_QUOTA_URL).json() == {"connected": 0, "limit": 15}

    mid = test_client.post(_MAILBOX_URL, json={"display_name": "QuotaMB"}).json()["mailbox_id"]
    test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "acc1"},
    )
    assert test_client.get(_QUOTA_URL).json() == {"connected": 1, "limit": 15}


def test_create_account_over_limit_returns_409(test_client, monkeypatch):
    # With the cap lowered to 1, the second account creation is rejected with a
    # 409 ``account_limit_exceeded`` carrying the numbers for the UI counter.
    monkeypatch.setenv("MAX_ACCOUNTS_PER_USER", "1")
    mid = test_client.post(_MAILBOX_URL, json={"display_name": "LimitMB"}).json()["mailbox_id"]

    first = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "acc1"},
    )
    assert first.status_code == 200

    second = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "outlook", "display_label": "acc2"},
    )
    assert second.status_code == 409
    body = second.json()["error"]
    assert body["code"] == "account_limit_exceeded"
    assert body["detail"] == {"limit": 1, "connected": 1}


# ==================================================================
# Partial update
# ==================================================================

def test_update_account_config_only(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"config": {"extra": True}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"] == {"extra": True}
    assert data["display_label"] == "test-gmail"


def test_update_account_signature_html(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": "<p>Firma HTML</p>"},
    )
    assert resp.status_code == 200
    assert resp.json()["signature_html"] == "<p>Firma HTML</p>"


def test_update_account_signature_html_persists(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    patch = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": "<p>Persisted signature</p>"},
    )
    assert patch.status_code == 200

    # A follow-up GET reads the stored value back (real DB with rollback).
    got = test_client.get(f"{_MAILBOX_URL}/{mid}/accounts/{aid}")
    assert got.status_code == 200
    data = got.json()
    assert data["signature_html"] == "<p>Persisted signature</p>"
    # The signature does not clobber the rest of the account.
    assert data["display_label"] == "test-gmail"


def test_update_account_signature_html_empty(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    # Seed a signature, then clear it with an explicit "".
    test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": "<p>to be cleared</p>"},
    )
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["signature_html"] == ""


def test_update_account_signature_html_sanitised(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={
            "signature_html": (
                '<p style="color:red">hola</p>'
                '<img src="https://x/y.png">'
                "<script>alert(1)</script>"
            )
        },
    )
    assert resp.status_code == 200
    signature = resp.json()["signature_html"]
    # Outbound sanitiser strips images, inline colour styles and scripts at the
    # boundary while keeping the visible text.
    assert "<script>" not in signature
    assert "<img" not in signature
    assert "color:red" not in signature
    assert "hola" in signature


def test_update_account_signature_html_too_long_422(test_client, setup_mailbox_and_account):
    mid, aid = setup_mailbox_and_account(test_client)
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": "a" * 10_001},
    )
    # Over the 10_000 cap → FastAPI's default validation envelope ({"detail":
    # [...]}), NOT the project's {"error": {...}} shape (no RequestValidationError
    # handler is registered).
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_update_account_signature_html_at_cap_accepted(
    test_client, setup_mailbox_and_account
):
    # Exactly at the 10_000 cap must be accepted (200) — guards the boundary
    # against an off-by-one that tightens the cap below 10_000. Plain text at
    # the cap carries no tags, so the outbound sanitiser returns it unchanged.
    mid, aid = setup_mailbox_and_account(test_client)
    signature = "a" * 10_000
    resp = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": signature},
    )
    assert resp.status_code == 200
    assert resp.json()["signature_html"] == signature


def test_update_account_signature_html_survives_unrelated_update(
    test_client, setup_mailbox_and_account
):
    # Load-bearing regression for the UPSERT_ACCOUNT four-clause lockstep
    # (database_guide.md): an unrelated PATCH must NOT wipe a stored signature.
    # Dropping signature_html from the query's INSERT/VALUES would make
    # EXCLUDED.signature_html resolve to the column DEFAULT (NULL) and silently
    # clear the signature on every account update, while the rest of the
    # signature suite stays green.
    mid, aid = setup_mailbox_and_account(test_client)
    seeded = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"signature_html": "<p>keep me</p>"},
    )
    assert seeded.status_code == 200

    # PATCH an unrelated field — the body carries no signature_html key.
    renamed = test_client.patch(
        f"{_MAILBOX_URL}/{mid}/accounts/{aid}",
        json={"display_label": "renamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_label"] == "renamed"
    assert renamed.json()["signature_html"] == "<p>keep me</p>"

    # And it survives a follow-up read from the DB.
    got = test_client.get(f"{_MAILBOX_URL}/{mid}/accounts/{aid}")
    assert got.json()["signature_html"] == "<p>keep me</p>"


# ==================================================================
# Outlook end-to-end
# ==================================================================

def test_outlook_account_connect(test_client):
    mid = test_client.post(_MAILBOX_URL, json={"display_name": "OL"}).json()["mailbox_id"]
    aid = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "outlook", "display_label": "my-outlook"},
    ).json()["account_id"]
    resp = test_client.post(f"{_MAILBOX_URL}/{mid}/accounts/{aid}/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "outlook"
    assert data["authorization_url"].startswith("https://")

    callback = test_client.get(
        "/auth/outlook/callback", params={"state": data["state"], "code": "auth-code"},
    )
    assert callback.status_code == 200
    assert "Account connected" in callback.text
