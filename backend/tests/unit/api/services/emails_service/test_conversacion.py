"""Tests espejo de ``emails_service.conversacion``: get_conversation y mapeo de error 502."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    ConversationFetchError,
    EmailNotFound,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, conversacion
from core.email import EmailManager
from core.email.errors import EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient, build_conversation_message

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


_CONVERSATION_BASE_ROW = {
    "provider_message_id": "m_base",
    "account_id": _ACCOUNT_ID,
    "mailbox_id": _MAILBOX_ID,
    "thread_id": "thr-1",
    "from_email": "sender@test.com",
    "from_name": "Sender",
    "subject": "Hello",
    "received_at": "2025-01-15T10:00:00+00:00",
    "is_read": False,
    "box": "ALL_MAIL",
}


def test_conversation_message_maps_is_favorite_through():
    # is_favorite must survive the ConversationMessage → EmailMetadata mapping:
    # the metadata upsert now persists it, so dropping it (the old behaviour)
    # would silently un-favourite the row when the viewer completes the mailbox.
    result = conversacion._conversation_message_to_metadata(
        build_conversation_message(is_favorite=True), _ACCOUNT_ID,
    )
    assert result.is_favorite is True
    assert result.account_id == _ACCOUNT_ID


def _patch_get_conversation_common(
    monkeypatch,
    *,
    base_row="default",
    fake_client_kwargs=None,
    persist_exc=None,
    persist_calls=None,
):
    """Common monkeypatches for ``get_conversation`` tests.

    Patches the base-message read (``get_metadata``) and the lazy-sync
    persist helper (``persist_email_metadata_batch``, recording into
    ``persist_calls`` when given) — the dependency set of the conversation
    path, narrower than ``_patch_common``.
    """
    monkeypatch.setattr(
        conversacion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        conversacion.account_store, "get",
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
        conversacion.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    row = _CONVERSATION_BASE_ROW if base_row == "default" else base_row
    monkeypatch.setattr(
        conversacion.email_metadata_store, "get_metadata",
        lambda _aid, _mid: row,
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
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(conversacion, "build_manager_for_accounts", _build)

    if persist_exc is not None:
        def _persist(_aid, _meta, **_kw):
            raise persist_exc
        monkeypatch.setattr(conversacion, "persist_email_metadata_batch", _persist)
    else:
        def _persist(_aid, _meta, **_kw):
            if persist_calls is not None:
                persist_calls.append((_aid, list(_meta)))
            return len(_meta)
        monkeypatch.setattr(conversacion, "persist_email_metadata_batch", _persist)


class TestGetConversation:

    def test_threadless_base_maps_singleton_without_provider_call(self, monkeypatch):
        # thread_id='' → single-message conversation mapped from the base row
        # already read; the provider path is never entered.
        base = dict(_CONVERSATION_BASE_ROW, thread_id="")
        _patch_get_conversation_common(monkeypatch, base_row=base)
        # A manager build would mean the provider branch was reached — make it
        # explode so the singleton short-circuit is proven.
        def _explode(_accounts):
            raise AssertionError("manager must not be built for a threadless base")
        monkeypatch.setattr(conversacion, "build_manager_for_accounts", _explode)

        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == ""
        assert len(result.messages) == 1
        # The singleton is mapped from the base row via row_to_email_metadata_out.
        assert result.messages[0].provider_message_id == "m_base"
        assert result.messages[0].mailbox_id == _MAILBOX_ID

    def test_happy_path_orders_ascending_and_returns_conversation_out(self, monkeypatch):
        # Provider returns members out of order; the response is sorted
        # oldest-first and mapped from the FRESH provider state.
        members = [
            build_conversation_message(
                provider_message_id="m_new", thread_id="thr-1",
                received_at=datetime(2025, 1, 2, 9, 0), box="SENT", is_favorite=True,
            ),
            build_conversation_message(
                provider_message_id="m_old", thread_id="thr-1",
                received_at=datetime(2025, 1, 1, 9, 0), box="ALL_MAIL",
            ),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
        )
        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == "thr-1"
        assert [m.provider_message_id for m in result.messages] == ["m_old", "m_new"]
        # Per-message state comes from the provider members; account/mailbox
        # are stamped from the resolved account; has_attachments is B.lazy.
        m_new = result.messages[1]
        assert m_new.box == "SENT"
        assert m_new.is_favorite is True
        assert m_new.account_id == _ACCOUNT_ID
        assert m_new.mailbox_id == _MAILBOX_ID
        assert all(m.has_attachments is False for m in result.messages)

    def test_lazy_sync_upserts_members_with_is_favorite(self, monkeypatch):
        persist_calls: list = []
        members = [
            build_conversation_message(provider_message_id="m_fav", is_favorite=True),
            build_conversation_message(provider_message_id="m_plain", is_favorite=False),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
        )
        conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        # The lazy-sync upserts every member with its provider-fresh is_favorite
        # (the shared upsert is now provider-authoritative for favourites — no
        # separate re-apply): m_fav → True, m_plain → False.
        persisted = {m.provider_message_id: m for call in persist_calls for m in call[1]}
        assert persisted["m_fav"].is_favorite is True
        assert persisted["m_plain"].is_favorite is False

    def test_persist_failure_is_best_effort(self, monkeypatch):
        # A lazy-sync persist failure must NOT abort the viewer response.
        members = [build_conversation_message(provider_message_id="m1")]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_exc=RuntimeError("db write failed"),
        )
        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == "thr-1"
        assert [m.provider_message_id for m in result.messages] == ["m1"]

    def test_email_not_found_when_base_row_missing(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch, base_row=None)
        with pytest.raises(EmailNotFound):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", _USER_ID,
            )

    def test_account_not_found(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            conversacion.get_conversation(
                _MAILBOX_ID, "nonexistent", "m_base", _USER_ID,
            )

    def test_provider_external_error_translated_to_external_api_error(self, monkeypatch):
        # A genuine provider failure (EmailExternalAPIError) surfaces as
        # ExternalAPIError (502) via translate_core_error — NOT the
        # ConversationFetchError fallback.
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_conversation_exc": EmailExternalAPIError("thread fetch failed"),
            },
        )
        with pytest.raises(ExternalAPIError):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_get_metadata_database_error_translated(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        from api.errors.exceptions import DatabaseQueryError
        _patch_get_conversation_common(monkeypatch)

        def _raise(_aid, _mid):
            raise DbQueryError("get_metadata fail")
        monkeypatch.setattr(
            conversacion.email_metadata_store, "get_metadata", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_ownership_checked_first(self, monkeypatch):
        from api.errors.exceptions import Forbidden
        _patch_get_conversation_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Foreign mailbox in conversation ownership test.")
        monkeypatch.setattr(conversacion, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )


def test_conversation_fetch_error_maps_to_502():
    # Lock the _STATUS_MAP registration: ConversationFetchError → 502, same
    # family as EmailContentFetchError / EmailReplyContextError.
    from fastapi import status
    from api.errors.handlers import _STATUS_MAP
    assert _STATUS_MAP[ConversationFetchError] == status.HTTP_502_BAD_GATEWAY
