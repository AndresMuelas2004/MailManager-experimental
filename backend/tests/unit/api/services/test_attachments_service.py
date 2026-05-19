"""
Unit tests for ``attachments_service`` — cache-aside download + admin
purge endpoint, mocked at the store boundary so no DB or provider runs.
"""

from __future__ import annotations

import os

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    AttachmentNotFound,
    AttachmentProviderForbidden,
    AttachmentProviderUnavailable,
    AttachmentUnavailable,
    DatabaseQueryError,
    InvalidAdminToken,
    PurgeDisabled,
)
from api.services import attachments_service
from core.email import AttachmentBinary, EmailAttachmentDownloadFailed, EmailManager
from core.email.errors import EmailAttachmentNotFound as CoreAttachmentNotFound
from database.errors import QueryError as DbQueryError
from tests.shared.email_fakes import FakeEmailClient


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_PROVIDER_MESSAGE_ID = "msg1"
_ATTACHMENT_ID = "att-uuid-1"
_LABEL = f"{_MAILBOX_ID}__{_ACCOUNT_ID}"


def _row(*, blob_present: bool = True, unavailable: bool = False, mid: str = _PROVIDER_MESSAGE_ID) -> dict:
    return {
        "attachment_id": _ATTACHMENT_ID,
        "account_id": _ACCOUNT_ID,
        "provider_message_id": mid,
        "part_id": "0.1",
        "provider_attachment_id": None,
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size": 4,
        "content_id": None,
        "is_inline": False,
        "position": 0,
        "unavailable_at": "2024-01-01T00:00:00" if unavailable else None,
    }


def _patch_download_common(
    monkeypatch,
    *,
    row: dict | None = None,
    blob: bytes | None = b"data",
    fake_client_kwargs: dict | None = None,
) -> list[FakeEmailClient]:
    monkeypatch.setattr(
        attachments_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        attachments_service.email_attachment_store, "get_for_download",
        lambda **_kw: row,
    )
    monkeypatch.setattr(
        attachments_service.email_attachment_store, "get_blob",
        lambda _aid: blob,
    )
    monkeypatch.setattr(
        attachments_service.email_attachment_store, "insert_blob",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        attachments_service.email_attachment_store, "mark_unavailable",
        lambda _aid: None,
    )
    monkeypatch.setattr(
        attachments_service.account_store, "get",
        lambda _mb, _aid: {
            "account_id": _ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": "gmail",
        },
    )
    monkeypatch.setattr(
        attachments_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        attachments_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        attachments_service.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    captured: list[FakeEmailClient] = []

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            client = FakeEmailClient(
                label,
                auth_silent_return=None,
                **(fake_client_kwargs or {}),
            )
            captured.append(client)
            manager.add_client(client)
        return manager

    monkeypatch.setattr(attachments_service, "build_manager_for_accounts", _build)
    return captured


# ── download_email_attachment ──────────────────────────────────────


class TestDownloadEmailAttachment:

    def test_cache_hit_returns_blob_without_provider_call(self, monkeypatch):
        captured = _patch_download_common(
            monkeypatch, row=_row(), blob=b"hello world",
        )
        chunks, mime, filename, size = attachments_service.download_email_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
        )
        assert mime == "application/pdf"
        assert filename == "report.pdf"
        assert size == len(b"hello world")
        assert b"".join(chunks) == b"hello world"
        # Cache hit: build_manager_for_accounts must not be invoked.
        assert captured == []

    def test_cache_miss_fetches_from_provider_and_persists(self, monkeypatch):
        binary = AttachmentBinary(
            mime_type="application/pdf",
            filename="report.pdf",
            data=b"PDF bytes",
            size=9,
        )
        captured = _patch_download_common(
            monkeypatch,
            row=_row(),
            blob=None,
            fake_client_kwargs={"fetch_attachment_binary_return": binary},
        )
        inserts: list[tuple] = []
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "insert_blob",
            lambda aid, blob: inserts.append((aid, blob)),
        )
        chunks, mime, filename, size = attachments_service.download_email_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
        )
        assert b"".join(chunks) == b"PDF bytes"
        assert mime == "application/pdf"
        assert filename == "report.pdf"
        assert size == 9
        # Provider was queried exactly once and the blob was persisted.
        assert len(captured) == 1
        assert len(captured[0].fetch_attachment_binary_calls) == 1
        assert inserts == [(_ATTACHMENT_ID, b"PDF bytes")]

    def test_row_missing_raises_attachment_not_found(self, monkeypatch):
        _patch_download_common(monkeypatch, row=None)
        with pytest.raises(AttachmentNotFound):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_unavailable_at_set_raises_unavailable(self, monkeypatch):
        _patch_download_common(monkeypatch, row=_row(unavailable=True))
        with pytest.raises(AttachmentUnavailable):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_provider_message_id_mismatch_raises_not_found(self, monkeypatch):
        # The lookup returns a row, but the row's provider_message_id
        # disagrees with the URL — the service treats this as not-found
        # (defence in depth on top of the SQL ownership chain).
        _patch_download_common(
            monkeypatch, row=_row(mid="some_other_message"),
        )
        with pytest.raises(AttachmentNotFound):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_provider_returns_404_marks_unavailable_and_raises(self, monkeypatch):
        captured = _patch_download_common(
            monkeypatch,
            row=_row(),
            blob=None,
            fake_client_kwargs={
                "fetch_attachment_binary_exc": CoreAttachmentNotFound("gone"),
            },
        )
        marked: list[str] = []
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "mark_unavailable",
            lambda aid: marked.append(aid),
        )
        with pytest.raises(AttachmentUnavailable):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )
        # mark_unavailable was called with the right id on a 404 path.
        assert marked == [_ATTACHMENT_ID]
        assert len(captured) == 1

    def test_provider_403_translated_to_provider_forbidden(self, monkeypatch):
        _patch_download_common(
            monkeypatch,
            row=_row(),
            blob=None,
            fake_client_kwargs={
                "fetch_attachment_binary_exc": EmailAttachmentDownloadFailed(
                    "forbidden", {"reason": "forbidden"},
                ),
            },
        )
        with pytest.raises(AttachmentProviderForbidden):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_provider_5xx_translated_to_provider_unavailable(self, monkeypatch):
        _patch_download_common(
            monkeypatch,
            row=_row(),
            blob=None,
            fake_client_kwargs={
                "fetch_attachment_binary_exc": EmailAttachmentDownloadFailed(
                    "5xx", {"reason": "unavailable"},
                ),
            },
        )
        with pytest.raises(AttachmentProviderUnavailable):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_lookup_translated(self, monkeypatch):
        _patch_download_common(monkeypatch, row=_row())
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "get_for_download",
            lambda **_kw: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_get_blob_translated(self, monkeypatch):
        _patch_download_common(monkeypatch, row=_row(), blob=None)
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "get_blob",
            lambda _aid: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )

    def test_account_missing_during_cache_miss_raises_account_not_found(self, monkeypatch):
        _patch_download_common(monkeypatch, row=_row(), blob=None)
        monkeypatch.setattr(
            attachments_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            attachments_service.download_email_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_MESSAGE_ID, _ATTACHMENT_ID, _USER_ID,
            )


