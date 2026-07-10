"""Tests espejo de ``drafts_service.sincronizacion``: sync_drafts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    DatabaseQueryError,
    DraftSyncError,
    ExternalAPIError,
    Forbidden,
)
from api.services.drafts_service import _comunes, sincronizacion
from core.email import DraftMetadata, EmailManager
from core.email.errors import (
    EmailAuthError,
    EmailExternalAPIError,
)
from database.errors import QueryError as DbQueryError
from tests.shared.email_fakes import FakeEmailClient

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _PROVIDER,
    _USER_ID,
    _fake_account,
)


# =====================================================================
# TestSyncDrafts — unit tests for drafts_service.sync_drafts
# =====================================================================

_DRAFT_TS = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _sample_draft(provider_draft_id: str = "d1", subject: str = "S") -> DraftMetadata:
    return DraftMetadata(
        provider_draft_id=provider_draft_id,
        to_recipients=["to@example.com"],
        cc_recipients=[],
        bcc_recipients=[],
        subject=subject,
        body="hi",
        created_at=_DRAFT_TS,
        updated_at=_DRAFT_TS,
    )


def _patch_sync_common(
    monkeypatch,
    *,
    fake_client_kwargs=None,
    accounts=None,
):
    """Common monkeypatches for sync_drafts tests.

    Builds an EmailManager with one FakeEmailClient per account. Captures
    replace_all_for_account invocations in a list returned at the end.
    """
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        sincronizacion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        sincronizacion.account_store, "get",
        lambda _mb, _aid: next(
            (a for a in accounts if a["account_id"] == _aid), None,
        ),
    )
    monkeypatch.setattr(
        sincronizacion.account_store, "list_by_mailbox",
        lambda _mb: list(accounts),
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        sincronizacion.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    kwargs = fake_client_kwargs or {}

    def _build(accounts_list):
        manager = EmailManager()
        for acc in accounts_list:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            default_return = [_sample_draft(provider_draft_id=f"d_{aid}")]
            call_kwargs = {
                "auth_return": {"access_token": "tok", "refresh_token": "ref"},
                "fetch_drafts_return": default_return,
                **kwargs,
            }
            manager.add_client(FakeEmailClient(label, **call_kwargs))
        return manager

    monkeypatch.setattr(sincronizacion, "build_manager_for_accounts", _build)

    replace_calls: list[tuple[str, list[dict]]] = []

    def _replace(account_id, drafts_list):
        replace_calls.append((account_id, list(drafts_list)))
        return len(drafts_list)

    monkeypatch.setattr(
        sincronizacion.draft_store, "replace_all_for_account", _replace,
    )
    return replace_calls


class TestSyncDrafts:

    def test_sync_single_account_happy_path(self, monkeypatch):
        calls = _patch_sync_common(monkeypatch)
        result = sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert isinstance(result.total_synced, int)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].provider == _PROVIDER
        assert result.accounts[0].drafts_synced == 1
        assert len(calls) == 1
        assert calls[0][0] == _ACCOUNT_ID

    def test_sync_mailbox_happy_path(self, monkeypatch):
        accounts = [
            _fake_account(account_id="acc-gmail", provider="gmail"),
            _fake_account(account_id="acc-outlook", provider="outlook"),
        ]
        calls = _patch_sync_common(monkeypatch, accounts=accounts)
        result = sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result.total_synced == 2
        assert len(result.accounts) == 2
        account_ids = {a.account_id for a in result.accounts}
        assert account_ids == {"acc-gmail", "acc-outlook"}
        providers = {a.provider for a in result.accounts}
        assert providers == {"gmail", "outlook"}
        assert len(calls) == 2

    def test_sync_account_not_found_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_mailbox_access_denied_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(sincronizacion, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_sync_db_error_on_account_lookup_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(sincronizacion.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_db_error_on_list_by_mailbox_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("list failed")

        monkeypatch.setattr(sincronizacion.account_store, "list_by_mailbox", _raise_db)
        with pytest.raises(DatabaseQueryError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_sync_provider_external_api_error_translated(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": EmailExternalAPIError("Provider down"),
        })
        with pytest.raises(ExternalAPIError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("Token expired"),
        })
        with pytest.raises(AccountNotConnected):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_db_error_on_replace_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("persist failed")

        monkeypatch.setattr(
            sincronizacion.draft_store, "replace_all_for_account", _raise_db,
        )
        with pytest.raises(DatabaseQueryError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_unexpected_error_on_replace_raises_draft_sync_error(
        self, monkeypatch,
    ):
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            sincronizacion.draft_store, "replace_all_for_account", _raise,
        )
        with pytest.raises(DraftSyncError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_empty_accounts_returns_zero(self, monkeypatch):
        _patch_sync_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            lambda _mb: [],
        )
        result = sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result.total_synced == 0
        assert result.accounts == []

    def test_sync_rows_include_all_draft_fields(self, monkeypatch):
        """The rows passed to replace_all_for_account carry every DraftMetadata field."""
        calls = _patch_sync_common(monkeypatch)
        sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(calls) == 1
        _, rows = calls[0]
        assert len(rows) == 1
        row = rows[0]
        assert row["provider_draft_id"] == f"d_{_ACCOUNT_ID}"
        assert row["to_recipients"] == ["to@example.com"]
        assert row["cc_recipients"] == []
        assert row["bcc_recipients"] == []
        assert row["subject"] == "S"
        assert row["body"] == "hi"
        assert row["created_at"] == _DRAFT_TS
        assert row["updated_at"] == _DRAFT_TS

    def test_sync_persist_refreshed_tokens_happy_path(self, monkeypatch):
        # When authenticate_silent returns refreshed tokens, _persist_refreshed_tokens
        # must call account_store.upsert_tokens with the unwrapped values.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            sincronizacion.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_sync_persist_refreshed_tokens_db_error_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(sincronizacion.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_persist_refreshed_tokens_unexpected_raises_draft_sync_error(
        self, monkeypatch,
    ):
        # After Bloque 3 refactor, _persist_refreshed_tokens accepts a `fallback`
        # parameter and sync_drafts passes DraftSyncError — so a plain RuntimeError
        # from upsert_tokens must surface as DraftSyncError (not DraftCreationError).
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(sincronizacion.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftSyncError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_fetch_all_drafts_core_error_translated(self, monkeypatch):
        # fetch_all_drafts captures the error in _last_errors; the downstream
        # raise_on_silent_auth_errors call translates it to ExternalAPIError.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": EmailExternalAPIError("Provider 502"),
        })
        with pytest.raises(ExternalAPIError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_fetch_all_drafts_generic_exception_raises_draft_sync_error(
        self, monkeypatch,
    ):
        # A non-CoreError captured in _last_errors is surfaced by
        # translate_core_error via the fallback (DraftSyncError for sync_drafts).
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(DraftSyncError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_provider_runtime_error_raises_draft_sync_error(self, monkeypatch):
        # Documents the asymmetric behavior vs. create_draft: a plain RuntimeError
        # from FakeEmailClient.fetch_drafts is captured in _last_errors by
        # EmailManager.fetch_all_drafts (not wrapped into EmailExternalAPIError
        # like send_email does) and surfaces as DraftSyncError via the fallback.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": RuntimeError("boom"),
        })
        with pytest.raises(DraftSyncError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_build_auth_context_error_raises_draft_sync_error(self, monkeypatch):
        # Validates Bloque 1.3 fix: _build_draft_auth_context + build_manager_for_accounts
        # must be inside the outer try block so plain exceptions from those helpers
        # are caught and re-raised as DraftSyncError.
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(_comunes, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftSyncError):
            sincronizacion.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
