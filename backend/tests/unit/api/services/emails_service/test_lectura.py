"""Tests espejo de ``emails_service.lectura``: update_read_status."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    ExternalAPIError,
)
from api.schemas.email import ReadStatusItem, ReadStatusRequest
from api.services.emails_service import _comunes, lectura
from core.email import EmailManager
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient

from ._helpers import (
    _ACCOUNT_ID,
    _ACCOUNT_ID_2,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


def _patch_read_status(monkeypatch, *, accounts=None, fake_client_kwargs=None):
    """Apply monkeypatches specific to update_read_status tests."""
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        lectura, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        lectura.account_store, "list_by_mailbox",
        lambda _mb: accounts,
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
        lectura.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        lectura, "update_email_read_status_batch",
        lambda _aid, _ids, _read, **_kw: len(_ids),
    )
    monkeypatch.setattr(
        lectura, "update_email_read_status_by_thread",
        lambda _aid, _ids, _read, **_kw: len(_ids),
    )

    kwargs = fake_client_kwargs or {}

    def _build(accs):
        manager = EmailManager()
        for acc in accs:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(lectura, "build_manager_for_accounts", _build)


class TestUpdateReadStatus:

    @staticmethod
    def _make_payload(items, is_read=True, propagate_thread=False):
        return ReadStatusRequest(
            is_read=is_read,
            items=[ReadStatusItem(account_id=aid, provider_message_id=mid)
                   for aid, mid in items],
            propagate_thread=propagate_thread,
        )

    def test_happy_path_single_account(self, monkeypatch):
        _patch_read_status(monkeypatch)
        payload = self._make_payload([
            (_ACCOUNT_ID, "m1"),
            (_ACCOUNT_ID, "m2"),
        ], is_read=True)
        result = lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].updated == 2

    def test_default_marks_by_id_not_thread(self, monkeypatch):
        # Per-message surfaces (default propagate_thread=False) mark only the
        # ids sent, via the per-id batch helper — never the by-thread one.
        _patch_read_status(monkeypatch)
        calls = {"batch": [], "by_thread": []}
        monkeypatch.setattr(
            lectura, "update_email_read_status_batch",
            lambda _aid, ids, _read, **_kw: (calls["batch"].append(list(ids)), len(ids))[1],
        )
        monkeypatch.setattr(
            lectura, "update_email_read_status_by_thread",
            lambda _aid, ids, _read, **_kw: (calls["by_thread"].append(list(ids)), len(ids))[1],
        )
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert calls["batch"] == [["m1"]]
        assert calls["by_thread"] == []

    def test_propagate_thread_uses_by_thread_helper(self, monkeypatch):
        # Conversation viewer sends propagate_thread=True → the DB update must
        # route through the by-thread helper so every duplicate Outlook row of
        # the thread flips, not just the sent id.
        _patch_read_status(monkeypatch)
        calls = {"batch": [], "by_thread": []}
        monkeypatch.setattr(
            lectura, "update_email_read_status_batch",
            lambda _aid, ids, _read, **_kw: (calls["batch"].append(list(ids)), len(ids))[1],
        )
        monkeypatch.setattr(
            lectura, "update_email_read_status_by_thread",
            lambda _aid, ids, _read, **_kw: (calls["by_thread"].append(list(ids)), len(ids))[1],
        )
        payload = self._make_payload([(_ACCOUNT_ID, "m1")], propagate_thread=True)
        lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert calls["by_thread"] == [["m1"]]
        assert calls["batch"] == []

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [
            _fake_account(_ACCOUNT_ID),
            _fake_account(_ACCOUNT_ID_2),
        ]
        _patch_read_status(monkeypatch, accounts=accounts)
        payload = self._make_payload([
            (_ACCOUNT_ID, "m1"),
            (_ACCOUNT_ID_2, "m2"),
        ], is_read=False)
        result = lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 2
        assert len(result.accounts) == 2
        returned_aids = {d.account_id for d in result.accounts}
        assert _ACCOUNT_ID in returned_aids
        assert _ACCOUNT_ID_2 in returned_aids

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_read_status(monkeypatch)
        payload = self._make_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "update_read_status_exc": EmailExternalAPIError("API fail"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "update_read_status_exc": RuntimeError("unexpected"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_only_for_updated_ids(self, monkeypatch):
        """FakeEmailClient returns all IDs by default; override to return subset."""
        accounts = [_fake_account()]
        monkeypatch.setattr(
            lectura, "ensure_mailbox_access",
            lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
        )
        monkeypatch.setattr(
            lectura.account_store, "list_by_mailbox",
            lambda _mb: accounts,
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
            lectura.account_store, "upsert_tokens",
            lambda *_a, **_kw: None,
        )

        # Build a manager whose FakeEmailClient returns only "m1" (not "m2")
        def _build_partial(accs):
            manager = EmailManager()
            for acc in accs:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"

                class _PartialFake(FakeEmailClient):
                    def update_read_status(self, message_ids, is_read):
                        return ["m1"]  # only m1 succeeded

                manager.add_client(_PartialFake(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                ))
            return manager

        monkeypatch.setattr(lectura, "build_manager_for_accounts", _build_partial)

        db_calls = []
        monkeypatch.setattr(
            lectura, "update_email_read_status_batch",
            lambda aid, ids, is_read, **_kw: (db_calls.append((aid, list(ids), is_read)), len(ids))[1],
        )

        payload = self._make_payload([
            (_ACCOUNT_ID, "m1"),
            (_ACCOUNT_ID, "m2"),
        ], is_read=True)
        result = lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 1
        assert len(db_calls) == 1
        assert db_calls[0][1] == ["m1"]

    def test_persists_refreshed_tokens(self, monkeypatch):
        accounts = [_fake_account()]
        monkeypatch.setattr(
            lectura, "ensure_mailbox_access",
            lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
        )
        monkeypatch.setattr(
            lectura.account_store, "list_by_mailbox",
            lambda _mb: accounts,
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
            lectura, "update_email_read_status_batch",
            lambda _aid, _ids, _read, **_kw: len(_ids),
        )

        upsert_calls = []
        monkeypatch.setattr(
            lectura.account_store, "upsert_tokens",
            lambda *args, **kwargs: upsert_calls.append(args),
        )

        def _build_refreshing(accs):
            manager = EmailManager()
            for acc in accs:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_silent_return={"access_token": "new_tok", "refresh_token": "new_ref"},
                ))
            return manager

        monkeypatch.setattr(lectura, "build_manager_for_accounts", _build_refreshing)

        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        lectura.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert len(upsert_calls) >= 1
