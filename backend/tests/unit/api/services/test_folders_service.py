"""Unit tests for ``api.services.folders_service``.

Every external dependency (the folder / account / email-metadata stores plus the
``services_helpers`` leaf functions the service imports by name) is
monkeypatched, so the tests run without DB or provider access. The Provider-First
insert-only-on-success contract of the assignment itself lives in
``services_helpers/test_carpetas.py`` — here the service just delegates to the
already-patched ``assign_folder_provider_first`` spy.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    DatabaseQueryError,
    EmailNotFound,
    FolderNameConflict,
    FolderNotFound,
)
from api.schemas.folder import FolderCreate, FolderRef, FolderUpdate
from api.services import folders_service
from database.errors.exceptions import QueryError


_USER_ID = "user-1"
_MAILBOX_ID = "mb-1"
_ACCOUNT_ID = "acc-1"
_FOLDER_ID = "folder-1"
_PMID = "m1"
_ISO = datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat()


def _folder_record(*, folder_id=_FOLDER_ID, owner_user_id=_USER_ID, name="Universidad", color=None):
    return {
        "folder_id": folder_id,
        "owner_user_id": owner_user_id,
        "name": name,
        "color": color,
        "created_at": _ISO,
        "updated_at": _ISO,
    }


def _account_record():
    return {
        "account_id": _ACCOUNT_ID,
        "mailbox_id": _MAILBOX_ID,
        "provider": "gmail",
        "email_address": "me@example.com",
    }


def _listing_row(pmid=_PMID):
    return {
        "provider_message_id": pmid,
        "account_id": _ACCOUNT_ID,
        "thread_id": None,
        "from_email": "a@x.com",
        "from_name": "A",
        "subject": "hi",
        "received_at": datetime(2026, 5, 10, tzinfo=timezone.utc),
        "is_read": False,
        "box": "ALL_MAIL",
        "has_attachments": False,
        "is_favorite": False,
        "to_email": "",
        "to_name": "",
        "mailbox_id": _MAILBOX_ID,
    }


# ---------------------------------------------------------------------------
# Ownership + name conflict
# ---------------------------------------------------------------------------


class TestOwnership:
    def test_get_folder_foreign_owner_is_404(self, monkeypatch):
        monkeypatch.setattr(
            folders_service.folder_store, "get",
            lambda _fid: _folder_record(owner_user_id="someone-else"),
        )
        with pytest.raises(FolderNotFound):
            folders_service.get_folder(_FOLDER_ID, _USER_ID)

    def test_get_folder_missing_is_404(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: None)
        with pytest.raises(FolderNotFound):
            folders_service.get_folder(_FOLDER_ID, _USER_ID)

    def test_get_folder_owned_returns_model(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        result = folders_service.get_folder(_FOLDER_ID, _USER_ID)
        assert result.folder_id == _FOLDER_ID
        assert result.name == "Universidad"


class TestCreateFolder:
    def test_happy_path_persists_owner_and_returns_model(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "list_by_owner", lambda _uid: [])
        captured = {}

        def _create(row):
            captured["row"] = row
            return _folder_record(name=row["name"], color=row["color"])

        monkeypatch.setattr(folders_service.folder_store, "create", _create)

        result = folders_service.create_folder(_USER_ID, FolderCreate(name="Universidad"))
        assert result.name == "Universidad"
        assert captured["row"]["owner_user_id"] == _USER_ID
        assert "folder_id" in captured["row"]

    def test_case_insensitive_name_conflict_is_409(self, monkeypatch):
        monkeypatch.setattr(
            folders_service.folder_store, "list_by_owner",
            lambda _uid: [_folder_record(name="universidad")],
        )
        with pytest.raises(FolderNameConflict):
            folders_service.create_folder(_USER_ID, FolderCreate(name="Universidad"))

    def test_db_error_translates_to_503(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "list_by_owner", lambda _uid: [])

        def _boom(_row):
            raise QueryError("boom")

        monkeypatch.setattr(folders_service.folder_store, "create", _boom)
        with pytest.raises(DatabaseQueryError):
            folders_service.create_folder(_USER_ID, FolderCreate(name="Work"))


class TestUpdateFolder:
    def test_rename_conflict_is_409(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record(name="Old"))
        monkeypatch.setattr(
            folders_service.folder_store, "list_by_owner",
            lambda _uid: [
                _folder_record(name="Old"),
                _folder_record(folder_id="other", name="Taken"),
            ],
        )
        with pytest.raises(FolderNameConflict):
            folders_service.update_folder(_FOLDER_ID, _USER_ID, FolderUpdate(name="Taken"))

    def test_rename_reflects_to_provider_before_local_write(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record(name="Old"))
        monkeypatch.setattr(folders_service.folder_store, "list_by_owner", lambda _uid: [_folder_record(name="Old")])
        reflected = {}
        monkeypatch.setattr(
            folders_service, "_reflect_folder_rename",
            lambda fid, uid, old, new: reflected.update(old=old, new=new),
        )
        monkeypatch.setattr(
            folders_service.folder_store, "update",
            lambda row: _folder_record(name=row["name"], color=row["color"]),
        )
        result = folders_service.update_folder(_FOLDER_ID, _USER_ID, FolderUpdate(name="New"))
        assert result.name == "New"
        assert reflected == {"old": "Old", "new": "New"}

    def test_recolour_only_does_not_reflect_rename(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record(name="Keep"))
        called = {"reflect": False}
        monkeypatch.setattr(
            folders_service, "_reflect_folder_rename",
            lambda *a: called.__setitem__("reflect", True),
        )
        monkeypatch.setattr(
            folders_service.folder_store, "update",
            lambda row: _folder_record(name=row["name"], color=row["color"]),
        )
        result = folders_service.update_folder(_FOLDER_ID, _USER_ID, FolderUpdate(color="#fff"))
        assert result.color == "#fff"
        assert called["reflect"] is False

    def test_update_row_vanished_is_404(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record(name="Keep"))
        monkeypatch.setattr(folders_service.folder_store, "update", lambda _row: None)
        with pytest.raises(FolderNotFound):
            folders_service.update_folder(_FOLDER_ID, _USER_ID, FolderUpdate(color="#fff"))


class TestDeleteFolder:
    def test_happy_path_reflects_then_deletes(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        order = []
        monkeypatch.setattr(folders_service, "_reflect_folder_delete", lambda *a: order.append("reflect"))
        monkeypatch.setattr(folders_service.folder_store, "delete", lambda _fid: order.append("delete") or True)
        folders_service.delete_folder(_FOLDER_ID, _USER_ID)
        assert order == ["reflect", "delete"]

    def test_delete_zero_rows_is_404(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        monkeypatch.setattr(folders_service, "_reflect_folder_delete", lambda *a: None)
        monkeypatch.setattr(folders_service.folder_store, "delete", lambda _fid: False)
        with pytest.raises(FolderNotFound):
            folders_service.delete_folder(_FOLDER_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Per-email assign / unassign
# ---------------------------------------------------------------------------


def _wire_resolve_chain(monkeypatch, *, account=True, exists=True):
    monkeypatch.setattr(folders_service, "ensure_mailbox_access", lambda *a: None)
    monkeypatch.setattr(
        folders_service.account_store, "get",
        lambda _mid, _aid: _account_record() if account else None,
    )
    monkeypatch.setattr(folders_service.email_metadata_store, "exists", lambda *a: exists)
    monkeypatch.setattr(
        folders_service, "authenticate_single_account",
        lambda account, mailbox_id, *, fallback: ("MANAGER", "mb-1__acc-1"),
    )
    monkeypatch.setattr(
        folders_service, "fetch_folder_chips",
        lambda pairs: {(_PMID, _ACCOUNT_ID): [FolderRef(folder_id=_FOLDER_ID, name="Universidad", color=None)]},
    )


class TestAssignFolderToEmail:
    def test_happy_path_provider_first_returns_chips(self, monkeypatch):
        _wire_resolve_chain(monkeypatch)
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        captured = {}
        monkeypatch.setattr(
            folders_service, "assign_folder_provider_first",
            lambda *a, **k: captured.update(args=a),
        )
        result = folders_service.assign_folder_to_email(
            _MAILBOX_ID, _ACCOUNT_ID, _PMID, _FOLDER_ID, _USER_ID,
        )
        assert [f.folder_id for f in result.folders] == [_FOLDER_ID]
        assert captured["args"][3] == _PMID  # provider_message_id forwarded

    def test_missing_account_is_404(self, monkeypatch):
        _wire_resolve_chain(monkeypatch, account=False)
        with pytest.raises(AccountNotFound):
            folders_service.assign_folder_to_email(
                _MAILBOX_ID, _ACCOUNT_ID, _PMID, _FOLDER_ID, _USER_ID,
            )

    def test_missing_email_is_404(self, monkeypatch):
        _wire_resolve_chain(monkeypatch, exists=False)
        with pytest.raises(EmailNotFound):
            folders_service.assign_folder_to_email(
                _MAILBOX_ID, _ACCOUNT_ID, _PMID, _FOLDER_ID, _USER_ID,
            )

    def test_foreign_folder_is_404(self, monkeypatch):
        _wire_resolve_chain(monkeypatch)
        monkeypatch.setattr(
            folders_service.folder_store, "get",
            lambda _fid: _folder_record(owner_user_id="someone-else"),
        )
        with pytest.raises(FolderNotFound):
            folders_service.assign_folder_to_email(
                _MAILBOX_ID, _ACCOUNT_ID, _PMID, _FOLDER_ID, _USER_ID,
            )


class TestUnassignFolderFromEmail:
    def test_happy_path_calls_provider_first_unassign(self, monkeypatch):
        _wire_resolve_chain(monkeypatch)
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        called = {"unassign": False}
        monkeypatch.setattr(
            folders_service, "unassign_folder_provider_first",
            lambda *a, **k: called.__setitem__("unassign", True),
        )
        result = folders_service.unassign_folder_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _PMID, _FOLDER_ID, _USER_ID,
        )
        assert called["unassign"] is True
        assert result.folders[0].folder_id == _FOLDER_ID


# ---------------------------------------------------------------------------
# Folder email listing
# ---------------------------------------------------------------------------


class TestListFolderEmails:
    def test_foreign_folder_is_404(self, monkeypatch):
        monkeypatch.setattr(
            folders_service.folder_store, "get",
            lambda _fid: _folder_record(owner_user_id="other"),
        )
        with pytest.raises(FolderNotFound):
            folders_service.list_folder_emails(_FOLDER_ID, _USER_ID)

    def test_no_owned_accounts_returns_empty_page_without_query(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        monkeypatch.setattr(folders_service.account_store, "list_account_ids_by_user", lambda _uid: [])
        # If list_filtered were reached the test would fail (no stub) — the
        # empty-accounts short-circuit avoids the DB entirely.
        page = folders_service.list_folder_emails(_FOLDER_ID, _USER_ID)
        assert page.items == []
        assert page.total == 0

    def test_happy_path_forwards_folder_id_and_enriches(self, monkeypatch):
        monkeypatch.setattr(folders_service.folder_store, "get", lambda _fid: _folder_record())
        monkeypatch.setattr(
            folders_service.account_store, "list_account_ids_by_user",
            lambda _uid: [_ACCOUNT_ID],
        )
        captured = {}

        def _list_filtered(account_ids, box, tokens, limit, offset, **kwargs):
            captured["list_kwargs"] = kwargs
            return [_listing_row()]

        def _count_filtered(account_ids, box, tokens, **kwargs):
            captured["count_kwargs"] = kwargs
            return 1

        monkeypatch.setattr(folders_service.email_metadata_store, "list_filtered", _list_filtered)
        monkeypatch.setattr(folders_service.email_metadata_store, "count_filtered", _count_filtered)
        enriched = {"called": False}
        monkeypatch.setattr(
            folders_service, "enrich_items_with_folders",
            lambda items: enriched.__setitem__("called", True),
        )

        page = folders_service.list_folder_emails(_FOLDER_ID, _USER_ID)
        assert page.total == 1
        assert len(page.items) == 1
        # Both list AND count must carry folder_id (else page and total diverge)
        # plus the dedup + group-by-thread flags of the unified folder listing.
        assert captured["list_kwargs"]["folder_id"] == _FOLDER_ID
        assert captured["count_kwargs"]["folder_id"] == _FOLDER_ID
        assert captured["list_kwargs"]["distinct_provider_message_id"] is True
        assert captured["list_kwargs"]["group_by_thread"] is True
        assert enriched["called"] is True
