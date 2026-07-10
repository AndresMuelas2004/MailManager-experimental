"""Tests espejo de ``drafts_service.adjuntos``: alta / baja de adjuntos y copia desde un email."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    AttachmentBlockedExtension,
    AttachmentLimitExceeded,
    AttachmentMessageSizeExceeded,
    AttachmentTooLarge,
    DatabaseQueryError,
    DraftAttachmentNotFound,
    DraftDeleteError,
    DraftNotFound,
    Forbidden,
)
from api.services.drafts_service import adjuntos
from core.email import EmailManager
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


_DRAFT_ID = "draft-1"


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


def _make_upload(
    filename: str, content: bytes, content_type: str = "application/pdf",
) -> tuple[bytes, str, str]:
    # ``add_draft_attachment`` now takes plain bytes/str (the router reads the
    # multipart UploadFile and passes the body through — M6). The test mirrors
    # that: it provides the same (content, filename, content_type) the router
    # would have extracted, with no FastAPI UploadFile needed.
    return (content, filename, content_type)


def _call_add(
    upload: tuple[bytes, str, str],
    *,
    mailbox_id: str = _MAILBOX_ID,
    account_id: str = _ACCOUNT_ID,
    draft_id: str = _DRAFT_ID,
    user_id: str = _USER_ID,
):
    content, filename, content_type = upload
    return adjuntos.add_draft_attachment(
        mailbox_id,
        account_id,
        draft_id,
        file_content=content,
        filename=filename,
        content_type=content_type,
        user_id=user_id,
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
        adjuntos, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        adjuntos.account_store, "get",
        lambda _mb, _aid: account if account is not None else _fake_account(),
    )
    monkeypatch.setattr(
        adjuntos.draft_store, "get",
        lambda _did, _aid: draft_row if draft_row is not None else _fake_draft_row(),
    )
    monkeypatch.setattr(
        adjuntos.draft_attachment_store, "list_by_draft",
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

    monkeypatch.setattr(adjuntos.draft_attachment_store, "insert", _insert)


# ── add_draft_attachment ───────────────────────────────────────────


class TestAddDraftAttachment:

    def test_happy_path_returns_metadata(self, monkeypatch):
        # Phase 2.6: position is assigned atomically inside the INSERT
        # statement, so the test fakes the post-insert response via
        # ``inserted_overrides`` rather than pre-computing a value the
        # service would have ignored.
        _patch_attachment_common(monkeypatch, inserted_overrides={"position": 2})
        upload = _make_upload("report.pdf", b"PDF-bytes")

        result = _call_add(upload)
        assert result.filename == "report.pdf"
        assert result.mime_type == "application/pdf"
        assert result.size == len(b"PDF-bytes")
        assert result.position == 2
        assert result.provider_attachment_id is None

    def test_blocked_extension_raises_before_persistence(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("malware.exe", b"x")
        with pytest.raises(AttachmentBlockedExtension):
            _call_add(upload)

    def test_blocked_extension_check_is_case_insensitive(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("malware.EXE", b"x")
        with pytest.raises(AttachmentBlockedExtension):
            _call_add(upload)

    def test_oversize_single_file_raises_attachment_too_large(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        upload = _make_upload("big.pdf", b"\x00" * (25 * 1024 * 1024 + 1))
        with pytest.raises(AttachmentTooLarge):
            _call_add(upload)

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
            _call_add(upload)

    def test_cumulative_size_above_25mb_raises_message_size(self, monkeypatch):
        # Existing total is just below 25 MB; the new file pushes it over.
        existing = [{"filename": "a.pdf", "size": 20 * 1024 * 1024, "position": 0}]
        _patch_attachment_common(monkeypatch, existing_attachments=existing)
        upload = _make_upload("more.pdf", b"\x00" * (6 * 1024 * 1024))  # 6 MB
        with pytest.raises(AttachmentMessageSizeExceeded):
            _call_add(upload)

    def test_account_not_found_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.account_store, "get", lambda _mb, _aid: None,
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(AccountNotFound):
            _call_add(upload)

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_store, "get", lambda _did, _aid: None,
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DraftNotFound):
            _call_add(upload)

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_attachment_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("nope")

        monkeypatch.setattr(adjuntos, "ensure_mailbox_access", _raise)
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(Forbidden):
            _call_add(upload)

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

        monkeypatch.setattr(adjuntos.draft_attachment_store, "insert", _insert)
        # Filename has reserved chars + collides with existing "report.pdf".
        upload = _make_upload("report.pdf", b"x")
        result = _call_add(upload)
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

        monkeypatch.setattr(adjuntos.draft_attachment_store, "insert", _insert)
        upload = _make_upload("../etc/passwd.pdf", b"x")
        result = _call_add(upload)
        # `..` is replaced by `-`; path separator likewise.
        assert ".." not in result.filename
        assert "/" not in result.filename

    def test_db_error_on_list_translated(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_by_draft",
            lambda _a, _b: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DatabaseQueryError):
            _call_add(upload)

    def test_db_error_on_insert_translated(self, monkeypatch):
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "insert",
            lambda _row: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(DatabaseQueryError):
            _call_add(upload)

    def test_unexpected_error_on_insert_wrapped(self, monkeypatch):
        # Phase 2.1 fix: insert failures now surface as
        # AttachmentInsertError (one-concept-per-class) instead of being
        # rolled into the generic DraftCreationError.
        from api.errors.exceptions import AttachmentInsertError
        _patch_attachment_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "insert",
            lambda _row: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        upload = _make_upload("ok.pdf", b"x")
        with pytest.raises(AttachmentInsertError):
            _call_add(upload)

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

        monkeypatch.setattr(adjuntos.draft_attachment_store, "insert", _insert)
        upload = _make_upload("ok.pdf", b"x")
        _call_add(upload)
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
        adjuntos, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        adjuntos.account_store, "get", lambda _mb, _aid: _fake_account(),
    )
    effective_row = _attachment_row() if row is _REMOVE_DEFAULT else row
    monkeypatch.setattr(
        adjuntos.draft_attachment_store, "get",
        lambda _id: effective_row,
    )
    monkeypatch.setattr(
        adjuntos.draft_attachment_store, "delete",
        lambda _id: deleted,
    )


class TestRemoveDraftAttachment:

    def test_happy_path_returns_deleted(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        result = adjuntos.remove_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
        )
        assert result == {"status": "deleted"}

    def test_concurrent_delete_returns_deleted_idempotently(self, monkeypatch):
        # Race: row found, but the DELETE returns False because another tab
        # already removed it. Service treats that as success (D-07 idempotence).
        _patch_remove_common(monkeypatch, deleted=False)
        result = adjuntos.remove_draft_attachment(
            _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
        )
        assert result == {"status": "deleted"}

    def test_attachment_not_found_raises(self, monkeypatch):
        _patch_remove_common(monkeypatch, row=None)
        with pytest.raises(DraftAttachmentNotFound):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_attachment_belongs_to_other_draft_raises_not_found(self, monkeypatch):
        _patch_remove_common(
            monkeypatch,
            row=_attachment_row(draft_id="some-other-draft"),
        )
        with pytest.raises(DraftAttachmentNotFound):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_attachment_belongs_to_other_account_raises_not_found(self, monkeypatch):
        _patch_remove_common(
            monkeypatch,
            row=_attachment_row(account_id="some-other-account"),
        )
        with pytest.raises(DraftAttachmentNotFound):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.account_store, "get", lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_get_translated(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "get",
            lambda _id: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_db_error_on_delete_translated(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "delete",
            lambda _id: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )

    def test_unexpected_error_on_delete_wrapped(self, monkeypatch):
        _patch_remove_common(monkeypatch)
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "delete",
            lambda _id: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(DraftDeleteError):
            adjuntos.remove_draft_attachment(
                _MAILBOX_ID, _ACCOUNT_ID, _DRAFT_ID, _DRAFT_ATTACHMENT_ID, _USER_ID,
            )


# ==================================================================
# copy_attachments_from_email
# ==================================================================


_COPY_DRAFT_ID = "draft_copy"
_SOURCE_ACCOUNT_ID = "00000000-0000-4000-a000-000000000abc"
_SOURCE_MESSAGE_ID = "src-msg-1"
_SOURCE_ATTACHMENT_ID = "11111111-1111-4000-a000-aaaaaaaaaaaa"


def _patch_copy_attachments_common(monkeypatch, *, draft_provider: str = "gmail"):
    """Patch helpers needed by copy_attachments_from_email tests."""
    _patch_common(monkeypatch, adjuntos)

    # Draft account.
    def _get(_mb, _aid):
        if _aid != _ACCOUNT_ID:
            return None
        return {
            "account_id": _ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": draft_provider,
            "display_label": f"{draft_provider}:{_ACCOUNT_ID}",
            "email_address": "me@me.com",
        }
    monkeypatch.setattr(adjuntos.account_store, "get", _get)

    # Source account ownership (D-22 single-JOIN).
    monkeypatch.setattr(
        adjuntos.account_store, "get_by_id_for_user",
        lambda _aid, _uid: {
            "account_id": _SOURCE_ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": "gmail",
            "display_label": "gmail:src",
            "email_address": "src@me.com",
        } if _aid == _SOURCE_ACCOUNT_ID else None,
    )

    # Draft exists.
    monkeypatch.setattr(
        adjuntos.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _COPY_DRAFT_ID else None,
    )

    # Source email metadata exists.
    monkeypatch.setattr(
        adjuntos.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )

    # Loader for the final response (returns the current draft attachments).
    monkeypatch.setattr(
        adjuntos, "_load_draft_attachments_metadata_out",
        lambda _aid, _did: [],
    )


class TestCopyAttachmentsFromEmail:
    """Covers R-06 / R-12 — server-side copy of Forward attachments."""

    def _seed_one_source_attachment(
        self, monkeypatch, *, blob: bytes | None = b"BLOB-BYTES",
        unavailable_at=None,
    ):
        # Source email_attachments rows.
        rows = [{
            "attachment_id": _SOURCE_ATTACHMENT_ID,
            "filename": "src.pdf",
            "mime_type": "application/pdf",
            "size": len(blob) if blob else 1000,
            "content_id": None,
            "is_inline": False,
            "position": 0,
            "part_id": "1",
            "provider_attachment_id": None,
            "unavailable_at": unavailable_at,
        }]
        monkeypatch.setattr(
            adjuntos.email_attachment_store, "list_by_message",
            lambda _aid, _mid: rows,
        )
        monkeypatch.setattr(
            adjuntos.email_attachment_store, "get_blob",
            lambda _aid: blob,
        )
        # No prior copies — idempotency check returns empty.
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: set(),
        )
        # No pre-existing chips → counts start at zero.
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [],
        )
        inserts: list[dict] = []
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "insert",
            lambda row: inserts.append(row) or row,
        )
        return inserts

    def test_outlook_draft_returns_noop(self, monkeypatch):
        # The Outlook code path short-circuits before reading source
        # attachments — createForward already inherited them.
        _patch_copy_attachments_common(monkeypatch, draft_provider="outlook")
        monkeypatch.setattr(
            adjuntos.email_attachment_store, "list_by_message",
            lambda *_a, **_kw: pytest.fail("list_by_message must NOT be called for Outlook"),
        )
        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert result.skipped == []

    def test_gmail_cached_blob_inserts_draft_attachment(self, monkeypatch):
        # Happy path: source attachment row + cached blob → single insert.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=b"BLOB-BYTES")
        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 1
        assert result.skipped == []
        assert len(inserts) == 1
        # R-12 source-tracking columns set on the new row.
        assert inserts[0]["source_account_id"] == _SOURCE_ACCOUNT_ID
        assert inserts[0]["source_attachment_id"] == _SOURCE_ATTACHMENT_ID
        # Blob copied from the cache.
        assert inserts[0]["blob"] == b"BLOB-BYTES"

    def test_gmail_missing_blob_falls_back_to_provider_download(self, monkeypatch):
        # Cache miss path: fetch_attachment_binary is invoked, then
        # the bytes are persisted (cache-aside) and the draft row inserted.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=None)
        # Track insert_blob calls for cache-aside.
        blob_inserts: list[tuple] = []
        monkeypatch.setattr(
            adjuntos.email_attachment_store, "insert_blob",
            lambda aid, b: blob_inserts.append((aid, b)),
        )
        # Reconfigure the source manager fake to return a binary.
        from core.email.email_client import AttachmentBinary

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    fetch_attachment_binary_return=AttachmentBinary(
                        mime_type="application/pdf",
                        filename="src.pdf",
                        data=b"DOWNLOADED-BYTES",
                        size=16,
                    ),
                ))
            return manager
        monkeypatch.setattr(adjuntos, "build_manager_for_accounts", _build)

        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 1
        assert len(inserts) == 1
        # Blob was persisted into email_attachment_blobs (cache-aside).
        assert blob_inserts == [(_SOURCE_ATTACHMENT_ID, b"DOWNLOADED-BYTES")]
        # The draft attachment carries the downloaded bytes.
        assert inserts[0]["blob"] == b"DOWNLOADED-BYTES"

    def test_unavailable_at_source_skipped_with_reason(self, monkeypatch):
        # An attachment marked unavailable (D-17) is skipped without
        # touching the provider.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(
            monkeypatch, blob=b"DOES-NOT-MATTER", unavailable_at="2026-01-01T00:00:00Z",
        )
        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "unavailable_at_source"
        # Nothing inserted.
        assert inserts == []

    def test_idempotency_skips_already_copied_rows(self, monkeypatch):
        # R-12: a retry must NOT duplicate rows. ``list_existing_source_attachment_ids``
        # returns the ids we already copied; those are skipped.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=b"X")
        # Pretend the attachment was already copied.
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: {_SOURCE_ATTACHMENT_ID},
        )
        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "already_copied"
        assert inserts == []

    def test_source_email_not_found_returns_404(self, monkeypatch):
        from api.errors.exceptions import EmailNotFound
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        monkeypatch.setattr(
            adjuntos.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )
        with pytest.raises(EmailNotFound):
            adjuntos.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
                _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_source_account_unknown_returns_account_not_found(self, monkeypatch):
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        # D-22 anti-leak: foreign / unknown account → 404 account_not_found.
        monkeypatch.setattr(
            adjuntos.account_store, "get_by_id_for_user",
            lambda _aid, _uid: None,
        )
        with pytest.raises(AccountNotFound):
            adjuntos.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
                _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_draft_not_found_short_circuits(self, monkeypatch):
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        monkeypatch.setattr(
            adjuntos.draft_store, "get",
            lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            adjuntos.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", _SOURCE_ACCOUNT_ID,
                _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_inline_attachments_filtered_out(self, monkeypatch):
        # Only ``is_inline=False`` rows are candidates; inline images
        # are excluded (D-13 strict + R-06).
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        rows = [
            {
                "attachment_id": _SOURCE_ATTACHMENT_ID,
                "filename": "inline.png",
                "mime_type": "image/png",
                "size": 100,
                "content_id": "cid-1",
                "is_inline": True,  # excluded
                "position": 0,
                "part_id": "1",
                "provider_attachment_id": None,
                "unavailable_at": None,
            },
        ]
        monkeypatch.setattr(
            adjuntos.email_attachment_store, "list_by_message",
            lambda _aid, _mid: rows,
        )
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: set(),
        )
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [],
        )
        inserts: list[dict] = []
        monkeypatch.setattr(
            adjuntos.draft_attachment_store, "insert",
            lambda row: inserts.append(row) or row,
        )
        result = adjuntos.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        # Inline rows pre-filtered → no copies.
        assert result.copied_count == 0
        assert inserts == []
