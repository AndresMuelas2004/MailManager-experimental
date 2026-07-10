"""Tests espejo de ``emails_service.sincronizacion``: sync_email_metadata, reconciliacion y prefetch/purga."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    EmailFetchError,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, sincronizacion
from core.email import EmailManager
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient, build_metadata

from ._helpers import (
    _ACCOUNT_ID,
    _ACCOUNT_ID_2,
    _LABEL,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


def _patch_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for sincronizacion tests."""
    monkeypatch.setattr(
        sincronizacion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        sincronizacion.account_store, "list_by_mailbox",
        lambda _mb: [_fake_account()],
    )
    monkeypatch.setattr(
        sincronizacion.account_store, "get",
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
        sincronizacion.account_store, "upsert_tokens",
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

    monkeypatch.setattr(sincronizacion, "build_manager_for_accounts", _build)

    # Stub persistence helpers
    monkeypatch.setattr(sincronizacion, "persist_email_metadata_batch", lambda _aid, _meta, **_kw: len(_meta))
    monkeypatch.setattr(sincronizacion, "delete_email_metadata_batch", lambda _aid, _ids, **_kw: len(_ids))
    monkeypatch.setattr(sincronizacion, "update_email_metadata_labels_batch", lambda _aid, _lu, **_kw: len(_lu))
    monkeypatch.setattr(sincronizacion, "load_sync_cursors", lambda _lookup, **_kw: {})
    monkeypatch.setattr(sincronizacion, "update_sync_cursor", lambda _mb, _acc, _cur, **_kw: None)


class TestSyncEmailMetadata:

    def test_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        # Full success carries an empty ``failed_accounts`` (the field defaults
        # to [] and is only populated on a partial success).
        assert result.failed_accounts == []

    def test_background_tasks_none_schedules_no_prefetch(self, monkeypatch):
        """Direct callers (service tests, scripts) omit ``background_tasks``;
        the post-response prefetch/purge is a best-effort optimisation and must
        simply be skipped when absent, leaving the sync contract unchanged."""
        _patch_common(monkeypatch)
        ran = []
        monkeypatch.setattr(
            sincronizacion, "_run_content_prefetch_and_purge",
            lambda *a, **kw: ran.append(a),
        )
        # No background_tasks kwarg → nothing scheduled, no prefetch run.
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
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
        sincronizacion.sync_email_metadata(
            _MAILBOX_ID, _USER_ID, background_tasks=bt,
        )
        assert len(added) == 1
        fn, args = added[0]
        assert fn is sincronizacion._run_content_prefetch_and_purge
        # args = (manager, prefetch_targets, synced_account_ids)
        _manager, targets, account_ids = args
        assert targets == [(_LABEL, _ACCOUNT_ID)]
        assert account_ids == [_ACCOUNT_ID]

    def test_empty_accounts_returns_zero(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            lambda _mb: [],
        )

        def _build_empty(accounts):
            return EmailManager()

        monkeypatch.setattr(sincronizacion, "build_manager_for_accounts", _build_empty)
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced == 0

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_fetch_core_error_uses_centralized_mapping(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "fetch_exc": EmailExternalAPIError("API fail"),
        })
        with pytest.raises(ExternalAPIError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_fetch_generic_exception_raises_email_fetch_error(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "fetch_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(EmailFetchError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    @staticmethod
    def _partial_builder(broken_account_id, **broken_kwargs):
        """A ``build_manager_for_accounts`` replacement that injects the failure
        kwargs into ONLY the given account's FakeEmailClient. The uniform
        ``fake_client_kwargs`` of ``_patch_common`` cannot express a per-account
        failure, so a partial-success test needs its own builder."""
        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                label = f"{acc.get('mailbox_id', '')}__{acc.get('account_id', '')}"
                kwargs = dict(broken_kwargs) if acc.get("account_id") == broken_account_id else {}
                manager.add_client(FakeEmailClient(
                    label,
                    metadata=[build_metadata()],
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    **kwargs,
                ))
            return manager
        return _build

    def test_partial_auth_failure_reports_account_without_raising(self, monkeypatch):
        # Unified mailbox: one healthy account + one whose token is revoked. The
        # healthy mail still lands (no raise) and the dead account travels in
        # ``failed_accounts`` instead of aborting the sync (Option A).
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            lambda _mb: [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)],
        )
        monkeypatch.setattr(
            sincronizacion, "build_manager_for_accounts",
            self._partial_builder(_ACCOUNT_ID_2, auth_silent_exc=EmailAuthError("token revoked")),
        )

        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

        # Only the healthy account persisted and is reported in ``accounts``.
        assert result.total_synced == 1
        assert [d.account_id for d in result.accounts] == [_ACCOUNT_ID]
        # The broken account is reported, not raised.
        assert len(result.failed_accounts) == 1
        failure = result.failed_accounts[0]
        assert failure.account_id == _ACCOUNT_ID_2
        assert failure.reason == "account_not_connected"

    def test_partial_non_auth_failure_reports_sync_failed_without_raising(self, monkeypatch):
        # A non-auth per-account failure (provider API error) must ALSO not
        # abort a partial success — it is reported with reason ``sync_failed``.
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            lambda _mb: [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)],
        )
        monkeypatch.setattr(
            sincronizacion, "build_manager_for_accounts",
            self._partial_builder(_ACCOUNT_ID_2, fetch_exc=EmailExternalAPIError("provider 500")),
        )

        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

        assert result.total_synced == 1
        assert [d.account_id for d in result.accounts] == [_ACCOUNT_ID]
        assert len(result.failed_accounts) == 1
        assert result.failed_accounts[0].account_id == _ACCOUNT_ID_2
        assert result.failed_accounts[0].reason == "sync_failed"

    def test_all_accounts_failing_still_raises_account_not_connected(self, monkeypatch):
        # Every account's token is dead → 0 synced → genuine total failure → the
        # deferred raise still fires (409), unchanged from before Option A. The
        # uniform ``fake_client_kwargs`` suffices here (both fail identically).
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            lambda _mb: [_fake_account(_ACCOUNT_ID), _fake_account(_ACCOUNT_ID_2)],
        )
        with pytest.raises(AccountNotConnected):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_persists_refreshed_tokens(self, monkeypatch):
        _patch_common(monkeypatch)
        upsert_calls = []
        monkeypatch.setattr(
            sincronizacion.account_store, "upsert_tokens",
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

        monkeypatch.setattr(sincronizacion, "build_manager_for_accounts", _build_refreshing)
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(upsert_calls) >= 1

    def test_sync_cursors_loaded(self, monkeypatch):
        _patch_common(monkeypatch)
        load_calls = []
        original_load = sincronizacion.load_sync_cursors
        monkeypatch.setattr(
            sincronizacion, "load_sync_cursors",
            lambda lookup, **_kw: (load_calls.append(lookup), {})[1],
        )
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(load_calls) == 1

    def test_metadata_persisted(self, monkeypatch):
        _patch_common(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(
            sincronizacion, "persist_email_metadata_batch",
            lambda aid, meta, **_kw: (persist_calls.append((aid, meta)), len(meta))[1],
        )
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert len(persist_calls) >= 1

    def test_list_by_mailbox_database_error(self, monkeypatch):
        from database import DatabaseError
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_list_by_mailbox_unexpected_error(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "list_by_mailbox",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

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

        monkeypatch.setattr(sincronizacion, "build_manager_for_accounts", _build_refreshing)
        monkeypatch.setattr(
            sincronizacion.account_store, "upsert_tokens",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)

    def test_single_account_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID

    def test_single_account_not_found_raises(self, monkeypatch):
        _patch_common(monkeypatch)
        with pytest.raises(AccountNotFound, match="during metadata sync"):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID, "nonexistent")

    def test_single_account_db_error_on_lookup(self, monkeypatch):
        from database import DatabaseError
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "get",
            MagicMock(side_effect=DatabaseError("db fail")),
        )
        from api.errors.exceptions import ApiError
        with pytest.raises(ApiError):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_single_account_unexpected_error_on_lookup(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            sincronizacion.account_store, "get",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        with pytest.raises(EmailFetchError, match="Failed to look up account for metadata sync"):
            sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)


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
            sincronizacion, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["m_ghost"],
        )
        delete_calls = []
        monkeypatch.setattr(
            sincronizacion, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        # m_ghost should have been deleted
        assert any("m_ghost" in ids for _, ids in delete_calls)

    def test_incremental_skips_reconciliation(self, monkeypatch):
        """Incremental (is_full_sync=False) should not trigger reconciliation."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": False,
        })
        load_calls = []
        monkeypatch.setattr(
            sincronizacion, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: (load_calls.append(_aid), [])[1],
        )
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert load_calls == []

    def test_verification_error_skips_gracefully(self, monkeypatch):
        """If verify_message_existence fails, reconciliation should skip."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "verify_exc": RuntimeError("API down"),
        })
        monkeypatch.setattr(
            sincronizacion, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["m_ghost"],
        )
        # Should not raise
        result = sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        assert result.total_synced >= 0

    def test_no_ghosts_means_no_deletes(self, monkeypatch):
        """If the suspect set is empty, no deletes should happen."""
        _patch_common(monkeypatch, fake_client_kwargs={
            "is_full_sync": True,
            "existing_message_ids": ["m1"],
        })
        # SQL diff returns no suspects (every stored id was in the bootstrap set).
        monkeypatch.setattr(
            sincronizacion, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: [],
        )
        delete_calls = []
        monkeypatch.setattr(
            sincronizacion, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
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
            sincronizacion, "load_suspect_message_ids",
            lambda _aid, _boot, **_kw: ["ghost1", "ghost2"],
        )
        delete_calls = []
        monkeypatch.setattr(
            sincronizacion, "delete_email_metadata_batch",
            lambda aid, ids, **_kw: (delete_calls.append((aid, ids)), len(ids))[1],
        )
        sincronizacion.sync_email_metadata(_MAILBOX_ID, _USER_ID)
        all_deleted = [mid for _, ids in delete_calls for mid in ids]
        assert "ghost1" in all_deleted
        assert "ghost2" in all_deleted


