"""Tests espejo de ``emails_service.papelera``: manage_trash y move_to_trash."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    EmailNotInTrash,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, papelera
from core.email import EmailManager
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient, build_metadata

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


def _patch_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for papelera tests."""
    monkeypatch.setattr(
        papelera, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        papelera.account_store, "list_by_mailbox",
        lambda _mb: [_fake_account()],
    )
    monkeypatch.setattr(
        papelera.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
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
        papelera.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    kwargs = fake_client_kwargs or {}

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                metadata=[build_metadata()],
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(papelera, "build_manager_for_accounts", _build)


class TestManageTrash:

    def _make_payload(self, action="delete", items=None):
        from api.schemas.email import TrashActionRequest, TrashItem
        if items is None:
            items = [TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID)]
        return TrashActionRequest(action=action, items=items)

    def _patch_trash_common(self, monkeypatch, *, fake_client_kwargs=None, previous_box="ALL_MAIL"):
        _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)
        monkeypatch.setattr(
            papelera, "get_trash_emails_by_ids",
            lambda _aid, _ids, **_kw: [
                {"provider_message_id": mid, "box": "TRASH", "previous_box": previous_box}
                for mid in _ids
            ],
        )
        monkeypatch.setattr(
            papelera, "mark_as_deleted_batch",
            lambda _aid, _ids, **_kw: len(_ids),
        )
        monkeypatch.setattr(
            papelera, "restore_from_trash_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )
        monkeypatch.setattr(
            papelera, "restore_from_trash_discovered_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )

    def test_delete_happy_path(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        result = papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
        )
        assert result.affected == 1

    def test_restore_happy_path(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        result = papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert result.affected == 1

    def test_delete_marks_deleted_in_db(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        mark_calls = []
        monkeypatch.setattr(
            papelera, "mark_as_deleted_batch",
            lambda aid, ids, **_kw: (mark_calls.append((aid, ids)), len(ids))[1],
        )
        papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
        )
        assert len(mark_calls) == 1
        assert mark_calls[0][0] == _ACCOUNT_ID
        assert "m1" in mark_calls[0][1]

    def test_restore_calls_restore_batch(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        restore_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert len(restore_calls) == 1

    def test_partial_provider_failure_only_updates_succeeded(self, monkeypatch):
        """Provider-first: if provider only deletes 1 of 2, DB only marks 1."""
        from api.schemas.email import TrashItem
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "delete_return": ["m1"],  # only m1 succeeds
        })
        payload = self._make_payload("delete", items=[
            TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID),
            TrashItem(provider_message_id="m2", account_id=_ACCOUNT_ID),
        ])
        result = papelera.manage_trash(_MAILBOX_ID, payload, _USER_ID)
        assert result.affected == 1

    def test_account_not_in_mailbox_raises(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        from api.schemas.email import TrashItem
        payload = self._make_payload("delete", items=[
            TrashItem(provider_message_id="m1", account_id="nonexistent"),
        ])
        with pytest.raises(AccountNotFound):
            papelera.manage_trash(_MAILBOX_ID, payload, _USER_ID)

    def test_email_not_in_trash_raises(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        monkeypatch.setattr(
            papelera, "get_trash_emails_by_ids",
            lambda _aid, _ids, **_kw: [],  # no emails found in trash
        )
        with pytest.raises(EmailNotInTrash):
            papelera.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            papelera.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "delete_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            papelera.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_generic_exception_raises_external_api_error(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "delete_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            papelera.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_restore_null_previous_box_calls_discovered_batch(self, monkeypatch):
        """When previous_box is None, restore uses fetch_messages_metadata + discovered batch."""
        discovered_meta = build_metadata(provider_message_id="m1", box="SENT")
        self._patch_trash_common(
            monkeypatch,
            fake_client_kwargs={"fetch_messages_metadata_return": [discovered_meta]},
            previous_box=None,
        )
        discovered_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        restore_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        result = papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert result.affected == 1
        assert len(discovered_calls) == 1
        assert discovered_calls[0][1][0][3] == "SENT"
        assert len(restore_calls) == 0

    def test_restore_known_previous_box_uses_normal_batch(self, monkeypatch):
        """When previous_box is known, normal restore_from_trash_batch is used."""
        self._patch_trash_common(monkeypatch, previous_box="SPAM")
        restore_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        discovered_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        result = papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert result.affected == 1
        assert len(restore_calls) == 1
        assert len(discovered_calls) == 0

    def test_restore_null_previous_box_defaults_to_all_mail_on_fetch_miss(self, monkeypatch):
        """When fetch_messages_metadata returns nothing, discovered box defaults to ALL_MAIL."""
        self._patch_trash_common(
            monkeypatch,
            fake_client_kwargs={"fetch_messages_metadata_return": []},
            previous_box=None,
        )
        discovered_calls = []
        monkeypatch.setattr(
            papelera, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        papelera.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert len(discovered_calls) == 1
        assert discovered_calls[0][1][0][3] == "ALL_MAIL"


class TestMoveToTrash:

    def _make_payload(self, items=None):
        from api.schemas.email import MoveToTrashRequest, TrashItem
        if items is None:
            items = [TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID)]
        return MoveToTrashRequest(items=items)

    def _patch_move_common(self, monkeypatch, *, fake_client_kwargs=None):
        _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)
        monkeypatch.setattr(
            papelera, "move_to_trash_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )

    def test_happy_path(self, monkeypatch):
        self._patch_move_common(monkeypatch)
        result = papelera.move_to_trash(
            _MAILBOX_ID, self._make_payload(), _USER_ID,
        )
        assert result.affected == 1

    def test_provider_partial_failure_only_updates_succeeded(self, monkeypatch):
        """Provider-first: if provider only trashes 1 of 2, DB only updates 1."""
        from api.schemas.email import TrashItem
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_return": {"m1": "m1"},  # only m1 succeeds
        })
        payload = self._make_payload(items=[
            TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID),
            TrashItem(provider_message_id="m2", account_id=_ACCOUNT_ID),
        ])
        result = papelera.move_to_trash(_MAILBOX_ID, payload, _USER_ID)
        assert result.affected == 1

    def test_all_provider_failure_returns_zero(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_return": {},
        })
        result = papelera.move_to_trash(
            _MAILBOX_ID, self._make_payload(), _USER_ID,
        )
        assert result.affected == 0

    def test_account_not_in_mailbox_raises(self, monkeypatch):
        self._patch_move_common(monkeypatch)
        from api.schemas.email import TrashItem
        payload = self._make_payload(items=[
            TrashItem(provider_message_id="m1", account_id="nonexistent"),
        ])
        with pytest.raises(AccountNotFound):
            papelera.move_to_trash(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            papelera.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            papelera.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )

    def test_generic_exception_raises_external_api_error(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            papelera.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )
