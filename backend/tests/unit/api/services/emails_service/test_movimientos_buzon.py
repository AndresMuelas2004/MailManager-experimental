"""Tests espejo de ``emails_service.movimientos_buzon``: move/restore spam y archive."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    ExternalAPIError,
)
from api.schemas.email import (
    ArchiveItem,
    ArchiveRequest,
    SpamItem,
    SpamRequest,
)
from api.services.emails_service import _comunes, movimientos_buzon
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


def _patch_spam(monkeypatch, *, accounts=None, fake_client_kwargs=None):
    """Apply monkeypatches specific to spam move/restore tests."""
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        movimientos_buzon, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        movimientos_buzon.account_store, "list_by_mailbox",
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
        movimientos_buzon.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        movimientos_buzon, "update_email_spam_status_batch",
        lambda _aid, _results, _box, **_kw: len(_results),
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

    monkeypatch.setattr(movimientos_buzon, "build_manager_for_accounts", _build)


def _spam_payload(items):
    return SpamRequest(
        items=[SpamItem(account_id=aid, provider_message_id=mid)
               for aid, mid in items],
    )


class TestMoveToSpam:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_spam_exc": EmailExternalAPIError("API fail"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_spam_exc": RuntimeError("unexpected"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_spam_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            movimientos_buzon, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        movimientos_buzon.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "SPAM"


class TestRestoreFromSpam:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_spam_exc": EmailExternalAPIError("API fail"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_spam_exc": RuntimeError("unexpected"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_all_mail_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            movimientos_buzon, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        movimientos_buzon.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ALL_MAIL"


def _archive_payload(items):
    return ArchiveRequest(
        items=[ArchiveItem(account_id=aid, provider_message_id=mid)
               for aid, mid in items],
    )


class TestMoveToArchive:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_archive_exc": EmailExternalAPIError("API fail"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        # A RuntimeError from the client is wrapped by EmailManager into
        # EmailExternalAPIError (a CoreError), which the engine translates to
        # ExternalAPIError — mirroring the spam path exactly.
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_archive_exc": RuntimeError("unexpected"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_archive_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            movimientos_buzon, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        movimientos_buzon.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ARCHIVE"


class TestRestoreFromArchive:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = movimientos_buzon.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            movimientos_buzon.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_archive_exc": EmailExternalAPIError("API fail"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_archive_exc": RuntimeError("unexpected"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            movimientos_buzon.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_all_mail_box(self, monkeypatch):
        # Unarchiving restores the message to the real inbox (ALL_MAIL).
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            movimientos_buzon, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        movimientos_buzon.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ALL_MAIL"
