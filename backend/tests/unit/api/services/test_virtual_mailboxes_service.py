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
    DatabaseQueryError,
    VirtualMailboxListError,
    VirtualMailboxNotFound,
    VirtualMailboxOperationError,
)
from api.schemas.virtual_mailbox import (
    VirtualMailboxCreate,
    VirtualMailboxFilterPayload,
    VirtualMailboxUpdate,
)
from api.services import virtual_mailboxes_service
from database.errors.exceptions import QueryError


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
    account_ids=None,
    filter_payload=None,
):
    return {
        "virtual_mailbox_id": virtual_mailbox_id,
        "owner_user_id": owner_user_id,
        "display_name": display_name,
        "account_ids": list(account_ids) if account_ids is not None else [_ACCOUNT_ID],
        "filter_payload": filter_payload or {},
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat(),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat(),
    }


def _patch_user_catalogue(monkeypatch, *, accounts=None):
    """Wire monkeypatches so the user owns ``accounts`` (default: [_ACCOUNT_ID])."""
    if accounts is None:
        accounts = [_ACCOUNT_ID]
    monkeypatch.setattr(
        virtual_mailboxes_service.mailbox_store, "list_by_owner",
        lambda _uid: [_fake_mailbox()],
    )
    monkeypatch.setattr(
        virtual_mailboxes_service.account_store, "list_by_mailbox",
        lambda _mid: [_fake_account(account_id=aid) for aid in accounts],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateVirtualMailbox:

    def test_happy_path(self, monkeypatch):
        _patch_user_catalogue(monkeypatch)
        captured = {}

        def _create(row):
            captured["row"] = row
            return _fake_record(
                display_name=row["display_name"],
                account_ids=row["account_ids"],
                filter_payload=row["filter_payload"],
            )

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "create", _create,
        )

        payload = VirtualMailboxCreate(
            display_name="Newsletters",
            account_ids=[_ACCOUNT_ID],
            filter_payload=VirtualMailboxFilterPayload(subject_contains="newsletter"),
        )

        result = virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)
        assert result.display_name == "Newsletters"
        assert result.account_ids == [_ACCOUNT_ID]
        assert captured["row"]["owner_user_id"] == _USER_ID
        assert captured["row"]["filter_payload"] == {"subject_contains": "newsletter"}

    def test_rejects_unowned_account(self, monkeypatch):
        _patch_user_catalogue(monkeypatch, accounts=["acc-mine"])
        payload = VirtualMailboxCreate(
            display_name="X",
            account_ids=["acc-stranger"],
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(AccountNotFound):
            virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)


class TestUpdateVirtualMailbox:

    def test_full_field_replace(self, monkeypatch):
        _patch_user_catalogue(monkeypatch)
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
                account_ids=row["account_ids"],
                filter_payload=row["filter_payload"],
            )

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "update", _update,
        )

        payload = VirtualMailboxUpdate(
            display_name="Newer name",
            account_ids=[_ACCOUNT_ID],
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
            account_ids=[_ACCOUNT_ID],
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.update_virtual_mailbox("vmb-1", _USER_ID, payload)

    def test_store_returns_none_due_to_race_surfaces_404(self, monkeypatch):
        """Race: ownership pre-check passes but the row is deleted before
        the UPDATE. Repository returns ``None``; service must translate
        to 404 ``VirtualMailboxNotFound`` instead of the old 500 path."""
        _patch_user_catalogue(monkeypatch)
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
            account_ids=[_ACCOUNT_ID],
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


class TestListVirtualMailboxes:

    def test_happy_path_returns_all_owned_vmboxes(self, monkeypatch):
        rows = [
            _fake_record(virtual_mailbox_id="vmb-1"),
            _fake_record(virtual_mailbox_id="vmb-2"),
        ]
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "list_by_owner",
            lambda _uid: rows,
        )
        result = virtual_mailboxes_service.list_virtual_mailboxes(_USER_ID)
        assert [r.virtual_mailbox_id for r in result] == ["vmb-1", "vmb-2"]

    def test_unexpected_error_translates_to_operation_error(self, monkeypatch):
        def _raise(_uid):
            raise RuntimeError("boom")
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "list_by_owner",
            _raise,
        )
        with pytest.raises(VirtualMailboxOperationError):
            virtual_mailboxes_service.list_virtual_mailboxes(_USER_ID)


