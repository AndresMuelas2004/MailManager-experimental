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


def _listing_row(*, pmid, account_id=_ACCOUNT_ID):
    """A minimal ``email_metadata`` row dict shaped for
    ``row_to_email_metadata_out`` (all the keys it reads)."""
    return {
        "provider_message_id": pmid,
        "account_id": account_id,
        "thread_id": None,
        "from_email": "a@x.com",
        "from_name": "A",
        "subject": "shared",
        "received_at": datetime(2026, 5, 10, tzinfo=timezone.utc),
        "is_read": False,
        "box": "ALL_MAIL",
        "has_attachments": False,
        "is_favorite": False,
        "to_email": "",
        "to_name": "",
        "mailbox_id": _MAILBOX_ID,
    }


def _patch_user_catalogue(monkeypatch, *, accounts=None):
    """Wire monkeypatches so the user owns ``accounts`` (default: [_ACCOUNT_ID]).

    Single mock now: ``_owned_account_ids`` uses the JOIN-based
    ``account_store.list_account_ids_by_user`` instead of the prior
    ``mailbox_store.list_by_owner`` + per-mailbox ``list_by_mailbox``
    pattern (N+1 fix). Tests that need to assert the catalogue lookup
    happens only need to monkeypatch this one entry point.
    """
    if accounts is None:
        accounts = [_ACCOUNT_ID]
    monkeypatch.setattr(
        virtual_mailboxes_service.account_store, "list_account_ids_by_user",
        lambda _uid: list(accounts),
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

    def _patch_listing(
        self, monkeypatch, *, record, rows=None, list_exc=None, owned=None,
        total=None, count_captured=None,
    ):
        captured = {}
        monkeypatch.setattr(
            virtual_mailboxes_service.virtual_mailbox_store, "get",
            lambda _vid: record,
        )
        _patch_user_catalogue(monkeypatch, accounts=owned if owned is not None else [_ACCOUNT_ID])

        def _list(
            account_ids, box, tokens, limit, offset, *,
            extra_filters=None, box_in=None, box_not_in=None,
            distinct_provider_message_id=False, group_by_thread=False,
        ):
            captured["account_ids"] = account_ids
            captured["box"] = box
            captured["tokens"] = tokens
            captured["limit"] = limit
            captured["offset"] = offset
            captured["extra_filters"] = extra_filters
            captured["box_in"] = box_in
            captured["box_not_in"] = box_not_in
            captured["distinct_provider_message_id"] = distinct_provider_message_id
            captured["group_by_thread"] = group_by_thread
            if list_exc:
                raise list_exc
            return rows or []

        def _count(
            account_ids, box, tokens, *,
            extra_filters=None, box_in=None, box_not_in=None,
            distinct_provider_message_id=False, group_by_thread=False,
        ):
            if count_captured is not None:
                count_captured["account_ids"] = account_ids
                count_captured["box"] = box
                count_captured["tokens"] = tokens
                count_captured["extra_filters"] = extra_filters
                count_captured["box_in"] = box_in
                count_captured["box_not_in"] = box_not_in
                count_captured["distinct_provider_message_id"] = distinct_provider_message_id
                count_captured["group_by_thread"] = group_by_thread
            return total if total is not None else len(rows or [])

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered", _list,
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "count_filtered", _count,
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
            virtual_mailboxes_service.account_store, "list_account_ids_by_user",
            lambda _uid: [],
        )
        called = {"list_filtered": False, "count_filtered": False}

        def _fail_list(*args, **kwargs):
            called["list_filtered"] = True
            raise AssertionError("list_filtered must not be called")

        def _fail_count(*args, **kwargs):
            called["count_filtered"] = True
            raise AssertionError("count_filtered must not be called")

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "list_filtered", _fail_list,
        )
        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "count_filtered", _fail_count,
        )
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )
        assert result.items == []
        assert result.total == 0
        assert called["list_filtered"] is False
        assert called["count_filtered"] is False

    def test_unexpected_listing_error_translated(self, monkeypatch):
        record = _fake_record()
        self._patch_listing(monkeypatch, record=record, list_exc=RuntimeError("boom"))
        with pytest.raises(VirtualMailboxListError):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )

    def test_requests_distinct_dedup_on_both_calls(self, monkeypatch):
        # The vmbox listing dedups in SQL now (the Python
        # _dedupe_rows_by_provider_message_id is gone). The contract is
        # that BOTH list_filtered AND count_filtered are asked for the
        # deduplicated variant — otherwise the page and its total would
        # disagree on cross-account duplicates.
        record = _fake_record()
        count_captured: dict = {}
        captured = self._patch_listing(
            monkeypatch, record=record, count_captured=count_captured,
        )
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        assert captured["distinct_provider_message_id"] is True
        assert count_captured["distinct_provider_message_id"] is True

    def test_always_groups_by_thread_on_both_calls(self, monkeypatch):
        # The virtual listing ALWAYS collapses each thread into its most-recent
        # message (conversation view). Both list_filtered and count_filtered
        # must receive group_by_thread=True so threads are never split across
        # pages and total counts threads, not messages.
        record = _fake_record()
        count_captured: dict = {}
        captured = self._patch_listing(
            monkeypatch, record=record, count_captured=count_captured,
        )
        virtual_mailboxes_service.list_emails_for_virtual_mailbox("vmb-1", _USER_ID)
        assert captured["group_by_thread"] is True
        assert count_captured["group_by_thread"] is True

    def test_returns_envelope_with_total_from_count(self, monkeypatch):
        record = _fake_record()
        rows = [_listing_row(pmid="m1")]
        result = self._run_listing_with_total(monkeypatch, record, rows, total=9)
        assert result.total == 9
        assert len(result.items) == 1
        assert result.items[0].provider_message_id == "m1"

    def test_count_filtered_shares_predicates_with_list(self, monkeypatch):
        record = _fake_record(
            filter_payload={"subject_contains": "factura", "is_favorite": True},
        )
        count_captured: dict = {}
        captured = self._patch_listing(
            monkeypatch, record=record, count_captured=count_captured,
        )
        virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID, q="foo",
        )
        for key in ("account_ids", "box", "tokens", "extra_filters", "box_not_in"):
            assert count_captured[key] == captured[key]

    def test_limit_and_offset_echoed_in_envelope(self, monkeypatch):
        record = _fake_record()
        self._patch_listing(monkeypatch, record=record)
        result = virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID, limit=15, offset=30,
        )
        assert result.limit == 15
        assert result.offset == 30

    def test_unexpected_count_error_translated(self, monkeypatch):
        record = _fake_record()
        self._patch_listing(monkeypatch, record=record)

        def _raise(*_a, **_kw):
            raise RuntimeError("count boom")

        monkeypatch.setattr(
            virtual_mailboxes_service.email_metadata_store, "count_filtered", _raise,
        )
        with pytest.raises(
            VirtualMailboxListError,
            match="Failed to count emails while paginating the virtual mailbox listing",
        ):
            virtual_mailboxes_service.list_emails_for_virtual_mailbox(
                "vmb-1", _USER_ID,
            )

    def _run_listing_with_total(self, monkeypatch, record, rows, *, total):
        self._patch_listing(monkeypatch, record=record, rows=rows, total=total)
        return virtual_mailboxes_service.list_emails_for_virtual_mailbox(
            "vmb-1", _USER_ID,
        )


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
