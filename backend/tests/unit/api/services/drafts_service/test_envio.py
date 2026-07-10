"""Tests espejo de ``drafts_service.envio``: send_draft + propagacion de metadata de respuesta."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    AttachmentSendFailed,
    DatabaseQueryError,
    DraftNotFound,
    DraftSendError,
    ExternalAPIError,
    Forbidden,
)
from api.schemas.draft import DraftSendOut
from api.services.drafts_service import envio
from core.email import EmailManager
from core.email.errors import (
    EmailAttachmentSendFailed,
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
    _patch_common,
    _persisted_row,
)


# ===================================================================
# send_draft
# ===================================================================

_SEND_DRAFT_ID = "draft_abc"


def _patch_send_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for send_draft tests."""
    _patch_common(monkeypatch, envio, fake_client_kwargs=fake_client_kwargs)

    # Pre-check: draft exists
    monkeypatch.setattr(
        envio.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _SEND_DRAFT_ID else None,
    )
    # Best-effort delete (no-op)
    monkeypatch.setattr(envio.draft_store, "delete", lambda _did, _aid: None)
    # Best-effort metadata persist (no-op)
    monkeypatch.setattr(
        envio, "persist_email_metadata_batch",
        lambda _aid, _metas, **_kw: len(_metas),
    )
    # Draft attachments — the unified send path collects them from the
    # store before delegating to the manager. Tests that don't care about
    # attachments default to "no rows" so the path stays focused on the
    # provider behaviour they exercise. Tests that DO care override these
    # patches inline.
    monkeypatch.setattr(
        envio.draft_attachment_store, "list_by_draft",
        lambda _aid, _did: [],
    )
    # The send path uses ``list_by_draft_with_blob`` (single round trip).
    monkeypatch.setattr(
        envio.draft_attachment_store, "list_by_draft_with_blob",
        lambda _aid, _did: [],
    )
    monkeypatch.setattr(
        envio.draft_attachment_store, "update_provider_attachment_id",
        lambda _id, _pid: None,
    )


class TestSendDraft:

    def test_happy_path_returns_draft_send_out(self, monkeypatch):
        _patch_send_common(monkeypatch)
        result = envio.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert isinstance(result, DraftSendOut)
        assert result.status == "sent"
        assert result.provider_message_id == f"sent_{_SEND_DRAFT_ID}"
        assert result.provider == _PROVIDER

    def test_account_not_found_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            envio.account_store, "get", lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            envio.draft_store, "get", lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Access denied.")

        monkeypatch.setattr(envio, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        # The send path goes through ``send_draft_with_attachments`` even
        # for drafts without attachments — it is the unified entry point
        # for the new flow. Inject the failure on that method's kwarg.
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "send_draft_with_attachments_exc": EmailExternalAPIError(
                    "Provider boom",
                ),
            },
        )
        with pytest.raises(ExternalAPIError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_attachment_send_failure_persists_partial_results_then_raises(self, monkeypatch):
        # D-27 partial-success resume: Outlook's non-atomic send raises
        # EmailAttachmentSendFailed carrying the already-uploaded parts in
        # ``detail['succeeded']``. The service must persist their
        # provider_attachment_ids (so a retry skips them) BEFORE re-raising
        # the translated AttachmentSendFailed (502). This branch is distinct
        # from the EmailExternalAPIError / RuntimeError branches, which never
        # touch ``_persist_partial_upload_results``.
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "send_draft_with_attachments_exc": EmailAttachmentSendFailed(
                    "Outlook attachment upload failed mid-flight.",
                    detail={
                        "succeeded": [
                            {"draft_attachment_id": "da-1", "provider_attachment_id": "pa-1"},
                            {"draft_attachment_id": "da-2", "provider_attachment_id": "pa-2"},
                        ],
                        "failed_attachments": [
                            {"draft_attachment_id": "da-3", "filename": "big.pdf", "reason": "upload_failed"},
                        ],
                    },
                ),
            },
        )
        persisted_pairs: list[tuple[str, str]] = []
        monkeypatch.setattr(
            envio.draft_attachment_store, "batch_update_provider_attachment_ids",
            lambda pairs: persisted_pairs.extend(pairs),
        )
        with pytest.raises(AttachmentSendFailed):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )
        # Only the two succeeded parts are stamped; the failed one stays NULL.
        assert persisted_pairs == [("da-1", "pa-1"), ("da-2", "pa-2")]

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "auth_silent_exc": EmailAuthError("Token expired"),
            },
        )
        with pytest.raises(AccountNotConnected):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_db_draft_delete_failure_swallowed(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _explode(_did, _aid):
            raise DbQueryError("DB delete failed")

        monkeypatch.setattr(envio.draft_store, "delete", _explode)
        result = envio.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert result.status == "sent"

    def test_metadata_persist_failure_swallowed(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _explode(_aid, _metas, **_kw):
            raise RuntimeError("persist failed")

        monkeypatch.setattr(
            envio, "persist_email_metadata_batch", _explode,
        )
        result = envio.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert result.status == "sent"

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            envio.account_store, "get",
            lambda _mb, _aid: (_ for _ in ()).throw(DbQueryError("DB fail")),
        )
        with pytest.raises(DatabaseQueryError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_account_db_lookup_unexpected_exception_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(envio.account_store, "get", _raise)
        with pytest.raises(DraftSendError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_db_lookup_error_translated(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("draft lookup failed")

        monkeypatch.setattr(envio.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_db_lookup_unexpected_exception_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(envio.draft_store, "get", _raise)
        with pytest.raises(DraftSendError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "send_draft_with_attachments_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_happy_path(self, monkeypatch):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            envio.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        envio.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_persist_refreshed_tokens_db_error_raises_database_query_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(envio.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(envio.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftSendError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_send_error(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(envio, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftSendError):
            envio.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )


class TestSendDraftPropagatesReplyMetadata:
    """``send_draft`` reads the reply metadata from the local row and
    passes ``in_reply_to`` / ``references`` / ``thread_id`` to
    ``send_draft_with_attachments`` — not from the request body."""

    def test_send_propagates_reply_kwargs_from_local_row(self, monkeypatch):
        _patch_send_common(monkeypatch)
        # Override the draft row to carry reply metadata.
        row = _persisted_row(provider_draft_id=_SEND_DRAFT_ID)
        row["reply_kind"] = "reply"
        row["reply_to_message_id"] = "orig-1"
        row["thread_id"] = "thr-row"
        row["in_reply_to"] = "<orig@x>"
        row["references_header"] = "<older@x> <orig@x>"
        monkeypatch.setattr(
            envio.draft_store, "get",
            lambda _did, _aid: row if _did == _SEND_DRAFT_ID else None,
        )
        # Capture clients to inspect send_draft_with_attachments kwargs.
        captured: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured.append(client)
                manager.add_client(client)
            return manager
        monkeypatch.setattr(envio, "build_manager_for_accounts", _build)

        envio.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert len(captured) == 1
        kw = captured[0].send_draft_with_attachments_reply_kwargs[0]
        # Threading flows from the row, NOT from the request body.
        assert kw["in_reply_to"] == "<orig@x>"
        assert kw["references"] == "<older@x> <orig@x>"
        assert kw["thread_id"] == "thr-row"
