"""
Unit tests for the attachment-related additions in ``drafts_service``:

- ``add_draft_attachment`` — D-01/02/03/04a validation, position calc,
  filename sanitisation, local-only persistence (no provider call).
- ``remove_draft_attachment`` — ownership check, delete, idempotent
  concurrent-delete handling.

Patched at the store boundary so no real DB or provider runs.
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from fastapi import UploadFile

from api.errors.exceptions import (
    AccountNotFound,
    AttachmentBlockedExtension,
    AttachmentLimitExceeded,
    AttachmentMessageSizeExceeded,
    AttachmentTooLarge,
    DatabaseQueryError,
    DraftAttachmentNotFound,
    DraftCreationError,
    DraftDeleteError,
    DraftNotFound,
    Forbidden,
)
from api.services import drafts_service
from database.errors import QueryError as DbQueryError


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_DRAFT_ID = "draft-1"
_PROVIDER = "gmail"


def _fake_account() -> dict:
    return {
        "account_id": _ACCOUNT_ID,
        "mailbox_id": _MAILBOX_ID,
        "provider": _PROVIDER,
        "display_label": "test",
    }


def _fake_draft_row() -> dict:
    return {
        "provider_draft_id": _DRAFT_ID,
        "account_id": _ACCOUNT_ID,
        "to_recipients": [],
        "cc_recipients": [],
        "bcc_recipients": [],
        "subject": "S",
        "body": "B",
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 1, 1),
    }


def _make_upload(filename: str, content: bytes, content_type: str = "application/pdf") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers={"content-type": content_type},
    )


def _patch_attachment_common(
    monkeypatch,
    *,
    account: dict | None = None,
    draft_row: dict | None = None,
    existing_attachments: list[dict] | None = None,
    next_position_value: int = 0,  # kept for backward compat with older callers; ignored
    inserted_overrides: dict | None = None,
):
    # ``next_position_value`` is accepted but unused: Phase 2.6 folded the
    # MAX(position)+1 lookup into INSERT_DRAFT_ATTACHMENT, so the service
    # no longer pre-computes a position. Older tests that still pass the
    # kwarg keep working without changes.
    _ = next_position_value
    monkeypatch.setattr(
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get",
        lambda _mb, _aid: account if account is not None else _fake_account(),
    )
    monkeypatch.setattr(
        drafts_service.draft_store, "get",
        lambda _did, _aid: draft_row if draft_row is not None else _fake_draft_row(),
    )
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "list_by_draft",
        lambda _aid, _did: list(existing_attachments or []),
    )

    def _insert(row):
        # Phase 2.6: ``row`` no longer carries a pre-computed ``position`` —
        # the INSERT statement assigns it atomically via the embedded
        # subquery. Tests fake the column by defaulting to 0.
        result = {
            "draft_attachment_id": str(row.get("draft_attachment_id") or "att-uuid-1"),
            "filename": row.get("filename"),
            "mime_type": row.get("mime_type"),
            "size": row.get("size"),
            "position": row.get("position", 0),
            "provider_attachment_id": None,
        }
        if inserted_overrides:
            result.update(inserted_overrides)
        return result

    monkeypatch.setattr(drafts_service.draft_attachment_store, "insert", _insert)


# ── add_draft_attachment ───────────────────────────────────────────


class TestAddDraftAttachment:

    def test_happy_path_returns_metadata(self, monkeypatch):
        # Phase 2.6: position is assigned atomically inside the INSERT
        # statement, so the test fakes the post-insert response via
        # ``inserted_overrides`` rather than pre-computing a value the
        # service would have ignored.
        _patch_attachment_common(monkeypatch, inserted_overrides={"position": 2})
        upload = _make_upload("report.pdf", b"PDF-bytes")

        result = drafts_service.add_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
        )
        assert result.filename == "report.pdf"
        assert result.mime_type == "application/pdf"
        assert result.size == len(b"PDF-bytes")
        assert result.position == 2
        assert result.provider_attachment_id is None

    def test_blocked_extension_raises_before_persistence(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("malware.exe", b"x")
        with pytest.raises(AttachmentBlockedExtension):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_blocked_extension_check_is_case_insensitive(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("malware.EXE", b"x")
        with pytest.raises(AttachmentBlockedExtension):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_oversize_single_file_raises_attachment_too_large(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("big.pdf", b"\x00" * (25 * 1024 * 1024 + 1))
        with pytest.raises(AttachmentTooLarge):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_at_25_attachments_raises_limit_exceeded(self, monkeypatch):
        existing = [
            {"filename": f"a{i}.pdf", "size": 1, "position": i}
            for i in range(25)
        ]
        _patch_attachment_common(
            monkeypatch, existing_attachments=existing, next_position_value=25,
        )
        upload = _make_upload("yet-another.pdf", b"x")
        with pytest.raises(AttachmentLimitExceeded):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_cumulative_size_above_25mb_raises_message_size(self, monkeypatch):
        # Existing total is just below 25 MB; the new file pushes it over.
        existing = [{"filename": "a.pdf", "size": 20 * 1024 * 1024, "position": 0}]
        _patch_attachment_common(monkeypatch, existing_attachments=existing)
        upload = _make_upload("more.pdf", b"\x00" * (6 * 1024 * 1024))  # 6 MB
        with pytest.raises(AttachmentMessageSizeExceeded):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get", lambda _mb, _aid: None,
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(AccountNotFound):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "get", lambda _did, _aid: None,
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DraftNotFound):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("nope")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(Forbidden):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_filename_sanitised_and_collisions_resolved(self, monkeypatch):
        captured: list[dict] = []
        _patch_attachment_common(
            monkeypatch,
            existing_attachments=[{"filename": "report.pdf", "size": 1, "position": 0}],
        )

        def _insert(row):
            captured.append(row)
            return {
                "draft_attachment_id": str(row["draft_attachment_id"]),
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "size": row["size"],
                "position": row.get("position", 0),
                "provider_attachment_id": None,
            }

        monkeypatch.setattr(drafts_service.draft_attachment_store, "insert", _insert)
        # Filename has reserved chars + collides with existing "report.pdf".
        upload = _make_upload("report.pdf", b"x")
        result = drafts_service.add_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
        )
        # Collision resolution appends ` (1)` before extension.
        assert result.filename == "report (1).pdf"
        assert captured[0]["filename"] == "report (1).pdf"

    def test_filename_sanitisation_replaces_path_separators(self, monkeypatch):
        captured: list[dict] = []
        _patch_attachment_common(monkeypatch)

        def _insert(row):
            captured.append(row)
            return {
                "draft_attachment_id": "x",
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "size": row["size"],
                "position": row.get("position", 0),
                "provider_attachment_id": None,
            }

        monkeypatch.setattr(drafts_service.draft_attachment_store, "insert", _insert)
        upload = _make_upload("../etc/passwd.pdf", b"x")
        result = drafts_service.add_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
        )
        # `..` is replaced by `-`; path separator likewise.
        assert ".." not in result.filename
        assert "/" not in result.filename

    def test_db_error_on_list_translated(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_by_draft",
            lambda _a, _b: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DatabaseQueryError):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_db_error_on_insert_translated(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "insert",
            lambda _row: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DatabaseQueryError):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_unexpected_error_on_insert_wrapped(self, monkeypatch):
        # Phase 2.1 fix: insert failures now surface as
        # AttachmentInsertError (one-concept-per-class) instead of being
        # rolled into the generic DraftCreationError.
        from api.errors.exceptions import AttachmentInsertError
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "insert",
            lambda _row: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(AttachmentInsertError):
            drafts_service.add_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
            )

    def test_position_resolved_atomically_inside_insert(self, monkeypatch):
        # Phase 2.6 fix: ``position`` is no longer pre-computed by the
        # service. The INSERT statement embeds ``COALESCE(MAX(position)+1, 0)``
        # so the row dict passed to the store does NOT carry a ``position``
        # field — the repository ignores any value the caller might supply.
        captured: list[dict] = []
        _patch_attachment_common(monkeypatch)

        def _insert(row):
            captured.append(row)
            return {
                "draft_attachment_id": "x",
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "size": row["size"],
                "position": 7,  # whatever Postgres assigned
                "provider_attachment_id": None,
            }

        monkeypatch.setattr(drafts_service.draft_attachment_store, "insert", _insert)
        upload = _make_upload("ok.pdf", b"x")
        drafts_service.add_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, upload, _USER_ID,
        )
        assert "position" not in captured[0]


# ── remove_draft_attachment ────────────────────────────────────────


_DRAFT_ATTACHMENT_ID = "att-uuid-2"


def _attachment_row(*, account_id: str = _ACCOUNT_ID, draft_id: str = _DRAFT_ID) -> dict:
    return {
        "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
        "account_id": account_id,
        "provider_draft_id": draft_id,
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size": 4,
        "position": 0,
    }


_REMOVE_DEFAULT = object()


def _patch_remove_common(monkeypatch, *, row=_REMOVE_DEFAULT, deleted: bool = True):
    monkeypatch.setattr(
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get", lambda _mb, _aid: _fake_account(),
    )
    effective_row = _attachment_row() if row is _REMOVE_DEFAULT else row
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "get",
        lambda _id: effective_row,
    )
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "delete",
        lambda _id: deleted,
    )


class TestRemoveDraftAttachment:

    def test_happy_path_returns_deleted(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        result = drafts_service.remove_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
        )
        assert result == {"status": "deleted"}

    def test_concurrent_delete_returns_deleted_idempotently(self, monkeypatch):
        # Race: row found, but the DELETE returns False because another tab
        # already removed it. Service treats that as success (D-07 idempotence).
        _patch_remove_common(monkeypatch, deleted=False)
        result = drafts_service.remove_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
        )
        assert result == {"status": "deleted"}

    def test_attachment_not_found_raises(self, monkeypatch):
        _patch_remove_common(monkeypatch, row=None)
        with pytest.raises(DraftAttachmentNotFound):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_attachment_belongs_to_other_draft_raises_not_found(self, monkeypatch):
        _patch_remove_common(
            monkeypatch,
            row=_attachment_row(draft_id="some-other-draft"),
        )
        with pytest.raises(DraftAttachmentNotFound):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_attachment_belongs_to_other_account_raises_not_found(self, monkeypatch):
        _patch_remove_common(
            monkeypatch,
            row=_attachment_row(account_id="some-other-account"),
        )
        with pytest.raises(DraftAttachmentNotFound):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get", lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_get_translated(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "get",
            lambda _id: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_delete_translated(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "delete",
            lambda _id: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_unexpected_error_on_delete_wrapped(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "delete",
            lambda _id: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(DraftDeleteError):
            drafts_service.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )
