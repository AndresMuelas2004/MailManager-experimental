"""
Unit tests for ``api.services.virtual_mailboxes_service``.

All external dependencies (mailbox / account / virtual mailbox stores
and ``email_metadata_store``) are monkeypatched so tests run without
DB or provider access.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    Forbidden,
    MailboxNotFound,
    VirtualMailboxListError,
    VirtualMailboxNotFound,
    VirtualMailboxOperationError,
)
from api.schemas.virtual_mailbox import (
    VirtualMailboxCreate,
    VirtualMailboxFilterPayload,
    VirtualMailboxScopePayload,
    VirtualMailboxUpdate,
)
from api.services import virtual_mailboxes_service


_USER_ID = "user-1"
_MAILBOX_ID = "mb-1"
_ACCOUNT_ID = "acc-1"


def _fake_mailbox(mailbox_id=_MAILBOX_ID, owner=_USER_ID):
    return {
        "mailbox_id": mailbox_id,
        "owner_user_id": owner,
        "display_name": "fake",
        "created_at": "2026-05-19T00:00:00+00:00",
    }


def _fake_account(account_id=_ACCOUNT_ID, mailbox_id=_MAILBOX_ID):
    return {
        "account_id": account_id,
        "mailbox_id": mailbox_id,
        "provider": "gmail",
        "display_label": "test",
        "email_address": "x@example.com",
    }


def _fake_record(
    *,
    virtual_mailbox_id="vmb-1",
    owner_user_id=_USER_ID,
    display_name="My virtual mailbox",
    scope_kind="all",
    scope_payload=None,
    filter_payload=None,
):
    return {
        "virtual_mailbox_id": virtual_mailbox_id,
        "owner_user_id": owner_user_id,
        "display_name": display_name,
        "scope_kind": scope_kind,
        "scope_payload": scope_payload or {},
        "filter_payload": filter_payload or {},
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat(),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateVirtualMailbox:

    def test_happy_path_scope_all(self, monkeypatch):
        captured = {}

        def _create(row):
            captured["row"] = row
            return _fake_record(
                display_name=row["display_name"],
                scope_kind=row["scope_kind"],
                scope_payload=row["scope_payload"],
                filter_payload=row["filter_payload"],
            )

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "create", _create,
        )

        payload = VirtualMailboxCreate(
            display_name="Newsletters",
            scope_kind="all",
            scope_payload=VirtualMailboxScopePayload(),
            filter_payload=VirtualMailboxFilterPayload(subject_contains="newsletter"),
        )

        result = virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)
        assert result.display_name == "Newsletters"
        assert result.scope_kind == "all"
        assert captured["row"]["owner_user_id"] == _USER_ID
        assert captured["row"]["filter_payload"] == {"subject_contains": "newsletter"}

    def test_scope_mailbox_validates_ownership(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "get",
            lambda _mid: _fake_mailbox(owner="someone-else"),
        )
        payload = VirtualMailboxCreate(
            display_name="X",
            scope_kind="mailbox",
            scope_payload=VirtualMailboxScopePayload(mailbox_id=_MAILBOX_ID),
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(Forbidden):
            virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)

    def test_scope_mailbox_missing_raises_404(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "get",
            lambda _mid: None,
        )
        payload = VirtualMailboxCreate(
            display_name="X",
            scope_kind="mailbox",
            scope_payload=VirtualMailboxScopePayload(mailbox_id="missing"),
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(MailboxNotFound):
            virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)

    def test_scope_accounts_rejects_unowned_account(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "list_by_owner",
            lambda _uid: [_fake_mailbox()],
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.account_store, "list_by_mailbox",
            lambda _mid: [_fake_account(account_id="acc-mine")],
        )
        payload = VirtualMailboxCreate(
            display_name="X",
            scope_kind="accounts",
            scope_payload=VirtualMailboxScopePayload(account_ids=["acc-stranger"]),
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(AccountNotFound):
            virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)


class TestUpdateVirtualMailbox:

    def test_full_field_replace(self, monkeypatch):
        original = _fake_record()
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: original,
        )

        captured = {}

        def _update(row):
            captured["row"] = row
            return _fake_record(
                display_name=row["display_name"],
                scope_kind=row["scope_kind"],
                scope_payload=row["scope_payload"],
                filter_payload=row["filter_payload"],
            )

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "update", _update,
        )

        payload = VirtualMailboxUpdate(
            display_name="Newer name",
            scope_kind="all",
            scope_payload=VirtualMailboxScopePayload(),
            filter_payload=VirtualMailboxFilterPayload(is_favorite=True),
        )
        result = virtual_mailboxes_service.update_virtual_mailbox(
            "vmb-1", _USER_ID, payload,
        )
        assert result.display_name == "Newer name"
        assert captured["row"]["filter_payload"] == {"is_favorite": True}

    def test_foreign_record_collapses_to_404(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(owner_user_id="stranger"),
        )
        payload = VirtualMailboxUpdate(
            display_name="X",
            scope_kind="all",
            scope_payload=VirtualMailboxScopePayload(),
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.update_virtual_mailbox("vmb-1", _USER_ID, payload)

    def test_store_returns_none_due_to_race_surfaces_404(self, monkeypatch):
        """Race: ownership pre-check passes but the row is deleted before
        the UPDATE. Repository returns ``None``; service must translate
        to 404 ``VirtualMailboxNotFound`` instead of the old 500 path."""
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "update",
            lambda _row: None,
        )
        payload = VirtualMailboxUpdate(
            display_name="Race-loser",
            scope_kind="all",
            scope_payload=VirtualMailboxScopePayload(),
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.update_virtual_mailbox("vmb-1", _USER_ID, payload)


class TestDeleteVirtualMailbox:

    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )
        deletes: list[str] = []
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "delete",
            lambda _vid: deletes.append(_vid) or True,
        )
        virtual_mailboxes_service.delete_virtual_mailbox("vmb-1", _USER_ID)
        assert deletes == ["vmb-1"]

    def test_foreign_record_collapses_to_404(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(owner_user_id="stranger"),
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.delete_virtual_mailbox("vmb-1", _USER_ID)

    def test_store_returns_false_due_to_race_surfaces_404(self, monkeypatch):
        """Race: ownership pre-check passes but the row vanishes before
        the DELETE. Repository returns ``False``; service must translate
        to 404 instead of silently reporting success."""
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "delete",
            lambda _vid: False,
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.delete_virtual_mailbox("vmb-1", _USER_ID)


# ---------------------------------------------------------------------------
# Email listing
# ---------------------------------------------------------------------------


class TestListEmailsForVirtualMailbox:

    def _patch_listing(self, monkeypatch, *, record, rows=None, list_exc=None):
        captured = {}
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: record,
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "list_by_owner",
            lambda _uid: [_fake_mailbox()],
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "get",
            lambda _mid: _fake_mailbox() if _mid == _MAILBOX_ID else None,
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.account_store, "list_by_mailbox",
            lambda _mid: [_fake_account()] if _mid == _MAILBOX_ID else [],
        )

        def _list(account_ids, box, tokens, limit, offset, *, extra_filters=None, box_in=None, box_not_in=None):
            captured["account_ids"] = account_ids
            captured["box"] = box
            captured["tokens"] = tokens
            captured["limit"] = limit
            captured["offset"] = offset
            captured["extra_filters"] = extra_filters
            captured["box_in"] = box_in
            captured["box_not_in"] = box_not_in
            if list_exc:
                raise list_exc
            return rows or []

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered", _list,
        )
        return captured

    def test_scope_all_aggregates_every_owned_account(self, monkeypatch):
        record = _fake_record(scope_kind="all", scope_payload={})
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert captured["account_ids"] == [_ACCOUNT_ID]
        # Default: exclude trash/spam when no explicit box.
        assert captured["box_not_in"] == ["TRASH", "SPAM"]

    def test_scope_accounts_filters_unowned_silently(self, monkeypatch):
        record = _fake_record(
            scope_kind="accounts",
            scope_payload={"account_ids": [_ACCOUNT_ID, "acc-stranger"]},
        )
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert captured["account_ids"] == [_ACCOUNT_ID]

    def test_filter_payload_translated_to_extra_filters(self, monkeypatch):
        record = _fake_record(
            scope_kind="all",
            filter_payload={
                "subject_contains": "factura",
                "from_domain": "empresa.com",
                "is_favorite": True,
                "is_read": False,
            },
        )
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        extra = captured["extra_filters"]
        assert extra["subject_contains"] == "factura"
        assert extra["from_domain"] == "empresa.com"
        assert extra["is_favorite"] is True
        assert extra["is_read"] is False

    def test_explicit_box_overrides_default_exclusion(self, monkeypatch):
        record = _fake_record(
            scope_kind="all",
            filter_payload={"box": "TRASH"},
        )
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert captured["box"] == "TRASH"
        assert captured["box_not_in"] is None

    def test_explicit_empty_box_not_in_disables_default_exclusion(self, monkeypatch):
        # ``box_not_in=[]`` means "do not exclude any box" — caller is
        # opting back into seeing TRASH and SPAM. The previous truthiness
        # check collapsed the empty list to the default exclusion list,
        # reversing the caller's intent.
        record = _fake_record(
            scope_kind="all",
            filter_payload={"box_not_in": []},
        )
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert captured["box"] is None
        assert captured["box_not_in"] == []

    def test_foreign_virtual_mailbox_raises_404(self, monkeypatch):
        record = _fake_record(owner_user_id="stranger")
        self._patch_listing(monkeypatch, record=record)
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )

    def test_no_resolved_accounts_returns_empty_without_query(self, monkeypatch):
        # Scope is 'all' but the user owns nothing.
        record = _fake_record(scope_kind="all")
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: record,
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.mailbox_store, "list_by_owner",
            lambda _uid: [],
        )
        called = {"list_filtered": False}

        def _fail(*args, **kwargs):
            called["list_filtered"] = True
            raise AssertionError("list_filtered must not be called")

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered", _fail,
        )
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert result == []
        assert called["list_filtered"] is False

    def test_unexpected_listing_error_translated(self, monkeypatch):
        record = _fake_record(scope_kind="all")
        self._patch_listing(monkeypatch, record=record, list_exc=RuntimeError("boom"))
        with pytest.raises(VirtualMailboxListError):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )
