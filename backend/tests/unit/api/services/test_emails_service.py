"""
Unit tests for emails_service.sync_email_metadata, emails_service.send_email,
emails_service.update_read_status, emails_service.move_to_spam,
emails_service.restore_from_spam, and emails_service.get_email_full_content.

All external dependencies are monkeypatched so tests run without DB or provider APIs.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    ConversationFetchError,
    EmailContentFetchError,
    EmailFetchError,
    EmailListError,
    EmailNotFound,
    EmailNotInTrash,
    EmailSendError,
    ExternalAPIError,
    MailboxNotFound,
    MoveToTrashError,
    ReadStatusUpdateError,
    SpamMoveError,
    SpamRestoreError,
    TrashOperationError,
    UnreadCountError,
)
from api.schemas.email import (
    ArchiveItem,
    ArchiveRequest,
    ReadStatusItem,
    ReadStatusRequest,
    SpamItem,
    SpamRequest,
)
# Note: EmailExternalAPIError maps to ExternalAPIError via _CORE_TO_API_MAP,
# while generic (non-CoreError) exceptions fall back to the caller-specified fallback.
from api.services import emails_service
from core.email import EmailContent, EmailManager, SyncResult
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import (
    FakeEmailClient,
    build_conversation_message,
    build_metadata,
)


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_PROVIDER = "gmail"
_LABEL = f"{_MAILBOX_ID}__{_ACCOUNT_ID}"


def _fake_account(account_id=_ACCOUNT_ID, provider=_PROVIDER) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": _MAILBOX_ID,
        "provider": provider,
        "display_label": f"{provider}:{account_id}",
    }


def _patch_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for emails_service tests."""
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "list_by_mailbox",
        lambda _mb: [_fake_account()],
    )
    monkeypatch.setattr(
        emails_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
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

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)

    # Stub persistence helpers
    monkeypatch.setattr(emails_service, "persist_email_metadata_batch", lambda _aid, _meta, **_kw: len(_meta))
    monkeypatch.setattr(emails_service, "delete_email_metadata_batch", lambda _aid, _ids, **_kw: len(_ids))
    monkeypatch.setattr(emails_service, "update_email_metadata_labels_batch", lambda _aid, _lu, **_kw: len(_lu))
    monkeypatch.setattr(emails_service, "load_sync_cursors", lambda _lookup, **_kw: {})
    monkeypatch.setattr(emails_service, "update_sync_cursor", lambda _mb, _acc, _cur, **_kw: None)


# ==================================================================
# sync_email_metadata
# ==================================================================


class TestSyncEmailMetadata:

    def test_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID

    def test_background_tasks_none_schedules_no_prefetch(self, monkeypatch):
        """Direct callers (service tests, scripts) omit ``background_tasks``;
        the post-response prefetch/purge is a best-effort optimisation and must
        simply be skipped when absent, leaving the sync contract unchanged."""
        _patch_common(monkeypatch)
        ran = []
        monkeypatch.setattr(
            emails_service, "_run_content_prefetch_and_purge",
            lambda *a, **kw: ran.append(a),
        )
        # No background_tasks kwarg → nothing scheduled, no prefetch run.
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced == 1
        assert ran == []

    def test_background_tasks_schedules_prefetch_for_synced_accounts(self, monkeypatch):
        """When the router injects ``BackgroundTasks``, the service registers the
        prefetch/purge job with the account_label + account_id of every synced
        account (``label`` is already the account_label — never reconstructed)."""
        _patch_common(monkeypatch)
        added = []

        class _FakeBackgroundTasks:
            def add_task(self, fn, *args):
                added.append((fn, args))

        bt = _FakeBackgroundTasks()
        emails_service.sync_email_metadata(
            _MAILBOX_ID, _USER_ID, background_tasks=bt,
        )
        assert len(added) == 1
        fn, args = added[0]
        assert fn is emails_service._run_content_prefetch_and_purge
        # args = (manager, prefetch_targets, synced_account_ids)
        _manager, targets, account_ids = args
        assert targets == [(_LABEL, _ACCOUNT_ID)]
        assert account_ids == [_ACCOUNT_ID]

    def test_empty_accounts_returns_zero(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.account_store, "list_by_mailbox",
            lambda _mb: [],
        )

        def _build_empty(accounts):
            return EmailManager()

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_empty)
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced == 0

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_fetch_core_error_uses_centralized_mapping(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "fetch_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_fetch_generic_exception_raises_email_fetch_error(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "fetch_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(EmailFetchError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_persists_refreshed_tokens(self, monkeypatch):
        _patch_common(monkeypatch)
        upsert_calls = []
        monkeypatch.setattr(
            emails_service.account_store, "upsert_tokens",
            lambda *args, **kwargs: upsert_calls.append(args),
        )
        # FakeEmailClient returns auth_return by default on authenticate_silent,
        # so manager.authenticate_all_silent will return updated tokens
        # only when the client returns non-None from authenticate_silent.
        # For this test, we use auth_silent_return to force token refresh.

        def _build_refreshing(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    metadata=[build_metadata()],
                    auth_silent_return={"access_token": "new_tok", "refresh_token": "new_ref"},
                ))
            return manager

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_refreshing)
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(upsert_calls) >= 1

    def test_sync_cursors_loaded(self, monkeypatch):
        _patch_common(monkeypatch)
        load_calls = []
        original_load = emails_service.load_sync_cursors
        monkeypatch.setattr(
            emails_service, "load_sync_cursors",
            lambda lookup, **_kw: (load_calls.append(lookup), {})[1],
        )
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(load_calls) == 1

    def test_metadata_persisted(self, monkeypatch):
        _patch_common(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(
            emails_service, "persist_email_metadata_batch",
            lambda aid, meta, **_kw: (persist_calls.append((aid, meta)), len(meta))[1],
        )
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(persist_calls) >= 1

    def test_list_by_mailbox_database_error(self, monkeypatch):
        from database import DatabaseError
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.account_store, "list_by_mailbox",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_list_by_mailbox_unexpected_error(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.account_store, "list_by_mailbox",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_persist_tokens_database_error(self, monkeypatch):
        from database import DatabaseError
        _patch_common(monkeypatch)

        def _build_refreshing(accounts):
            from core.email import EmailManager
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_silent_return={"access_token": "new_tok", "refresh_token": "new_ref"},
                ))
            return manager

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_refreshing)
        monkeypatch.setattr(
            emails_service.account_store, "upsert_tokens",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_single_account_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID

    def test_single_account_not_found_raises(self, monkeypatch):
        _patch_common(monkeypatch)
        with pytest.raises(AccountNotFound, match="during metadata sync"):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID, "nonexistent")

    def test_single_account_db_error_on_lookup(self, monkeypatch):
        from database import DatabaseError
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.account_store, "get",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_single_account_unexpected_error_on_lookup(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.account_store, "get",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        with pytest.raises(EmailFetchError, match="Failed to look up account for metadata sync"):
            emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)


# ==================================================================
# send_email
# ==================================================================


class TestSendEmail:

    def _make_payload(self, account_id=_ACCOUNT_ID):
        from api.schemas.email import EmailSendRequest
        return EmailSendRequest(
            account_id=account_id,
            subject="Hello",
            body="World",
            recipients=["dest@example.com"],
        )

    def test_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert result == {"status": "sent"}

    def test_account_not_found_raises_404(self, monkeypatch):
        _patch_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            emails_service.send_email(
                _MAILBOX_ID, self._make_payload("nonexistent"), _USER_ID,
            )

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_send_core_error_translated(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "send_exc": EmailExternalAPIError("SMTP reject"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_send_generic_exception_raises_external_api_error(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "send_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_persists_refreshed_tokens(self, monkeypatch):
        _patch_common(monkeypatch)
        upsert_calls = []
        monkeypatch.setattr(
            emails_service.account_store, "upsert_tokens",
            lambda *args, **kwargs: upsert_calls.append(args),
        )

        def _build_refreshing(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_silent_return={"access_token": "new_tok", "refresh_token": "new_ref"},
                ))
            return manager

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_refreshing)
        emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert len(upsert_calls) >= 1

    def test_persists_sent_metadata(self, monkeypatch):
        """After send, metadata is persisted with the correct account_id."""
        _patch_common(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(
            emails_service, "persist_email_metadata_batch",
            lambda aid, meta, **_kw: (persist_calls.append((aid, meta)), len(meta))[1],
        )
        emails_service.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert len(persist_calls) == 1
        aid, meta_list = persist_calls[0]
        assert aid == _ACCOUNT_ID
        assert len(meta_list) == 1
        assert meta_list[0].box == "SENT"

    def test_html_body_sanitised_before_provider_send(self, monkeypatch):
        # The outbound sanitiser runs at the trust boundary before the body
        # reaches the provider's send_email (Gmail multipart / Outlook HTML).
        from api.schemas.email import EmailSendRequest
        _patch_common(monkeypatch)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                label = f"{acc.get('mailbox_id', '')}__{acc.get('account_id', '')}"
                client = FakeEmailClient(
                    label, auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)
        payload = EmailSendRequest(
            account_id=_ACCOUNT_ID,
            subject="Hello",
            body='<script>steal()</script><p>real <strong>body</strong></p>',
            recipients=["dest@example.com"],
        )
        emails_service.send_email(_MAILBOX_ID, payload, _USER_ID)
        # The fake records (subject, body, recipients) on ``sent_emails``.
        _subject, sent_body, _recipients = captured_clients[0].sent_emails[0]
        assert "<script>" not in sent_body
        assert "<p>real <strong>body</strong></p>" in sent_body


# ==================================================================
# _reconcile_ghost_emails
# ==================================================================


class TestReconciliation:

    def test_full_sync_triggers_reconciliation(self, monkeypatch):
        """Bootstrap (is_full_sync=True) should trigger ghost reconciliation."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "existing_message_ids": ["m1"],
        })
        # The SQL set-difference (M8) already excludes the bootstrap ids, so
        # the suspect loader returns only m_ghost.
        monkeypatch.setattr(
            emails_service, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["m_ghost"],
        )
        delete_calls = []
        monkeypatch.setattr(
            emails_service, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        # m_ghost should have been deleted
        assert any("m_ghost" in ids for _, ids in delete_calls)

    def test_incremental_skips_reconciliation(self, monkeypatch):
        """Incremental (is_full_sync=False) should not trigger reconciliation."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": False,
        })
        load_calls = []
        monkeypatch.setattr(
            emails_service, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: (load_calls.append(_aid), [])[1],
        )
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert load_calls == []

    def test_verification_error_skips_gracefully(self, monkeypatch):
        """If verify_message_existence fails, reconciliation should skip."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "verify_exc": RuntimeError("API down"),
        })
        monkeypatch.setattr(
            emails_service, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["m_ghost"],
        )
        # Should not raise
        result = emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced >= 0

    def test_no_ghosts_means_no_deletes(self, monkeypatch):
        """If the suspect set is empty, no deletes should happen."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "existing_message_ids": ["m1"],
        })
        # SQL diff returns no suspects (every stored id was in the bootstrap set).
        monkeypatch.setattr(
            emails_service, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: [],
        )
        delete_calls = []
        monkeypatch.setattr(
            emails_service, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        # No ghost deletes (only the normal deletes call with empty list)
        ghost_deletes = [ids for _, ids in delete_calls if ids and "m1" not in ids]
        assert ghost_deletes == []

    def test_all_suspects_are_ghosts(self, monkeypatch):
        """When none of the suspect IDs exist at provider, all are deleted."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "existing_message_ids": [],  # nothing exists at provider
        })
        monkeypatch.setattr(
            emails_service, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["ghost1", "ghost2"],
        )
        delete_calls = []
        monkeypatch.setattr(
            emails_service, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        emails_service.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        all_deleted = [mid for _, ids in delete_calls for mid in ids]
        assert "ghost1" in all_deleted
        assert "ghost2" in all_deleted


# ==================================================================
# manage_trash
# ==================================================================


class TestManageTrash:

    def _make_payload(self, action="delete", items=None):
        from api.schemas.email import TrashActionRequest, TrashItem
        if items is None:
            items = [TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID)]
        return TrashActionRequest(action=action, items=items)

    def _patch_trash_common(self, monkeypatch, *, fake_client_kwargs=None, previous_box="ALL_MAIL"):
        _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)
        monkeypatch.setattr(
            emails_service, "get_trash_emails_by_ids",
            lambda _aid, _ids, **_kw: [
                {"provider_message_id": mid, "box": "TRASH", "previous_box": previous_box}
                for mid in _ids
            ],
        )
        monkeypatch.setattr(
            emails_service, "mark_as_deleted_batch",
            lambda _aid, _ids, **_kw: len(_ids),
        )
        monkeypatch.setattr(
            emails_service, "restore_from_trash_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )
        monkeypatch.setattr(
            emails_service, "restore_from_trash_discovered_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )

    def test_delete_happy_path(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        result = emails_service.manage_trash(
            _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
        )
        assert result.affected == 1

    def test_restore_happy_path(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        result = emails_service.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert result.affected == 1

    def test_delete_marks_deleted_in_db(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        mark_calls = []
        monkeypatch.setattr(
            emails_service, "mark_as_deleted_batch",
            lambda aid, ids, **_kw: (mark_calls.append((aid, ids)), len(ids))[1],
        )
        emails_service.manage_trash(
            _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
        )
        assert len(mark_calls) == 1
        assert mark_calls[0][0] == _ACCOUNT_ID
        assert "m1" in mark_calls[0][1]

    def test_restore_calls_restore_batch(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        restore_calls = []
        monkeypatch.setattr(
            emails_service, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        emails_service.manage_trash(
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
        result = emails_service.manage_trash(_MAILBOX_ID, payload, _USER_ID)
        assert result.affected == 1

    def test_account_not_in_mailbox_raises(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        from api.schemas.email import TrashItem
        payload = self._make_payload("delete", items=[
            TrashItem(provider_message_id="m1", account_id="nonexistent"),
        ])
        with pytest.raises(AccountNotFound):
            emails_service.manage_trash(_MAILBOX_ID, payload, _USER_ID)

    def test_email_not_in_trash_raises(self, monkeypatch):
        self._patch_trash_common(monkeypatch)
        monkeypatch.setattr(
            emails_service, "get_trash_emails_by_ids",
            lambda _aid, _ids, **_kw: [],  # no emails found in trash
        )
        with pytest.raises(EmailNotInTrash):
            emails_service.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            emails_service.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "delete_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.manage_trash(
                _MAILBOX_ID, self._make_payload("delete"), _USER_ID,
            )

    def test_generic_exception_raises_external_api_error(self, monkeypatch):
        self._patch_trash_common(monkeypatch, fake_client_kwargs={
            "delete_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.manage_trash(
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
            emails_service, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        restore_calls = []
        monkeypatch.setattr(
            emails_service, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        result = emails_service.manage_trash(
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
            emails_service, "restore_from_trash_batch",
            lambda aid, rows, **_kw: (restore_calls.append((aid, rows)), len(rows))[1],
        )
        discovered_calls = []
        monkeypatch.setattr(
            emails_service, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        result = emails_service.manage_trash(
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
            emails_service, "restore_from_trash_discovered_batch",
            lambda aid, rows, **_kw: (discovered_calls.append((aid, rows)), len(rows))[1],
        )
        emails_service.manage_trash(
            _MAILBOX_ID, self._make_payload("restore"), _USER_ID,
        )
        assert len(discovered_calls) == 1
        assert discovered_calls[0][1][0][3] == "ALL_MAIL"


# ==================================================================
# move_to_trash
# ==================================================================


class TestMoveToTrash:

    def _make_payload(self, items=None):
        from api.schemas.email import MoveToTrashRequest, TrashItem
        if items is None:
            items = [TrashItem(provider_message_id="m1", account_id=_ACCOUNT_ID)]
        return MoveToTrashRequest(items=items)

    def _patch_move_common(self, monkeypatch, *, fake_client_kwargs=None):
        _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)
        monkeypatch.setattr(
            emails_service, "move_to_trash_batch",
            lambda _aid, _rows, **_kw: len(_rows),
        )

    def test_happy_path(self, monkeypatch):
        self._patch_move_common(monkeypatch)
        result = emails_service.move_to_trash(
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
        result = emails_service.move_to_trash(_MAILBOX_ID, payload, _USER_ID)
        assert result.affected == 1

    def test_all_provider_failure_returns_zero(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_return": {},
        })
        result = emails_service.move_to_trash(
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
            emails_service.move_to_trash(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            emails_service.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )

    def test_generic_exception_raises_external_api_error(self, monkeypatch):
        self._patch_move_common(monkeypatch, fake_client_kwargs={
            "move_to_trash_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_trash(
                _MAILBOX_ID, self._make_payload(), _USER_ID,
            )


# ==================================================================
# update_read_status
# ==================================================================

_ACCOUNT_ID_2 = "acc2"
_LABEL_2 = f"{_MAILBOX_ID}__{_ACCOUNT_ID_2}"


def _patch_read_status(monkeypatch, *, accounts=None, fake_client_kwargs=None):
    """Apply monkeypatches specific to update_read_status tests."""
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "list_by_mailbox",
        lambda _mb: accounts,
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        emails_service, "update_email_read_status_batch",
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

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)


class TestUpdateReadStatus:

    @staticmethod
    def _make_payload(items, is_read=True):
        return ReadStatusRequest(
            is_read=is_read,
            items=[ReadStatusItem(account_id=aid, provider_message_id=mid)
                   for aid, mid in items],
        )

    def test_happy_path_single_account(self, monkeypatch):
        _patch_read_status(monkeypatch)
        payload = self._make_payload([
            (_ACCOUNT_ID, "m1"),
            (_ACCOUNT_ID, "m2"),
        ], is_read=True)
        result = emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].updated == 2

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
        result = emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 2
        assert len(result.accounts) == 2
        returned_aids = {d.account_id for d in result.accounts}
        assert _ACCOUNT_ID in returned_aids
        assert _ACCOUNT_ID_2 in returned_aids

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_read_status(monkeypatch)
        payload = self._make_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "update_read_status_exc": EmailExternalAPIError("API fail"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_read_status(monkeypatch, fake_client_kwargs={
            "update_read_status_exc": RuntimeError("unexpected"),
        })
        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_only_for_updated_ids(self, monkeypatch):
        """FakeEmailClient returns all IDs by default; override to return subset."""
        accounts = [_fake_account()]
        monkeypatch.setattr(
            emails_service, "ensure_mailbox_access",
            lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
        )
        monkeypatch.setattr(
            emails_service.account_store, "list_by_mailbox",
            lambda _mb: accounts,
        )
        monkeypatch.setattr(
            emails_service, "load_wrapped_app_credentials",
            lambda _prov: {"client_id": "cid", "client_secret": "cs"},
        )
        monkeypatch.setattr(
            emails_service, "load_wrapped_account_tokens",
            lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
        )
        monkeypatch.setattr(
            emails_service.account_store, "upsert_tokens",
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

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_partial)

        db_calls = []
        monkeypatch.setattr(
            emails_service, "update_email_read_status_batch",
            lambda aid, ids, is_read, **_kw: (db_calls.append((aid, list(ids), is_read)), len(ids))[1],
        )

        payload = self._make_payload([
            (_ACCOUNT_ID, "m1"),
            (_ACCOUNT_ID, "m2"),
        ], is_read=True)
        result = emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert result.updated_count == 1
        assert len(db_calls) == 1
        assert db_calls[0][1] == ["m1"]

    def test_persists_refreshed_tokens(self, monkeypatch):
        accounts = [_fake_account()]
        monkeypatch.setattr(
            emails_service, "ensure_mailbox_access",
            lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
        )
        monkeypatch.setattr(
            emails_service.account_store, "list_by_mailbox",
            lambda _mb: accounts,
        )
        monkeypatch.setattr(
            emails_service, "load_wrapped_app_credentials",
            lambda _prov: {"client_id": "cid", "client_secret": "cs"},
        )
        monkeypatch.setattr(
            emails_service, "load_wrapped_account_tokens",
            lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
        )
        monkeypatch.setattr(
            emails_service, "update_email_read_status_batch",
            lambda _aid, _ids, _read, **_kw: len(_ids),
        )

        upsert_calls = []
        monkeypatch.setattr(
            emails_service.account_store, "upsert_tokens",
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

        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build_refreshing)

        payload = self._make_payload([(_ACCOUNT_ID, "m1")])
        emails_service.update_read_status(_MAILBOX_ID, payload, _USER_ID)
        assert len(upsert_calls) >= 1


# ==================================================================
# move_to_spam / restore_from_spam
# ==================================================================


def _patch_spam(monkeypatch, *, accounts=None, fake_client_kwargs=None):
    """Apply monkeypatches specific to spam move/restore tests."""
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "list_by_mailbox",
        lambda _mb: accounts,
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        emails_service, "update_email_spam_status_batch",
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

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)


def _spam_payload(items):
    return SpamRequest(
        items=[SpamItem(account_id=aid, provider_message_id=mid)
               for aid, mid in items],
    )


class TestMoveToSpam:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_spam_exc": EmailExternalAPIError("API fail"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_spam_exc": RuntimeError("unexpected"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_spam_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            emails_service, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        emails_service.move_to_spam(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "SPAM"


class TestRestoreFromSpam:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _spam_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _spam_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_spam_exc": EmailExternalAPIError("API fail"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_spam_exc": RuntimeError("unexpected"),
        })
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_all_mail_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            emails_service, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _spam_payload([(_ACCOUNT_ID, "m1")])
        emails_service.restore_from_spam(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ALL_MAIL"


# ==================================================================
# move_to_archive / restore_from_archive
# ==================================================================
#
# Archive shares the generic ``_execute_box_move_operation`` engine and the
# ``update_email_spam_status_batch`` persistence helper with spam, so the
# spam patch helper (`_patch_spam`) is reused verbatim. The archive-specific
# coverage pins: the response wraps the ArchiveResponse/AccountArchiveDetail
# schema, the persistence box is ARCHIVE (move) / ALL_MAIL (restore), and
# errors translate via the dedicated fallbacks.


def _archive_payload(items):
    return ArchiveRequest(
        items=[ArchiveItem(account_id=aid, provider_message_id=mid)
               for aid, mid in items],
    )


class TestMoveToArchive:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_happy_path_multi_account(self, monkeypatch):
        accounts = [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)]
        _patch_spam(monkeypatch, accounts=accounts)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID_2, "m2")])
        result = emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_auth_error_raises_not_connected(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(AccountNotConnected):
            emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_archive_exc": EmailExternalAPIError("API fail"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        # A RuntimeError from the client is wrapped by EmailManager into
        # EmailExternalAPIError (a CoreError), which the engine translates to
        # ExternalAPIError — mirroring the spam path exactly.
        _patch_spam(monkeypatch, fake_client_kwargs={
            "move_to_archive_exc": RuntimeError("unexpected"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_archive_box(self, monkeypatch):
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            emails_service, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        emails_service.move_to_archive(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ARCHIVE"


class TestRestoreFromArchive:

    def test_happy_path_single_account(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([(_ACCOUNT_ID, "m1"), (_ACCOUNT_ID, "m2")])
        result = emails_service.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)
        assert result.moved_count == 2
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].moved == 2

    def test_account_not_in_mailbox_raises_not_found(self, monkeypatch):
        _patch_spam(monkeypatch)
        payload = _archive_payload([("nonexistent", "m1")])
        with pytest.raises(AccountNotFound):
            emails_service.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_core_error_translates_via_mapping(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_archive_exc": EmailExternalAPIError("API fail"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_unexpected_error_raises_external_api_error(self, monkeypatch):
        _patch_spam(monkeypatch, fake_client_kwargs={
            "restore_from_archive_exc": RuntimeError("unexpected"),
        })
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        with pytest.raises(ExternalAPIError):
            emails_service.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)

    def test_db_helper_called_with_all_mail_box(self, monkeypatch):
        # Unarchiving restores the message to the real inbox (ALL_MAIL).
        _patch_spam(monkeypatch)
        db_calls: list[tuple] = []
        monkeypatch.setattr(
            emails_service, "update_email_spam_status_batch",
            lambda aid, results, box, **_kw: (db_calls.append((aid, results, box)), len(results))[1],
        )
        payload = _archive_payload([(_ACCOUNT_ID, "m1")])
        emails_service.restore_from_archive(_MAILBOX_ID, payload, _USER_ID)
        assert len(db_calls) == 1
        assert db_calls[0][2] == "ALL_MAIL"


# ==================================================================
# list_emails
# ==================================================================


_SAMPLE_ROW = {
    "provider_message_id": "m1",
    "account_id": _ACCOUNT_ID,
    "mailbox_id": _MAILBOX_ID,
    "thread_id": "t1",
    "from_email": "sender@test.com",
    "from_name": "Sender",
    "subject": "Hello",
    "received_at": "2025-01-15T10:00:00+00:00",
    "is_read": False,
    "box": "ALL_MAIL",
}


def _patch_list_emails(
    monkeypatch,
    *,
    rows=None,
    account_get_return="default",
    accounts_for_mailbox=None,
    list_filtered_calls=None,
    count_filtered_calls=None,
    total=None,
):
    """Apply monkeypatches for list_emails tests against the unified
    list_filtered + count_filtered contract.

    ``list_emails`` now returns an ``EmailPageOut`` envelope, so it makes
    a second store call (``count_filtered``) for the total. Both stubs
    are installed here; ``count_filtered`` returns ``total`` (defaults to
    ``len(rows)``) and optionally records its kwargs into
    ``count_filtered_calls`` so a test can assert it received the SAME
    predicates as ``list_filtered`` (the shared-predicate guarantee).
    """
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    if account_get_return == "default":
        monkeypatch.setattr(
            emails_service.account_store, "get",
            lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
        )
    else:
        monkeypatch.setattr(
            emails_service.account_store, "get",
            account_get_return,
        )
    monkeypatch.setattr(
        emails_service.account_store, "list_by_mailbox",
        lambda _mb: accounts_for_mailbox if accounts_for_mailbox is not None else [_fake_account()],
    )
    result_rows = rows if rows is not None else [_SAMPLE_ROW]
    total_value = total if total is not None else len(result_rows)

    def _record(
        account_ids, box, tokens, limit, offset,
        *, extra_filters=None, box_in=None, box_not_in=None,
        distinct_provider_message_id=False, group_by_thread=False,
        operator_clauses=None,
    ):
        if list_filtered_calls is not None:
            list_filtered_calls.append({
                "account_ids": account_ids,
                "box": box,
                "tokens": tokens,
                "limit": limit,
                "offset": offset,
                "extra_filters": extra_filters,
                "box_in": box_in,
                "box_not_in": box_not_in,
                "distinct_provider_message_id": distinct_provider_message_id,
                "group_by_thread": group_by_thread,
                "operator_clauses": operator_clauses,
            })
        return result_rows

    def _count(
        account_ids, box, tokens,
        *, extra_filters=None, box_in=None, box_not_in=None,
        distinct_provider_message_id=False, group_by_thread=False,
        operator_clauses=None,
    ):
        if count_filtered_calls is not None:
            count_filtered_calls.append({
                "account_ids": account_ids,
                "box": box,
                "tokens": tokens,
                "extra_filters": extra_filters,
                "box_in": box_in,
                "box_not_in": box_not_in,
                "distinct_provider_message_id": distinct_provider_message_id,
                "group_by_thread": group_by_thread,
                "operator_clauses": operator_clauses,
            })
        return total_value

    monkeypatch.setattr(
        emails_service.email_metadata_store, "list_filtered", _record,
    )
    monkeypatch.setattr(
        emails_service.email_metadata_store, "count_filtered", _count,
    )


class TestListEmails:

    def test_single_account_happy_path(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        result = emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert len(result.items) == 1
        assert result.items[0].provider_message_id == "m1"
        assert result.items[0].account_id == _ACCOUNT_ID
        # ``mailbox_id`` is projected from the JOIN on ``accounts``. The
        # frontend reads it to know which mailbox owns each row inside a
        # virtual mailbox whose scope spans several real mailboxes —
        # dropping it would re-introduce the ``account_not_found`` 404 on
        # open/favorite/reply/forward/attachment paths.
        assert result.items[0].mailbox_id == _MAILBOX_ID
        # Single-account branch passes a one-element account_ids list.
        assert len(calls) == 1
        assert calls[0]["account_ids"] == [_ACCOUNT_ID]
        assert calls[0]["box"] == "ALL_MAIL"

    def test_unified_view_happy_path(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        result = emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
        assert len(result.items) == 1
        assert result.items[0].provider_message_id == "m1"
        # Unified branch resolves accounts via account_store.list_by_mailbox.
        assert calls[0]["account_ids"] == [_ACCOUNT_ID]

    def test_unified_view_with_multiple_accounts_passes_all_ids(self, monkeypatch):
        calls: list = []
        accounts = [
            _fake_account(account_id="acc1"),
            _fake_account(account_id="acc2"),
            _fake_account(account_id="acc3"),
        ]
        _patch_list_emails(
            monkeypatch,
            accounts_for_mailbox=accounts,
            list_filtered_calls=calls,
        )
        emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
        assert calls[0]["account_ids"] == ["acc1", "acc2", "acc3"]

    def test_unified_view_no_accounts_returns_empty_without_db_call(self, monkeypatch):
        calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            accounts_for_mailbox=[],
            list_filtered_calls=calls,
            count_filtered_calls=count_calls,
        )
        result = emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
        assert result.items == []
        assert result.total == 0
        # Empty mailbox must short-circuit BEFORE calling list_filtered.
        assert calls == []
        # ...and BEFORE count_filtered too — no DB round trips at all.
        assert count_calls == []

    def test_account_not_found_raises(self, monkeypatch):
        _patch_list_emails(monkeypatch)
        with pytest.raises(AccountNotFound, match="during email listing"):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, "nonexistent")

    def test_db_error_on_account_lookup_raises(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_mb, _aid):
            raise DbQueryError("db fail")

        monkeypatch.setattr(emails_service.account_store, "get", _raise)
        with pytest.raises(Exception):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_unexpected_error_on_account_lookup_raises_email_list_error(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        def _raise(_mb, _aid):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(emails_service.account_store, "get", _raise)
        with pytest.raises(
            EmailListError, match="Failed to look up account for email listing",
        ):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_db_error_on_list_filtered_translated(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        # ``list_filtered`` is called with keyword args (extra_filters,
        # box_not_in, operator_clauses); the stub must accept **_kwargs or it
        # raises TypeError before our injected error and the test passes for
        # the wrong reason. Mirrors test_db_error_on_count_filtered_translated.
        def _raise(_aids, _box, _tokens, _limit, _offset, **_kwargs):
            raise DbQueryError("db fail")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "list_filtered", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_db_error_on_unified_account_listing_translated(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_mb):
            raise DbQueryError("db fail")

        monkeypatch.setattr(emails_service.account_store, "list_by_mailbox", _raise)
        with pytest.raises(Exception):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_unexpected_error_raises_email_list_error_with_filtered_message(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        # **_kwargs so the keyword args (extra_filters/box_not_in/
        # operator_clauses) reach the stub and the injected RuntimeError is
        # what propagates — not a signature TypeError.
        def _raise(_aids, _box, _tokens, _limit, _offset, **_kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "list_filtered", _raise,
        )
        with pytest.raises(
            EmailListError, match="Failed to list email metadata for filtered listing",
        ):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_unexpected_error_on_unified_account_listing_raises_email_list_error(
        self, monkeypatch,
    ):
        _patch_list_emails(monkeypatch)

        def _raise(_mb):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(emails_service.account_store, "list_by_mailbox", _raise)
        with pytest.raises(
            EmailListError, match="Failed to load mailbox accounts for email listing",
        ):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_empty_result_returns_empty_list(self, monkeypatch):
        _patch_list_emails(monkeypatch, rows=[])
        result = emails_service.list_emails(_MAILBOX_ID, "SPAM", _USER_ID)
        assert result.items == []
        assert result.total == 0

    def test_q_is_parsed_into_tokens_passed_to_store(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="foo bar",
        )
        assert calls[0]["tokens"] == ["foo", "bar"]

    def test_q_none_results_in_empty_token_list(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert calls[0]["tokens"] == []

    def test_q_only_whitespace_results_in_empty_token_list(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="   ",
        )
        assert calls[0]["tokens"] == []

    def test_limit_and_offset_propagate_to_store(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            limit=42, offset=100,
        )
        assert calls[0]["limit"] == 42
        assert calls[0]["offset"] == 100

    def test_default_limit_and_offset_propagated(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        # The service signature default stays 200 — the 200->50 change is
        # ONLY in the router's Query(default=...). This test calls the
        # service directly without ``limit``, so it must still see 200.
        assert calls[0]["limit"] == 200
        assert calls[0]["offset"] == 0

    def test_group_by_thread_flag_passed_to_both_store_calls(self, monkeypatch):
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            group_by_thread=True,
        )
        # The flag must reach BOTH list_filtered and count_filtered so the
        # total counts threads, not messages (the page and total agree).
        assert list_calls[0]["group_by_thread"] is True
        assert count_calls[0]["group_by_thread"] is True

    def test_group_by_thread_defaults_false(self, monkeypatch):
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        # Favourites and the regular ungrouped listing omit the flag → False.
        emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert list_calls[0]["group_by_thread"] is False
        assert count_calls[0]["group_by_thread"] is False


class TestListEmailsPagination:
    """``list_emails`` returns an ``EmailPageOut`` envelope: the total
    comes from ``count_filtered`` and ``count_filtered`` must receive the
    exact same predicates as ``list_filtered``."""

    def test_total_is_value_returned_by_count_filtered(self, monkeypatch):
        _patch_list_emails(monkeypatch, rows=[_SAMPLE_ROW], total=137)
        result = emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        # total is the WHOLE filtered set, independent of the page length.
        assert result.total == 137
        assert len(result.items) == 1

    def test_limit_and_offset_echoed_in_envelope(self, monkeypatch):
        _patch_list_emails(monkeypatch)
        result = emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            limit=25, offset=50,
        )
        assert result.limit == 25
        assert result.offset == 50

    def test_count_filtered_receives_same_predicates_as_list_filtered(self, monkeypatch):
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="foo bar",
        )
        assert len(count_calls) == 1
        # The shared-predicate guarantee: account_ids / box / tokens /
        # extra_filters / box_not_in must match between the two calls so
        # the total counts exactly what the page lists.
        for key in ("account_ids", "box", "tokens", "extra_filters", "box_not_in"):
            assert count_calls[0][key] == list_calls[0][key]

    def test_favorite_predicates_shared_between_list_and_count(self, monkeypatch):
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, favorite=True,
        )
        # favourite + ALL_MAIL collapses box -> None and box_not_in ->
        # [TRASH, SPAM]; both the page and the total must agree on it.
        assert list_calls[0]["box"] is None
        assert list_calls[0]["box_not_in"] == ["TRASH", "SPAM"]
        assert list_calls[0]["extra_filters"] == {"is_favorite": True}
        assert count_calls[0]["box"] == list_calls[0]["box"]
        assert count_calls[0]["box_not_in"] == list_calls[0]["box_not_in"]
        assert count_calls[0]["extra_filters"] == list_calls[0]["extra_filters"]

    def test_regular_listing_does_not_request_distinct(self, monkeypatch):
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        # The regular box listing is scoped to a single mailbox where the
        # cross-account duplicate cannot occur — it must NOT dedup.
        assert list_calls[0]["distinct_provider_message_id"] is False
        assert count_calls[0]["distinct_provider_message_id"] is False

    def test_in_operator_overrides_route_box(self, monkeypatch):
        # ``in:sent`` in q wins over the route's ALL_MAIL: box_arg becomes
        # SENT and box_not_in is cleared (the override keeps the
        # mutually-exclusive box / box_not_in contract).
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="in:sent",
        )
        assert list_calls[0]["box"] == "SENT"
        assert list_calls[0]["box_not_in"] is None

    def test_in_operator_overrides_favorites_anchor_box(self, monkeypatch):
        # Favourites passes ALL_MAIL (box → None, box_not_in → [TRASH, SPAM]);
        # ``in:sent`` then overrides to SENT and clears box_not_in while the
        # is_favorite extra filter stays — i.e. "favourites in Sent".
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            favorite=True, q="in:sent",
        )
        assert list_calls[0]["box"] == "SENT"
        assert list_calls[0]["box_not_in"] is None
        assert list_calls[0]["extra_filters"] == {"is_favorite": True}

    def test_operator_clauses_passed_identically_to_list_and_count(self, monkeypatch):
        # The parsed operator_clauses must reach BOTH calls unchanged so the
        # total counts exactly what the page lists (same guarantee as tokens).
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            q="from:linkedin is:unread",
        )
        expected = [("from_contains", "linkedin"), ("is_read_op", False)]
        assert list_calls[0]["operator_clauses"] == expected
        assert count_calls[0]["operator_clauses"] == list_calls[0]["operator_clauses"]

    def test_no_operators_passes_none_operator_clauses(self, monkeypatch):
        # Pure free-text q → operator_clauses falls to None (service passes
        # ``operator_clauses or None``), so the emitted SQL is the pre-operator
        # query.
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        emails_service.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="foo bar",
        )
        assert list_calls[0]["operator_clauses"] is None
        # Free-text semantics are untouched by the richer parser.
        assert list_calls[0]["tokens"] == ["foo", "bar"]

    def test_db_error_on_count_filtered_translated(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_aids, _box, _tokens, **_kwargs):
            raise DbQueryError("count fail")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "count_filtered", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_unexpected_error_on_count_filtered_raises_email_list_error(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        def _raise(_aids, _box, _tokens, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "count_filtered", _raise,
        )
        with pytest.raises(
            EmailListError,
            match="Failed to count emails while paginating the mailbox listing",
        ):
            emails_service.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)


# ==================================================================
# count_unread_emails
# ==================================================================


def _patch_count_unread(
    monkeypatch,
    *,
    accounts_for_mailbox=None,
    counts=None,
    count_calls=None,
):
    """Apply monkeypatches for count_unread_emails tests.

    Patches the three symbols the service reads on the ``emails_service``
    module (it imports them by name): ``ensure_mailbox_access``,
    ``account_store.list_by_mailbox`` and
    ``email_metadata_store.count_unread_by_account``. ``counts`` is the
    per-account ``{account_id: unread}`` mapping the store returns (a
    ``GROUP BY`` omits accounts with 0). ``count_calls`` optionally records
    the ``(account_ids, box)`` the store was called with so a test can
    assert the short-circuit (it stays empty) or the box propagation.
    """
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "list_by_mailbox",
        lambda _mb: accounts_for_mailbox if accounts_for_mailbox is not None else [_fake_account()],
    )

    def _count(account_ids, box):
        if count_calls is not None:
            count_calls.append({"account_ids": account_ids, "box": box})
        return counts if counts is not None else {}

    monkeypatch.setattr(
        emails_service.email_metadata_store, "count_unread_by_account", _count,
    )


class TestCountUnreadEmails:

    def test_happy_path_breakdown_and_total(self, monkeypatch):
        accounts = [_fake_account(account_id="a1"), _fake_account(account_id="a2")]
        _patch_count_unread(
            monkeypatch, accounts_for_mailbox=accounts, counts={"a1": 5, "a2": 7},
        )
        result = emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
        assert result.mailbox_id == _MAILBOX_ID
        assert result.box == "ALL_MAIL"
        assert result.total == 12
        assert {(d.account_id, d.unread) for d in result.accounts} == {("a1", 5), ("a2", 7)}

    def test_accounts_without_unread_rows_are_filled_with_zero(self, monkeypatch):
        accounts = [_fake_account(account_id="a1"), _fake_account(account_id="a2")]
        # GROUP BY omits a2 (no unread rows); the service must still list it as 0.
        _patch_count_unread(
            monkeypatch, accounts_for_mailbox=accounts, counts={"a1": 3},
        )
        result = emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
        assert result.total == 3
        a2 = next(d for d in result.accounts if d.account_id == "a2")
        assert a2.unread == 0

    def test_mailbox_without_accounts_short_circuits_without_db_call(self, monkeypatch):
        count_calls: list = []
        _patch_count_unread(
            monkeypatch, accounts_for_mailbox=[], count_calls=count_calls,
        )
        result = emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
        assert result.total == 0
        assert result.accounts == []
        # No accounts ⇒ the per-account count query must never run.
        assert count_calls == []

    def test_spam_box_is_propagated_to_store_and_response(self, monkeypatch):
        count_calls: list = []
        _patch_count_unread(
            monkeypatch, counts={_ACCOUNT_ID: 4}, count_calls=count_calls,
        )
        result = emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "SPAM")
        assert count_calls[0]["box"] == "SPAM"
        assert result.box == "SPAM"

    def test_ownership_error_propagates_unwrapped(self, monkeypatch):
        _patch_count_unread(monkeypatch)
        monkeypatch.setattr(
            emails_service, "ensure_mailbox_access",
            lambda _mb, _uid: (_ for _ in ()).throw(MailboxNotFound("foreign mailbox")),
        )
        with pytest.raises(MailboxNotFound):
            emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_db_error_on_count_is_translated_not_wrapped(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_count_unread(monkeypatch)

        def _raise(_ids, _box):
            raise DbQueryError("count fail")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "count_unread_by_account", _raise,
        )
        # A DatabaseError must surface as 503 DatabaseQueryError via
        # translate_database_error — NOT as the 500 UnreadCountError.
        with pytest.raises(DatabaseQueryError):
            emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_unexpected_error_on_count_is_wrapped_in_unread_count_error(self, monkeypatch):
        _patch_count_unread(monkeypatch)

        def _raise(_ids, _box):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            emails_service.email_metadata_store, "count_unread_by_account", _raise,
        )
        with pytest.raises(
            UnreadCountError,
            match="Failed to count unread emails while building the mailbox unread badge",
        ):
            emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_unexpected_error_on_account_listing_is_wrapped(self, monkeypatch):
        _patch_count_unread(monkeypatch)

        def _raise(_mb):
            raise RuntimeError("boom")

        monkeypatch.setattr(emails_service.account_store, "list_by_mailbox", _raise)
        with pytest.raises(
            UnreadCountError, match="Failed to load mailbox accounts for unread count",
        ):
            emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_db_error_on_account_listing_is_translated_not_wrapped(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_count_unread(monkeypatch)

        def _raise(_mb):
            raise DbQueryError("list fail")

        monkeypatch.setattr(emails_service.account_store, "list_by_mailbox", _raise)
        # Mirror of test_db_error_on_count_is_translated_not_wrapped for the
        # FIRST try block (account listing): a DatabaseError must surface as a
        # 503 DatabaseQueryError via translate_database_error, NOT the 500
        # UnreadCountError. Without this, swapping the two except clauses on the
        # listing block would silently downgrade 503→500 and stay green (the
        # sibling unexpected-error test only injects RuntimeError).
        with pytest.raises(DatabaseQueryError):
            emails_service.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")


# ==================================================================
# get_email_full_content
# ==================================================================


def _patch_get_content_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for get_email_full_content tests."""
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
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
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)

    # Default: metadata row exists — tests that need a missing metadata row
    # override this patch explicitly.
    monkeypatch.setattr(
        emails_service.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )

    # Stub persist helper (best-effort, no-op by default)
    monkeypatch.setattr(emails_service, "persist_email_content", lambda *_a, **_kw: None)
    # The unified cache-miss path (``_fetch_and_persist_email_content``) also
    # recomputes ``has_attachments`` and lists the attachment rows after the
    # provider read; stub both so the service test never touches the real DB.
    # The cache-HIT path touches the sliding TTL — stub that too (asserted in
    # the dedicated hit test).
    monkeypatch.setattr(emails_service, "recompute_has_attachments", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        emails_service.email_attachment_store, "list_by_message",
        lambda _aid, _mid: [],
    )
    monkeypatch.setattr(
        emails_service, "touch_email_content_last_accessed", lambda *_a, **_kw: None,
    )


class TestGetEmailFullContent:

    def test_db_hit(self, monkeypatch):
        """When DB already has content, return it without calling core."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: {"html_body": "<p>cached</p>", "text_body": "cached"},
        )

        result = emails_service.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.html_body == "<p>cached</p>"
        assert result.text_body == "cached"

    def test_db_hit_touches_sliding_ttl_and_skips_provider(self, monkeypatch):
        """A cache hit refreshes ``last_accessed_at`` (sliding TTL) and does NOT
        build a provider manager — the body is served straight from the DB."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: {"html_body": "<p>cached</p>", "text_body": "cached"},
        )

        touch_calls = []
        monkeypatch.setattr(
            emails_service, "touch_email_content_last_accessed",
            lambda aid, mid: touch_calls.append((aid, mid)),
        )
        # If the hit branch reached the provider, this would explode.
        monkeypatch.setattr(
            emails_service, "build_manager_for_accounts",
            lambda _accs: (_ for _ in ()).throw(
                AssertionError("provider must not be built on a cache hit"),
            ),
        )

        result = emails_service.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.html_body == "<p>cached</p>"
        assert touch_calls == [(_ACCOUNT_ID, "m1")]

    def test_db_miss_fetches_from_provider(self, monkeypatch):
        """When DB returns None, fetch from provider (a SINGLE unified read) and
        persist. D4: ``fetch_content_with_attachments`` returns the body AND the
        attachment list in one round trip — verified via the fake's call list."""
        captured_clients = []

        def _capture_build(accounts):
            manager = EmailManager()
            for acc in accounts:
                label = f"{acc.get('mailbox_id', '')}__{acc.get('account_id', '')}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    email_content=EmailContent(
                        html_body="<p>from provider</p>", text_body="from provider",
                    ),
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _capture_build)
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        persist_calls = []
        monkeypatch.setattr(
            emails_service, "persist_email_content",
            lambda aid, mid, html, txt, **_kw: persist_calls.append((aid, mid, html, txt)),
        )

        result = emails_service.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.text_body == "from provider"
        assert len(persist_calls) == 1
        # Verify persist was called with the account_id and message id
        assert persist_calls[0][0] == _ACCOUNT_ID
        assert persist_calls[0][1] == "m1"
        # Exactly ONE unified provider read happened (body + attachments fused).
        assert len(captured_clients) == 1
        assert captured_clients[0].fetch_content_with_attachments_calls == ["m1"]

    def test_sanitizes_html(self, monkeypatch):
        """HTML body from provider is sanitized before being persisted and returned."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "email_content": EmailContent(
                html_body="<p>safe</p><script>alert(1)</script>", text_body="safe",
            ),
        })
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        persist_calls = []
        monkeypatch.setattr(
            emails_service, "persist_email_content",
            lambda aid, mid, html, txt, **_kw: persist_calls.append((aid, mid, html, txt)),
        )

        result = emails_service.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert "<script>" not in (result.html_body or "")
        assert "<p>safe</p>" in (result.html_body or "")
        # Verify persisted html is also sanitized
        assert "<script>" not in (persist_calls[0][2] or "")

    def test_account_not_found(self, monkeypatch):
        """Non-existent account_id raises AccountNotFound."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        with pytest.raises(AccountNotFound):
            emails_service.get_email_full_content(
                _MAILBOX_ID, "m1", "nonexistent", _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        """CoreError from manager.fetch_content_with_attachments is translated to
        EmailContentFetchError (mapped to ExternalAPIError). After D4 unification
        this is the SINGLE provider failure point in the cache-miss path; the
        fake raises it via the same ``fetch_content_exc`` injection."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "fetch_content_exc": EmailExternalAPIError("provider down"),
        })
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        with pytest.raises(ExternalAPIError):
            emails_service.get_email_full_content(
                _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
            )

    def test_persist_failure_best_effort(self, monkeypatch):
        """If persist_email_content raises, the content is still returned."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "email_content": EmailContent(
                html_body="<p>ok</p>", text_body="ok",
            ),
        })
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )
        monkeypatch.setattr(
            emails_service, "persist_email_content",
            MagicMock(side_effect=RuntimeError("db write failed")),
        )

        result = emails_service.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.text_body == "ok"
        assert result.html_body is not None

    def test_email_not_found_when_metadata_absent(self, monkeypatch):
        """Missing metadata row raises EmailNotFound before touching cache."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )

        get_content_calls = []
        monkeypatch.setattr(
            emails_service, "get_email_content",
            lambda _aid, _mid, **_kw: get_content_calls.append((_aid, _mid)) or None,
        )

        with pytest.raises(EmailNotFound):
            emails_service.get_email_full_content(
                _MAILBOX_ID, "never-existed", _ACCOUNT_ID, _USER_ID,
            )
        # exists must short-circuit before cache read
        assert get_content_calls == []

    def test_metadata_exists_database_error_translated(self, monkeypatch):
        """DatabaseError during exists() is translated via translate_database_error."""
        from database.errors.exceptions import QueryError as DbQueryError
        from api.errors.exceptions import DatabaseQueryError

        _patch_get_content_common(monkeypatch)

        def _raise(_aid, _mid):
            raise DbQueryError("exists fail")

        monkeypatch.setattr(emails_service.email_metadata_store, "exists", _raise)

        with pytest.raises(DatabaseQueryError):
            emails_service.get_email_full_content(
                _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
            )


# ==================================================================
# _run_content_prefetch_and_purge — post-sync background job (best-effort).
# Purges expired cached bodies for the synced accounts, then prefetches the
# body+attachments of each account's unread-recent-uncached inbox messages.
# Every failure is logged and swallowed so it can never abort the rest.
# ==================================================================


class TestRunContentPrefetchAndPurge:

    def test_purges_then_prefetches_every_target(self, monkeypatch):
        """Purge runs first (one indexed DELETE for all accounts), then every
        selected message is fetched + persisted via the shared helper."""
        purge_calls = []
        monkeypatch.setattr(
            emails_service, "purge_expired_email_content",
            lambda account_ids: purge_calls.append(account_ids) or 0,
        )
        monkeypatch.setattr(
            emails_service, "list_unread_recent_uncached",
            lambda aid, limit, **_kw: {"acc1": ["m1", "m2"], "acc2": ["m3"]}[aid],
        )
        fetched = []
        monkeypatch.setattr(
            emails_service, "_fetch_and_persist_email_content",
            lambda _mgr, label, aid, pmid: fetched.append((label, aid, pmid)),
        )

        manager = EmailManager()
        emails_service._run_content_prefetch_and_purge(
            manager,
            [("mb1__acc1", "acc1"), ("mb1__acc2", "acc2")],
            ["acc1", "acc2"],
        )
        # Purge scoped to exactly the synced accounts, once.
        assert purge_calls == [["acc1", "acc2"]]
        # Every selected message of every target was prefetched.
        assert fetched == [
            ("mb1__acc1", "acc1", "m1"),
            ("mb1__acc1", "acc1", "m2"),
            ("mb1__acc2", "acc2", "m3"),
        ]

    def test_uses_configured_prefetch_limit(self, monkeypatch):
        """The cap passed to the target selector is the module's _PREFETCH_LIMIT
        (the only value Python controls — window/TTL live in the SQL)."""
        monkeypatch.setattr(emails_service, "purge_expired_email_content", lambda _a: 0)
        seen_limits = []
        monkeypatch.setattr(
            emails_service, "list_unread_recent_uncached",
            lambda aid, limit, **_kw: seen_limits.append(limit) or [],
        )
        monkeypatch.setattr(
            emails_service, "_fetch_and_persist_email_content",
            lambda *a, **kw: None,
        )

        emails_service._run_content_prefetch_and_purge(
            EmailManager(), [("mb1__acc1", "acc1")], ["acc1"],
        )
        assert seen_limits == [emails_service._PREFETCH_LIMIT]

    def test_target_selection_failure_skips_account_not_the_rest(self, monkeypatch):
        """If selecting targets for one account raises, that account is skipped
        and the loop continues to the next (best-effort)."""
        monkeypatch.setattr(emails_service, "purge_expired_email_content", lambda _a: 0)

        def _select(aid, _limit, **_kw):
            if aid == "acc1":
                raise RuntimeError("selection boom")
            return ["m9"]

        monkeypatch.setattr(emails_service, "list_unread_recent_uncached", _select)
        fetched = []
        monkeypatch.setattr(
            emails_service, "_fetch_and_persist_email_content",
            lambda _mgr, label, aid, pmid: fetched.append((aid, pmid)),
        )

        # Must not raise — the first account's selection failure is swallowed.
        emails_service._run_content_prefetch_and_purge(
            EmailManager(),
            [("mb1__acc1", "acc1"), ("mb1__acc2", "acc2")],
            ["acc1", "acc2"],
        )
        # Only the surviving account's message was prefetched.
        assert fetched == [("acc2", "m9")]

    def test_single_message_fetch_failure_does_not_abort_remaining(self, monkeypatch):
        """A failure fetching one message must not abort the rest of the batch."""
        monkeypatch.setattr(emails_service, "purge_expired_email_content", lambda _a: 0)
        monkeypatch.setattr(
            emails_service, "list_unread_recent_uncached",
            lambda _aid, _limit, **_kw: ["m1", "m2", "m3"],
        )

        attempted = []

        def _fetch(_mgr, _label, _aid, pmid):
            attempted.append(pmid)
            if pmid == "m2":
                raise RuntimeError("provider hiccup")

        monkeypatch.setattr(emails_service, "_fetch_and_persist_email_content", _fetch)

        emails_service._run_content_prefetch_and_purge(
            EmailManager(), [("mb1__acc1", "acc1")], ["acc1"],
        )
        # m2 blew up but m3 was still attempted.
        assert attempted == ["m1", "m2", "m3"]


# ==================================================================
# _persist_attachment_metadata — B-SANITIZE (D-20 applied to received
# attachments). The filename of every discovered attachment goes through
# ``sanitize_filename`` with per-message dedup before it is upserted.
# ==================================================================


def _attachment_meta(
    *, filename, part_id="0.1", provider_attachment_id=None,
    mime_type="application/pdf", size=1024, content_id=None,
    is_inline=False, position=0,
):
    """Build an ``AttachmentMetadata`` instance for persistence tests."""
    from core.email import AttachmentMetadata
    return AttachmentMetadata(
        provider_message_id="m1",
        part_id=part_id,
        provider_attachment_id=provider_attachment_id,
        filename=filename,
        mime_type=mime_type,
        size=size,
        content_id=content_id,
        is_inline=is_inline,
        position=position,
    )


class TestPersistAttachmentMetadata:

    def _capture_rows(self, monkeypatch):
        captured: list[list[dict]] = []
        monkeypatch.setattr(
            emails_service.email_attachment_store, "upsert_batch",
            lambda rows: captured.append(rows),
        )
        return captured

    def test_empty_list_does_not_call_store(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        emails_service._persist_attachment_metadata(_ACCOUNT_ID, "m1", [])
        assert captured == []

    def test_clean_filename_passes_through(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="report.pdf")],
        )
        assert captured[0][0]["filename"] == "report.pdf"

    def test_path_traversal_is_neutralised(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="../../etc/passwd")],
        )
        persisted = captured[0][0]["filename"]
        assert ".." not in persisted
        assert "/" not in persisted

    def test_windows_reserved_name_is_prefixed(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="CON.pdf")],
        )
        assert captured[0][0]["filename"].startswith("_")

    def test_duplicate_names_within_message_are_deduped(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1",
            [
                _attachment_meta(filename="doc.pdf", part_id="0.1", position=0),
                _attachment_meta(filename="doc.pdf", part_id="0.2", position=1),
            ],
        )
        names = [row["filename"] for row in captured[0]]
        assert names == ["doc.pdf", "doc (1).pdf"]

    def test_mime_type_is_persisted_verbatim(self, monkeypatch):
        # mime_type is resolved upstream in the provider client (B-MIME);
        # _persist_attachment_metadata must not touch it.
        captured = self._capture_rows(monkeypatch)
        resolved_mime = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1",
            [_attachment_meta(filename="sheet.xlsx", mime_type=resolved_mime)],
        )
        assert captured[0][0]["mime_type"] == resolved_mime

    def test_upsert_database_error_is_swallowed(self, monkeypatch):
        # Persistence is best-effort (soft-fail) — a DatabaseError must not
        # propagate out of the helper.
        from database.errors.exceptions import QueryError as DbQueryError

        def _raise(_rows):
            raise DbQueryError("upsert failed")

        monkeypatch.setattr(
            emails_service.email_attachment_store, "upsert_batch", _raise,
        )
        # No exception expected.
        emails_service._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="report.pdf")],
        )


# ==================================================================
# get_reply_context
# ==================================================================


def _patch_reply_context_common(
    monkeypatch,
    *,
    fake_client_kwargs=None,
    account_provider: str = "gmail",
    account_email: str | None = "me@me.com",
):
    """Common monkeypatches for ``get_reply_context`` tests.

    The reply context endpoint is read-only, so it shares the
    ``ensure_mailbox_access`` → account lookup → silent auth → manager
    call → translate cascade with the rest of the email service.
    """
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )

    def _get(_mb, _aid):
        if _aid != _ACCOUNT_ID:
            return None
        return {
            "account_id": _ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": account_provider,
            "display_label": f"{account_provider}:{_ACCOUNT_ID}",
            "email_address": account_email,
        }
    monkeypatch.setattr(emails_service.account_store, "get", _get)
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
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
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)
    # Email metadata existence — default to True; tests that exercise
    # the missing-row path override.
    monkeypatch.setattr(
        emails_service.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )


def _build_reply_context_fake(
    *,
    from_email: str = "ana@x.com",
    from_name: str = "Ana",
    reply_to: list[str] | None = None,
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    subject: str = "Hello",
    message_id: str = "orig@x",
    references: str = "",
    thread_id: str = "thr-1",
    box: str = "ALL_MAIL",
):
    from datetime import datetime, timezone
    from core.email.email_client import ReplyContext
    return ReplyContext(
        provider_message_id="m1",
        thread_id=thread_id,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to or [],
        to_recipients=to_recipients or ["someone@x.com"],
        cc_recipients=cc_recipients or [],
        subject=subject,
        body_html=None,
        body_text="body text",
        received_at=datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc),
        message_id=message_id,
        references=references,
        box=box,
    )


class TestGetReplyContext:
    """Covers the service-layer orchestration for GET /reply-context."""

    def test_reply_happy_path(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="ana@x.com",
                    to_recipients=["me@me.com", "carol@x.com"],
                    subject="Hello",
                    message_id="orig@x",
                ),
            },
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        # Reply: To = [from] (R-10 does not apply, no Reply-To).
        assert result.to_recipients == ["ana@x.com"]
        assert result.cc_recipients == []
        assert result.subject == "Re: Hello"
        # Headers wrapped with angle brackets.
        assert result.in_reply_to == "<orig@x>"
        assert "<orig@x>" in result.references
        assert result.thread_id == "thr-1"
        assert result.reply_to_message_id == "m1"
        assert result.reply_kind == "reply"
        assert result.original_from_email == "ana@x.com"

    def test_reply_body_is_html_with_blockquote(self, monkeypatch):
        # The reply body is now built by ``build_quoted_body_html``: an HTML
        # attribution line followed by the original quoted inside a
        # <blockquote> (was plain text with "> " before the rich-text feature).
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(),
            },
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        assert "<blockquote" in result.body
        assert "escribió:" in result.body
        # The degraded original rides inside the quote fragment.
        assert "body text" in result.body

    def test_reply_all_excludes_current_account_email(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="ana@x.com",
                    to_recipients=["me@me.com", "carol@x.com"],
                    cc_recipients=["dan@x.com"],
                ),
            },
            account_email="me@me.com",
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply_all", _USER_ID,
        )
        assert result.to_recipients == ["ana@x.com"]
        cc = result.cc_recipients
        assert "me@me.com" not in cc
        assert "carol@x.com" in cc
        assert "dan@x.com" in cc

    def test_reply_to_header_overrides_from_for_to(self, monkeypatch):
        # R-10: mailing list pattern — Reply-To wins over From.
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="bounce@list.com",
                    reply_to=["editor@list.com"],
                ),
            },
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        assert result.to_recipients == ["editor@list.com"]
        assert result.original_from_email == "bounce@list.com"

    def test_forward_returns_empty_recipients_and_fwd_prefix(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    subject="Hello",
                    to_recipients=["a@x"], cc_recipients=["b@x"],
                ),
            },
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "forward", _USER_ID,
        )
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.subject == "Fwd: Hello"
        assert result.reply_kind == "forward"
        # The quoted body uses the forward block header.
        assert "Mensaje reenviado" in result.body

    def test_email_not_found_when_metadata_missing(self, monkeypatch):
        # The local-existence pre-check short-circuits to 404 BEFORE the
        # provider call (mirrors the favourites toggle pattern).
        _patch_reply_context_common(monkeypatch)
        monkeypatch.setattr(
            emails_service.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )
        with pytest.raises(EmailNotFound):
            emails_service.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", "reply", _USER_ID,
            )

    def test_account_not_found(self, monkeypatch):
        _patch_reply_context_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            emails_service.get_reply_context(
                _MAILBOX_ID, "nonexistent", "m1", "reply", _USER_ID,
            )

    def test_invalid_action_raises_reply_context_error(self, monkeypatch):
        from api.errors.exceptions import EmailReplyContextError
        _patch_reply_context_common(monkeypatch)
        with pytest.raises(EmailReplyContextError):
            emails_service.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "weird-action", _USER_ID,
            )

    def test_ownership_checked_before_action_validation(self, monkeypatch):
        # api_guide "ownership check first": ensure_mailbox_access must run
        # before the action guard, so a foreign mailbox is rejected even
        # when the action is invalid (otherwise the guard would leak action
        # validity to a non-owner). Locks the ordering fix.
        from api.errors.exceptions import Forbidden
        _patch_reply_context_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Foreign mailbox in reply-context ownership test.")

        monkeypatch.setattr(emails_service, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            emails_service.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "weird-action", _USER_ID,
            )

    def test_provider_fetch_error_translated_to_reply_context_error(self, monkeypatch):
        from api.errors.exceptions import EmailReplyContextError
        from core.email.errors import EmailReplyContextFetchError
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_exc": EmailReplyContextFetchError(
                    "provider failed",
                    detail={"reason": "provider_fetch_failed"},
                ),
            },
        )
        with pytest.raises(EmailReplyContextError):
            emails_service.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
            )

    def test_self_reply_to_sent_box_uses_original_to(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="me@me.com",
                    to_recipients=["client@x.com"],
                    box="SENT",
                ),
            },
            account_email="me@me.com",
        )
        result = emails_service.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        # Self-reply: To = original.to.
        assert result.to_recipients == ["client@x.com"]


# ==================================================================
# get_conversation — conversation viewer (read + lazy sync).
# Shares the ensure_mailbox_access → account lookup → silent auth →
# manager call → translate cascade with get_reply_context, but reads the
# base row via get_metadata (not exists), calls fetch_conversation, and
# best-effort persists the thread via _lazy_sync_conversation.
# ==================================================================


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


def _patch_get_conversation_common(
    monkeypatch,
    *,
    base_row="default",
    fake_client_kwargs=None,
    persist_exc=None,
    favorite_calls=None,
):
    """Common monkeypatches for ``get_conversation`` tests.

    Patches the base-message read (``get_metadata``), the lazy-sync persist
    helper (``persist_email_metadata_batch``) and the batched favourite
    re-apply (``email_metadata_store.set_favorites_true_batch``) — the
    dependency set of the conversation path, narrower than ``_patch_common``.
    """
    monkeypatch.setattr(
        emails_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        emails_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        emails_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        emails_service.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    row = _CONVERSATION_BASE_ROW if base_row == "default" else base_row
    monkeypatch.setattr(
        emails_service.email_metadata_store, "get_metadata",
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

    monkeypatch.setattr(emails_service, "build_manager_for_accounts", _build)

    if persist_exc is not None:
        def _persist(_aid, _meta, **_kw):
            raise persist_exc
        monkeypatch.setattr(emails_service, "persist_email_metadata_batch", _persist)
    else:
        monkeypatch.setattr(
            emails_service, "persist_email_metadata_batch",
            lambda _aid, _meta, **_kw: len(_meta),
        )

    def _set_favorites_true_batch(account_id, provider_message_ids):
        if favorite_calls is not None:
            favorite_calls.append((account_id, list(provider_message_ids)))
        return len(provider_message_ids)
    monkeypatch.setattr(
        emails_service.email_metadata_store, "set_favorites_true_batch",
        _set_favorites_true_batch,
    )


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
        monkeypatch.setattr(emails_service, "build_manager_for_accounts", _explode)

        result = emails_service.get_conversation(
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
        result = emails_service.get_conversation(
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

    def test_lazy_sync_applies_favorite_per_message(self, monkeypatch):
        favorite_calls: list = []
        members = [
            build_conversation_message(provider_message_id="m_fav", is_favorite=True),
            build_conversation_message(provider_message_id="m_plain", is_favorite=False),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            favorite_calls=favorite_calls,
        )
        emails_service.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        # A SINGLE batch re-apply carries only the favourite member's id (the
        # shared upsert does not carry is_favorite); the plain one is excluded.
        assert favorite_calls == [(_ACCOUNT_ID, ["m_fav"])]

    def test_persist_failure_is_best_effort(self, monkeypatch):
        # A lazy-sync persist failure must NOT abort the viewer response.
        members = [build_conversation_message(provider_message_id="m1")]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_exc=RuntimeError("db write failed"),
        )
        result = emails_service.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == "thr-1"
        assert [m.provider_message_id for m in result.messages] == ["m1"]

    def test_email_not_found_when_base_row_missing(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch, base_row=None)
        with pytest.raises(EmailNotFound):
            emails_service.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", _USER_ID,
            )

    def test_account_not_found(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            emails_service.get_conversation(
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
            emails_service.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_get_metadata_database_error_translated(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        from api.errors.exceptions import DatabaseQueryError
        _patch_get_conversation_common(monkeypatch)

        def _raise(_aid, _mid):
            raise DbQueryError("get_metadata fail")
        monkeypatch.setattr(
            emails_service.email_metadata_store, "get_metadata", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            emails_service.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_ownership_checked_first(self, monkeypatch):
        from api.errors.exceptions import Forbidden
        _patch_get_conversation_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Foreign mailbox in conversation ownership test.")
        monkeypatch.setattr(emails_service, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            emails_service.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )


def test_conversation_fetch_error_maps_to_502():
    # Lock the _STATUS_MAP registration: ConversationFetchError → 502, same
    # family as EmailContentFetchError / EmailReplyContextError.
    from fastapi import status
    from api.errors.handlers import _STATUS_MAP
    assert _STATUS_MAP[ConversationFetchError] == status.HTTP_502_BAD_GATEWAY
