"""
Unit tests for the accounts service layer.

All database stores and external helpers are monkeypatched to avoid real calls.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountNotFound,
    ApiError,
    DatabaseQueryError,
    ExternalAPIError,
)
from api.schemas.account import AccountConnectStartResponse, AccountCreate, AccountOut, AccountUpdate
from api.services import accounts_service, oauth_pending
from core.email import EmailAuthError, EmailManager
from database import QueryError
from tests.shared.email_fakes import FakeEmailClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_FAKE_RECORD = {
    "account_id": "00000000-0000-0000-0000-000000000001",
    "mailbox_id": "00000000-0000-0000-0000-000000000002",
    "provider": "gmail",
    "display_label": "my-gmail",
    "config": {},
}


class FakeAccountStore:
    """In-memory account store for unit tests."""

    def __init__(self, *, records=None, get_return=None, tokens=None):
        self._records = list(records or [])
        self._get_return = get_return
        self._tokens = tokens
        self.deleted: list[tuple[str, str]] = []
        self.upserted_tokens: list[tuple] = []

    def list_by_mailbox(self, mailbox_id):
        return [r for r in self._records if r["mailbox_id"] == mailbox_id]

    def get(self, mailbox_id, account_id):
        return dict(self._get_return) if self._get_return else None

    def upsert(self, record):
        return {**record}

    def delete(self, mailbox_id, account_id):
        self.deleted.append((mailbox_id, account_id))

    def upsert_tokens(self, mailbox_id, account_id, provider, token_payload):
        self.upserted_tokens.append((mailbox_id, account_id, provider, token_payload))


class FakeAccountStoreRaising:
    """Account store that raises on every method."""

    def __init__(self, exc, *, get_return=None):
        self._exc = exc
        self._get_return = get_return

    def list_by_mailbox(self, mailbox_id):
        raise self._exc

    def get(self, mailbox_id, account_id):
        if self._get_return is not None:
            return dict(self._get_return)
        raise self._exc

    def upsert(self, record):
        raise self._exc

    def delete(self, mailbox_id, account_id):
        raise self._exc

    def upsert_tokens(self, mailbox_id, account_id, provider, token_payload):
        raise self._exc


def _patch_access(monkeypatch):
    """Bypass ensure_mailbox_access in all tests."""
    monkeypatch.setattr(
        accounts_service, "ensure_mailbox_access",
        lambda mid, uid: {"mailbox_id": mid, "owner_user_id": uid},
    )


# ------------------------------------------------------------------
# _resolve_display_label (pure function)
# ------------------------------------------------------------------


class TestResolveDisplayLabel:

    def test_returns_display_label_when_present(self):
        record = {"display_label": "Custom", "provider": "gmail", "account_id": "a1"}
        assert accounts_service._resolve_display_label(record) == "Custom"

    def test_falls_back_to_provider_account_id(self):
        record = {"provider": "outlook", "account_id": "a2"}
        assert accounts_service._resolve_display_label(record) == "outlook:a2"


# ------------------------------------------------------------------
# list_accounts
# ------------------------------------------------------------------


class TestListAccounts:

    def test_happy_path_returns_list(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(records=[_FAKE_RECORD])
        monkeypatch.setattr(accounts_service, "account_store", store)
        result = accounts_service.list_accounts(_FAKE_RECORD["mailbox_id"], "user-1")
        assert len(result) == 1
        assert isinstance(result[0], AccountOut)

    def test_database_error_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(DatabaseQueryError):
            accounts_service.list_accounts("mb-1", "user-1")

    def test_generic_exception_raises_api_error(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(RuntimeError("boom"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(ApiError, match="Failed to list accounts"):
            accounts_service.list_accounts("mb-1", "user-1")


# ------------------------------------------------------------------
# create_account
# ------------------------------------------------------------------


class TestCreateAccount:

    def test_happy_path_returns_created_account(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore()
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountCreate(provider="gmail", display_label="my-acc", config={})
        result = accounts_service.create_account("mb-1", payload, "user-1")
        assert isinstance(result, AccountOut)
        assert result.provider == "gmail"

    def test_database_error_on_upsert_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountCreate(provider="gmail", display_label="x", config={})
        with pytest.raises(DatabaseQueryError):
            accounts_service.create_account("mb-1", payload, "user-1")

    def test_generic_exception_raises_api_error(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(RuntimeError("boom"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountCreate(provider="gmail", display_label="x", config={})
        with pytest.raises(ApiError, match="Failed to create account"):
            accounts_service.create_account("mb-1", payload, "user-1")


# ------------------------------------------------------------------
# get_account
# ------------------------------------------------------------------


class TestGetAccount:

    def test_happy_path_returns_account(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)
        result = accounts_service.get_account("mb-1", "acc-1", "user-1")
        assert isinstance(result, AccountOut)

    def test_not_found_when_none(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=None)
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(AccountNotFound):
            accounts_service.get_account("mb-1", "acc-1", "user-1")

    def test_database_error_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(DatabaseQueryError):
            accounts_service.get_account("mb-1", "acc-1", "user-1")

    def test_generic_exception_raises_api_error(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(RuntimeError("boom"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(ApiError, match="Failed to look up account"):
            accounts_service.get_account("mb-1", "acc-1", "user-1")


# ------------------------------------------------------------------
# update_account
# ------------------------------------------------------------------


class TestUpdateAccount:

    def test_happy_path_updates_display_label(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountUpdate(display_label="renamed")
        result = accounts_service.update_account("mb-1", "acc-1", payload, "user-1")
        assert isinstance(result, AccountOut)
        assert result.display_label == "renamed"

    def test_not_found_when_none(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=None)
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountUpdate(display_label="x")
        with pytest.raises(AccountNotFound):
            accounts_service.update_account("mb-1", "acc-1", payload, "user-1")

    def test_database_error_on_get_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountUpdate(display_label="x")
        with pytest.raises(DatabaseQueryError):
            accounts_service.update_account("mb-1", "acc-1", payload, "user-1")

    def test_database_error_on_upsert_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        # get succeeds, upsert fails
        store = FakeAccountStoreRaising(QueryError("DB fail"), get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountUpdate(display_label="x")
        with pytest.raises(DatabaseQueryError):
            accounts_service.update_account("mb-1", "acc-1", payload, "user-1")

    def test_generic_exception_raises_api_error(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(RuntimeError("boom"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        payload = AccountUpdate(display_label="x")
        with pytest.raises(ApiError, match="Failed to look up account"):
            accounts_service.update_account("mb-1", "acc-1", payload, "user-1")


# ------------------------------------------------------------------
# delete_account
# ------------------------------------------------------------------


class TestDeleteAccount:

    def test_happy_path_returns_deleted_status(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)
        result = accounts_service.delete_account("mb-1", "acc-1", "user-1")
        assert result == {"status": "deleted"}

    def test_not_found_when_none(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=None)
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(AccountNotFound):
            accounts_service.delete_account("mb-1", "acc-1", "user-1")

    def test_database_error_on_get_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(DatabaseQueryError):
            accounts_service.delete_account("mb-1", "acc-1", "user-1")

    def test_database_error_on_delete_translated(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(QueryError("DB fail"), get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(DatabaseQueryError):
            accounts_service.delete_account("mb-1", "acc-1", "user-1")

    def test_generic_exception_raises_api_error(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStoreRaising(RuntimeError("boom"))
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(ApiError, match="Failed to look up account"):
            accounts_service.delete_account("mb-1", "acc-1", "user-1")


# ------------------------------------------------------------------
# start_account_connect / complete_account_connect
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_pending_registry():
    """The pending-connect registry is module-level state; isolate tests."""
    oauth_pending._pending.clear()
    yield
    oauth_pending._pending.clear()


class TestConnectAccountFlow:

    _MID = _FAKE_RECORD["mailbox_id"]
    _AID = _FAKE_RECORD["account_id"]

    def _patch_connect_deps(self, monkeypatch, *, store=None, auth_exc=None, auth_return=None):
        """Wire all connect-flow dependencies."""
        _patch_access(monkeypatch)
        if store is None:
            store = FakeAccountStore(get_return=_FAKE_RECORD)
        monkeypatch.setattr(accounts_service, "account_store", store)

        fake_creds = {"client_id": "fake", "client_secret": SecretStr("fake")}
        monkeypatch.setattr(
            accounts_service, "load_wrapped_app_credentials", lambda p: fake_creds,
        )
        monkeypatch.setattr(accounts_service, "unwrap_secret", lambda v: v)

        if auth_return is None:
            auth_return = {
                "access_token": "tok",
                "refresh_token": "ref",
                "email_address": "user@example.com",
            }

        def _build_manager(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id") or "")
                aid = str(acc.get("account_id") or "")
                label = f"{mid}__{aid}"
                manager.add_client(
                    FakeEmailClient(label, auth_exc=auth_exc, auth_return=auth_return)
                )
            return manager

        monkeypatch.setattr(accounts_service, "build_manager_for_accounts", _build_manager)
        return store

    def _start(self):
        return accounts_service.start_account_connect(self._MID, self._AID, "user-1")

    # ---- start ----

    def test_start_returns_authorization_url_and_registers_pending(self, monkeypatch):
        self._patch_connect_deps(monkeypatch)
        result = self._start()
        assert isinstance(result, AccountConnectStartResponse)
        assert result.authorization_url.startswith("https://")
        assert result.state == "fake-state"
        pending = oauth_pending._pending.get("fake-state")
        assert pending is not None
        assert pending.account_id == self._AID
        assert pending.user_id == "user-1"

    def test_start_not_found_when_none(self, monkeypatch):
        _patch_access(monkeypatch)
        store = FakeAccountStore(get_return=None)
        monkeypatch.setattr(accounts_service, "account_store", store)
        with pytest.raises(AccountNotFound):
            self._start()

    def test_start_core_error_translated(self, monkeypatch):
        self._patch_connect_deps(monkeypatch, auth_exc=EmailAuthError("token rejected"))
        with pytest.raises(AccountConnectAuthError):
            self._start()

    def test_start_generic_exception_wrapped_by_manager(self, monkeypatch):
        self._patch_connect_deps(monkeypatch, auth_exc=RuntimeError("crash"))
        with pytest.raises(ExternalAPIError, match="Unexpected begin_connect error"):
            self._start()

    # ---- complete ----

    def test_complete_happy_path_persists_tokens(self, monkeypatch):
        store = self._patch_connect_deps(monkeypatch)
        start = self._start()

        result = accounts_service.complete_account_connect(start.state, "auth-code", None, None)

        assert result["ok"] is True
        assert result["provider"] == "gmail"
        assert len(store.upserted_tokens) == 1
        _, _, provider, payload = store.upserted_tokens[0]
        assert provider == "gmail"
        assert payload["access_token"] == "tok"
        assert payload["email_address"] == "user@example.com"
        # single-use: the pending entry is consumed
        assert oauth_pending._pending == {}

    def test_complete_unknown_state_reports_expired(self, monkeypatch):
        self._patch_connect_deps(monkeypatch)
        result = accounts_service.complete_account_connect("nope", "auth-code", None, None)
        assert result["ok"] is False
        assert "unknown or expired" in result["message"]

    def test_complete_provider_denied_reports_error_without_exchange(self, monkeypatch):
        store = self._patch_connect_deps(monkeypatch)
        start = self._start()
        result = accounts_service.complete_account_connect(
            start.state, None, "access_denied", "User cancelled",
        )
        assert result["ok"] is False
        assert "access_denied" in result["message"]
        assert store.upserted_tokens == []

    def test_complete_malicious_provider_error_is_sanitised(self, monkeypatch):
        store = self._patch_connect_deps(monkeypatch)
        start = self._start()
        result = accounts_service.complete_account_connect(
            start.state,
            None,
            "</script><script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
        )
        assert result["ok"] is False
        # Raw attacker-controlled error/description never reach the page message.
        assert "<script>" not in result["message"]
        assert "</script>" not in result["message"]
        assert "onerror" not in result["message"]
        assert "unknown_error" in result["message"]
        assert store.upserted_tokens == []

    def test_complete_missing_code_reports_error(self, monkeypatch):
        self._patch_connect_deps(monkeypatch)
        start = self._start()
        result = accounts_service.complete_account_connect(start.state, "", None, None)
        assert result["ok"] is False
        assert "did not include a code" in result["message"]

    def test_complete_core_error_reports_translated_message(self, monkeypatch):
        self._patch_connect_deps(monkeypatch)
        start = self._start()
        # Make the exchange fail: rebuild managers whose client raises.
        self._patch_connect_deps(monkeypatch, auth_exc=EmailAuthError("token rejected"))
        result = accounts_service.complete_account_connect(start.state, "auth-code", None, None)
        assert result["ok"] is False
        assert "token rejected" in result["message"]

    def test_complete_upsert_failure_reports_error(self, monkeypatch):
        store = self._patch_connect_deps(monkeypatch)
        start = self._start()

        def _fail(*args, **kwargs):
            raise QueryError("DB fail")

        monkeypatch.setattr(store, "upsert_tokens", _fail)
        result = accounts_service.complete_account_connect(start.state, "auth-code", None, None)
        assert result["ok"] is False
        assert "persist tokens" in result["message"]

    def test_complete_account_gone_reports_error(self, monkeypatch):
        store = self._patch_connect_deps(monkeypatch)
        start = self._start()
        store._get_return = None
        result = accounts_service.complete_account_connect(start.state, "auth-code", None, None)
        assert result["ok"] is False
        assert "no longer exists" in result["message"]