# ── purge_expired_attachments ──────────────────────────────────────


class TestPurgeExpiredAttachments:

    def test_no_env_var_raises_purge_disabled(self, monkeypatch):
        monkeypatch.delenv("ATTACHMENTS_PURGE_TOKEN", raising=False)
        with pytest.raises(PurgeDisabled):
            attachments_service.purge_expired_attachments("any-token")

    def test_wrong_token_raises_invalid_admin_token(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token")
        with pytest.raises(InvalidAdminToken):
            attachments_service.purge_expired_attachments("wrong-token")

    def test_missing_token_raises_invalid_admin_token(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token")
        with pytest.raises(InvalidAdminToken):
            attachments_service.purge_expired_attachments(None)

    def test_empty_token_when_env_set_raises_invalid(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token")
        with pytest.raises(InvalidAdminToken):
            attachments_service.purge_expired_attachments("")

    def test_correct_token_runs_purge_and_returns_stats(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token")
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "purge_expired_blobs",
            lambda: (3, 12345),
        )
        result = attachments_service.purge_expired_attachments("expected-token")
        assert result.purged_count == 3
        assert result.freed_bytes == 12345

    def test_db_error_translated(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "tk")
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "purge_expired_blobs",
            lambda: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            attachments_service.purge_expired_attachments("tk")

    def test_unexpected_exception_wrapped_into_api_error(self, monkeypatch):
        monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "tk")
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "purge_expired_blobs",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(ApiError):
            attachments_service.purge_expired_attachments("tk")


# ── touch_attachment_last_accessed ─────────────────────────────────


class TestTouchAttachmentLastAccessed:

    def test_happy_path_calls_store(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "touch_last_accessed",
            lambda aid: calls.append(aid),
        )
        attachments_service.touch_attachment_last_accessed("att-1")
        assert calls == ["att-1"]

    def test_swallows_exception(self, monkeypatch):
        # The TTL hint is best-effort: a transient DB hiccup must NOT
        # bubble up to the streaming response.
        monkeypatch.setattr(
            attachments_service.email_attachment_store, "touch_last_accessed",
            lambda _aid: (_ for _ in ()).throw(RuntimeError("transient")),
        )
        # No raise expected.
        attachments_service.touch_attachment_last_accessed("att-1")