class TestGetVirtualMailbox:

    def test_happy_path_returns_projection(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )
        result = virtual_mailboxes_service.get_virtual_mailbox("vmb-1", _USER_ID)
        assert result.virtual_mailbox_id == "vmb-1"
        assert result.owner_user_id == _USER_ID
        assert result.account_ids == [_ACCOUNT_ID]

    def test_foreign_record_collapses_to_404(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(owner_user_id="stranger"),
        )
        with pytest.raises(VirtualMailboxNotFound):
            virtual_mailboxes_service.get_virtual_mailbox("vmb-1", _USER_ID)


# ---------------------------------------------------------------------------
# Email listing
# ---------------------------------------------------------------------------


class TestListEmailsForVirtualMailbox:

    def _patch_listing(self, monkeypatch, *, record, rows=None, list_exc=None, owned=None):
        captured = {}
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: record,
        )
        _patch_user_catalogue(monkeypatch, accounts=owned if owned is not None else [_ACCOUNT_ID])

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

    def test_passes_persisted_account_ids_when_still_owned(self, monkeypatch):
        record = _fake_record(account_ids=[_ACCOUNT_ID])
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        assert captured["account_ids"] == [_ACCOUNT_ID]
        # Default: exclude trash/spam when no explicit box.
        assert captured["box_not_in"] == ["TRASH", "SPAM"]

    def test_listing_silently_drops_revoked_accounts(self, monkeypatch):
        # Vmbox persisted two account ids but the user only owns one
        # of them now — the second was revoked between create and read.
        record = _fake_record(account_ids=[_ACCOUNT_ID, "acc-revoked"])
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        assert captured["account_ids"] == [_ACCOUNT_ID]

    def test_filter_payload_translated_to_extra_filters(self, monkeypatch):
        record = _fake_record(
            filter_payload={
                "subject_contains": "factura",
                "from_email": "alguien@empresa.com",
                "is_favorite": True,
                "is_read": False,
            },
        )
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        extra = captured["extra_filters"]
        assert extra["subject_contains"] == "factura"
        assert extra["from_email"] == "alguien@empresa.com"
        assert extra["is_favorite"] is True
        assert extra["is_read"] is False

    def test_explicit_box_overrides_default_exclusion(self, monkeypatch):
        record = _fake_record(filter_payload={"box": "TRASH"})
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        assert captured["box"] == "TRASH"
        assert captured["box_not_in"] is None

    def test_explicit_empty_box_not_in_disables_default_exclusion(self, monkeypatch):
        # ``box_not_in=[]`` means "do not exclude any box" — caller is
        # opting back into seeing TRASH and SPAM. The previous truthiness
        # check collapsed the empty list to the default exclusion list,
        # reversing the caller's intent.
        record = _fake_record(filter_payload={"box_not_in": []})
        captured = self._patch_listing(monkeypatch, record=record)
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
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
        # The user owns nothing now — every persisted id collapses to ø.
        record = _fake_record(account_ids=["acc-1"])
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
        record = _fake_record()
        self._patch_listing(monkeypatch, record=record, list_exc=RuntimeError("boom"))
        with pytest.raises(VirtualMailboxListError):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )


# ---------------------------------------------------------------------------
# Dedup helper — collapses duplicate provider_message_id across accounts
# ---------------------------------------------------------------------------