class TestRunContentPrefetchAndPurge:

    def test_purges_then_prefetches_every_target(self, monkeypatch):
        """Purge runs first (one indexed DELETE for all accounts), then every
        selected message is fetched + persisted via the shared helper."""
        purge_calls = []
        monkeypatch.setattr(
            sincronizacion, "purge_expired_email_content",
            lambda account_ids: purge_calls.append(account_ids) or 0,
        )
        monkeypatch.setattr(
            sincronizacion, "list_unread_recent_uncached",
            lambda aid, limit, **_kw: {"acc1": ["m1", "m2"], "acc2": ["m3"]}[aid],
        )
        fetched = []
        monkeypatch.setattr(
            sincronizacion, "_fetch_and_persist_email_content",
            lambda _mgr, label, aid, pmid: fetched.append((label, aid, pmid)),
        )

        manager = EmailManager()
        sincronizacion._run_content_prefetch_and_purge(
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
        monkeypatch.setattr(sincronizacion, "purge_expired_email_content", lambda _a: 0)
        seen_limits = []
        monkeypatch.setattr(
            sincronizacion, "list_unread_recent_uncached",
            lambda aid, limit, **_kw: seen_limits.append(limit) or [],
        )
        monkeypatch.setattr(
            sincronizacion, "_fetch_and_persist_email_content",
            lambda *a, **kw: None,
        )

        sincronizacion._run_content_prefetch_and_purge(
            EmailManager(), [("mb1__acc1", "acc1")], ["acc1"],
        )
        assert seen_limits == [sincronizacion._PREFETCH_LIMIT]

    def test_target_selection_failure_skips_account_not_the_rest(self, monkeypatch):
        """If selecting targets for one account raises, that account is skipped
        and the loop continues to the next (best-effort)."""
        monkeypatch.setattr(sincronizacion, "purge_expired_email_content", lambda _a: 0)

        def _select(aid, _limit, **_kw):
            if aid == "acc1":
                raise RuntimeError("selection boom")
            return ["m9"]

        monkeypatch.setattr(sincronizacion, "list_unread_recent_uncached", _select)
        fetched = []
        monkeypatch.setattr(
            sincronizacion, "_fetch_and_persist_email_content",
            lambda _mgr, label, aid, pmid: fetched.append((aid, pmid)),
        )

        # Must not raise — the first account's selection failure is swallowed.
        sincronizacion._run_content_prefetch_and_purge(
            EmailManager(),
            [("mb1__acc1", "acc1"), ("mb1__acc2", "acc2")],
            ["acc1", "acc2"],
        )
        # Only the surviving account's message was prefetched.
        assert fetched == [("acc2", "m9")]

    def test_single_message_fetch_failure_does_not_abort_remaining(self, monkeypatch):
        """A failure fetching one message must not abort the rest of the batch."""
        monkeypatch.setattr(sincronizacion, "purge_expired_email_content", lambda _a: 0)
        monkeypatch.setattr(
            sincronizacion, "list_unread_recent_uncached",
            lambda _aid, _limit, **_kw: ["m1", "m2", "m3"],
        )

        attempted = []

        def _fetch(_mgr, _label, _aid, pmid):
            attempted.append(pmid)
            if pmid == "m2":
                raise RuntimeError("provider hiccup")

        monkeypatch.setattr(sincronizacion, "_fetch_and_persist_email_content", _fetch)

        sincronizacion._run_content_prefetch_and_purge(
            EmailManager(), [("mb1__acc1", "acc1")], ["acc1"],
        )
        # m2 blew up but m3 was still attempted.
        assert attempted == ["m1", "m2", "m3"]