class TestDedupeRowsByProviderMessageId:
    """Cover the scoring tuple inside ``_dedupe_rows_by_provider_message_id``.

    The helper only runs when a virtual mailbox aggregates two accounts
    that share the same provider message (same Gmail / Outlook OAuth
    connected twice). None of the existing tests feed rows with a
    duplicated ``provider_message_id``, so the scoring tuple — and the
    ``ts is None`` fallback — would silently regress.
    """

    def _row(
        self,
        *,
        pmid,
        account_id,
        to_email="",
        to_name="",
        received_at=None,
    ):
        return {
            "provider_message_id": pmid,
            "account_id": account_id,
            "thread_id": None,
            "from_email": "a@x.com",
            "from_name": "A",
            "subject": "shared",
            "received_at": received_at or datetime(2026, 5, 10, tzinfo=timezone.utc),
            "is_read": False,
            "box": "ALL_MAIL",
            "has_attachments": False,
            "is_favorite": False,
            "to_email": to_email,
            "to_name": to_name,
            "mailbox_id": _MAILBOX_ID,
        }

    def _patch_listing(self, monkeypatch, *, rows):
        record = _fake_record(account_ids=[_ACCOUNT_ID, "acc-2"])
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: record,
        )
        _patch_user_catalogue(monkeypatch, accounts=[_ACCOUNT_ID, "acc-2"])
        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered",
            lambda *a, **kw: rows,
        )

    def test_populated_to_email_wins_over_empty(self, monkeypatch):
        rows = [
            self._row(pmid="dup-1", account_id=_ACCOUNT_ID, to_email=""),
            self._row(pmid="dup-1", account_id="acc-2", to_email="real@x.com"),
        ]
        self._patch_listing(monkeypatch, rows=rows)
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert len(result) == 1
        assert result[0].to_email == "real@x.com"

    def test_populated_to_name_breaks_tie_when_to_email_empty(self, monkeypatch):
        rows = [
            self._row(pmid="dup-1", account_id=_ACCOUNT_ID, to_email="", to_name=""),
            self._row(pmid="dup-1", account_id="acc-2", to_email="", to_name="Real Name"),
        ]
        self._patch_listing(monkeypatch, rows=rows)
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert len(result) == 1
        assert result[0].to_name == "Real Name"

    def test_received_at_breaks_full_tie(self, monkeypatch):
        older = datetime(2026, 5, 9, tzinfo=timezone.utc)
        newer = datetime(2026, 5, 10, tzinfo=timezone.utc)
        rows = [
            self._row(pmid="dup-1", account_id=_ACCOUNT_ID, received_at=older),
            self._row(pmid="dup-1", account_id="acc-2", received_at=newer),
        ]
        self._patch_listing(monkeypatch, rows=rows)
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert len(result) == 1
        assert result[0].received_at == newer

    def test_row_with_null_provider_message_id_is_dropped(self, monkeypatch):
        rows = [
            self._row(pmid=None, account_id=_ACCOUNT_ID),
            self._row(pmid="real-1", account_id="acc-2"),
        ]
        self._patch_listing(monkeypatch, rows=rows)
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert [r.provider_message_id for r in result] == ["real-1"]


# ---------------------------------------------------------------------------
# DatabaseError translation — covers the ``except DatabaseError`` branch
# ---------------------------------------------------------------------------


class TestDatabaseErrorTranslation:
    """Cover the ordered ``except DatabaseError`` branch in every service
    function.

    The existing ``test_unexpected_*_error`` tests inject ``RuntimeError``
    and only exercise the trailing ``except Exception`` branch. A regression
    that flips the order (``except Exception`` before ``except
    DatabaseError``) would still keep all of those tests green while
    silently downgrading every ``QueryError`` (HTTP 503) to a generic
    ``VirtualMailboxOperationError`` (HTTP 500).
    """

    def test_create_translates_database_error(self, monkeypatch):
        _patch_user_catalogue(monkeypatch)

        def _raise(_row):
            raise QueryError("simulated DB failure on create")

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "create",
            _raise,
        )
        payload = VirtualMailboxCreate(
            display_name="X",
            account_ids=[_ACCOUNT_ID],
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(DatabaseQueryError):
            virtual_mailboxes_service.create_virtual_mailbox(_USER_ID, payload)

    def test_update_translates_database_error(self, monkeypatch):
        _patch_user_catalogue(monkeypatch)
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )

        def _raise(_row):
            raise QueryError("simulated DB failure on update")

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "update",
            _raise,
        )
        payload = VirtualMailboxUpdate(
            display_name="X",
            account_ids=[_ACCOUNT_ID],
            filter_payload=VirtualMailboxFilterPayload(),
        )
        with pytest.raises(DatabaseQueryError):
            virtual_mailboxes_service.update_virtual_mailbox(
                "vmb-1", _USER_ID, payload,
            )

    def test_delete_translates_database_error(self, monkeypatch):
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )

        def _raise(_vid):
            raise QueryError("simulated DB failure on delete")

        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "delete",
            _raise,
        )
        with pytest.raises(DatabaseQueryError):
            virtual_mailboxes_service.delete_virtual_mailbox("vmb-1", _USER_ID)

    def test_list_emails_translates_database_error(self, monkeypatch):
        _patch_user_catalogue(monkeypatch)
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: _fake_record(),
        )

        def _raise(*_a, **_kw):
            raise QueryError("simulated DB failure on list_filtered")

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered",
            _raise,
        )
        with pytest.raises(DatabaseQueryError):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )
