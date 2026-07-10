"""Tests espejo de ``emails_service.listado``: list_emails, paginacion y count_unread_emails."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    EmailListError,
    MailboxNotFound,
    UnreadCountError,
)
from api.services.emails_service import listado

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


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
        listado, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    if account_get_return == "default":
        monkeypatch.setattr(
            listado.account_store, "get",
            lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
        )
    else:
        monkeypatch.setattr(
            listado.account_store, "get",
            account_get_return,
        )
    monkeypatch.setattr(
        listado.account_store, "list_by_mailbox",
        lambda _mb: accounts_for_mailbox if accounts_for_mailbox is not None else [_fake_account()],
    )
    result_rows = rows if rows is not None else [_SAMPLE_ROW]
    total_value = total if total is not None else len(result_rows)

    def _record(
        account_ids, box, tokens, limit, offset,
        *, extra_filters=None, box_in=None, box_not_in=None,
        distinct_provider_message_id=False, group_by_thread=False,
        operator_clauses=None, sort=None, sort_dir=None,
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
                "sort": sort,
                "sort_dir": sort_dir,
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
        listado.email_metadata_store, "list_filtered", _record,
    )
    monkeypatch.setattr(
        listado.email_metadata_store, "count_filtered", _count,
    )


class TestListEmails:

    def test_single_account_happy_path(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        result = listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
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
        result = listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
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
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
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
        result = listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)
        assert result.items == []
        assert result.total == 0
        # Empty mailbox must short-circuit BEFORE calling list_filtered.
        assert calls == []
        # ...and BEFORE count_filtered too — no DB round trips at all.
        assert count_calls == []

    def test_account_not_found_raises(self, monkeypatch):
        _patch_list_emails(monkeypatch)
        with pytest.raises(AccountNotFound, match="during email listing"):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, "nonexistent")

    def test_db_error_on_account_lookup_raises(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_mb, _aid):
            raise DbQueryError("db fail")

        monkeypatch.setattr(listado.account_store, "get", _raise)
        with pytest.raises(Exception):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_unexpected_error_on_account_lookup_raises_email_list_error(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        def _raise(_mb, _aid):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(listado.account_store, "get", _raise)
        with pytest.raises(
            EmailListError, match="Failed to look up account for email listing",
        ):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

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
            listado.email_metadata_store, "list_filtered", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_db_error_on_unified_account_listing_translated(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_mb):
            raise DbQueryError("db fail")

        monkeypatch.setattr(listado.account_store, "list_by_mailbox", _raise)
        with pytest.raises(Exception):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_unexpected_error_raises_email_list_error_with_filtered_message(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        # **_kwargs so the keyword args (extra_filters/box_not_in/
        # operator_clauses) reach the stub and the injected RuntimeError is
        # what propagates — not a signature TypeError.
        def _raise(_aids, _box, _tokens, _limit, _offset, **_kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            listado.email_metadata_store, "list_filtered", _raise,
        )
        with pytest.raises(
            EmailListError, match="Failed to list email metadata for filtered listing",
        ):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_unexpected_error_on_unified_account_listing_raises_email_list_error(
        self, monkeypatch,
    ):
        _patch_list_emails(monkeypatch)

        def _raise(_mb):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(listado.account_store, "list_by_mailbox", _raise)
        with pytest.raises(
            EmailListError, match="Failed to load mailbox accounts for email listing",
        ):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID)

    def test_empty_result_returns_empty_list(self, monkeypatch):
        _patch_list_emails(monkeypatch, rows=[])
        result = listado.list_emails(_MAILBOX_ID, "SPAM", _USER_ID)
        assert result.items == []
        assert result.total == 0

    def test_q_is_parsed_into_tokens_passed_to_store(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="foo bar",
        )
        assert calls[0]["tokens"] == ["foo", "bar"]

    def test_q_none_results_in_empty_token_list(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert calls[0]["tokens"] == []

    def test_q_only_whitespace_results_in_empty_token_list(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="   ",
        )
        assert calls[0]["tokens"] == []

    def test_limit_and_offset_propagate_to_store(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            limit=42, offset=100,
        )
        assert calls[0]["limit"] == 42
        assert calls[0]["offset"] == 100

    def test_default_limit_and_offset_propagated(self, monkeypatch):
        calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=calls)
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
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
        listado.list_emails(
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
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert list_calls[0]["group_by_thread"] is False
        assert count_calls[0]["group_by_thread"] is False


class TestListEmailsPagination:
    """``list_emails`` returns an ``EmailPageOut`` envelope: the total
    comes from ``count_filtered`` and ``count_filtered`` must receive the
    exact same predicates as ``list_filtered``."""

    def test_total_is_value_returned_by_count_filtered(self, monkeypatch):
        _patch_list_emails(monkeypatch, rows=[_SAMPLE_ROW], total=137)
        result = listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        # total is the WHOLE filtered set, independent of the page length.
        assert result.total == 137
        assert len(result.items) == 1

    def test_limit_and_offset_echoed_in_envelope(self, monkeypatch):
        _patch_list_emails(monkeypatch)
        result = listado.list_emails(
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
        listado.list_emails(
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
        listado.list_emails(
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
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
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
        listado.list_emails(
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
        listado.list_emails(
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
        listado.list_emails(
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
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, q="foo bar",
        )
        assert list_calls[0]["operator_clauses"] is None
        # Free-text semantics are untouched by the richer parser.
        assert list_calls[0]["tokens"] == ["foo", "bar"]

    def test_unread_chip_maps_to_is_read_false_operator(self, monkeypatch):
        # The quick-filter chips reuse the SAME operator-clause kinds the lupa
        # uses; ``unread`` → ``is:unread`` semantics (is_read = False).
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, unread=True,
        )
        assert list_calls[0]["operator_clauses"] == [("is_read_op", False)]

    def test_has_attachment_chip_maps_to_has_attachments_operator(self, monkeypatch):
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, has_attachment=True,
        )
        assert list_calls[0]["operator_clauses"] == [("has_attachments", True)]

    def test_favorite_only_chip_maps_to_is_favorite_operator(self, monkeypatch):
        # ``favorite_only`` is a plain AND filter on the current box — distinct
        # from the ``favorite`` anchor param (which excludes TRASH/SPAM). It
        # rides the ``is_favorite_op`` operator, NOT the is_favorite extra
        # filter, so no box override happens.
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID, favorite_only=True,
        )
        assert list_calls[0]["operator_clauses"] == [("is_favorite_op", True)]
        # The anchor path is untouched: box stays ALL_MAIL, no TRASH/SPAM
        # exclusion, and no is_favorite extra filter (the service passes
        # ``extra_filters or None`` → None when the dict is empty, unlike the
        # ``favorite`` anchor which would have set ``{"is_favorite": True}``).
        assert list_calls[0]["box"] == "ALL_MAIL"
        assert list_calls[0]["extra_filters"] is None

    def test_all_three_chips_emit_all_three_operator_clauses(self, monkeypatch):
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            unread=True, has_attachment=True, favorite_only=True,
        )
        assert list_calls[0]["operator_clauses"] == [
            ("is_read_op", False),
            ("has_attachments", True),
            ("is_favorite_op", True),
        ]

    def test_chips_append_after_lupa_operators(self, monkeypatch):
        # A chip concatenates AFTER any q-derived operator clauses (the op{idx}
        # numbering continues without collision, same as a repeated operator).
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            q="from:linkedin", unread=True,
        )
        assert list_calls[0]["operator_clauses"] == [
            ("from_contains", "linkedin"),
            ("is_read_op", False),
        ]

    def test_chip_operator_clauses_passed_identically_to_list_and_count(self, monkeypatch):
        # The chips must reach BOTH list_filtered and count_filtered unchanged,
        # protecting the "total cuadra con la página" invariant — same guard as
        # the lupa-operator parity test above, now via chips.
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            unread=True, favorite_only=True,
        )
        assert list_calls[0]["operator_clauses"] == [
            ("is_read_op", False),
            ("is_favorite_op", True),
        ]
        assert count_calls[0]["operator_clauses"] == list_calls[0]["operator_clauses"]

    def test_sort_forwarded_only_to_list_filtered_not_count(self, monkeypatch):
        # sort / sort_dir reach list_filtered (the SELECT orders) but NOT
        # count_filtered (a COUNT does not order). The _count stub deliberately
        # rejects sort kwargs, so a regression that forwarded them would crash
        # this test with TypeError rather than passing silently.
        list_calls: list = []
        count_calls: list = []
        _patch_list_emails(
            monkeypatch,
            list_filtered_calls=list_calls,
            count_filtered_calls=count_calls,
        )
        listado.list_emails(
            _MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID,
            sort="subject", sort_dir="asc",
        )
        assert list_calls[0]["sort"] == "subject"
        assert list_calls[0]["sort_dir"] == "asc"
        assert "sort" not in count_calls[0]
        assert "sort_dir" not in count_calls[0]

    def test_sort_defaults_to_date_desc_and_no_chip_operator_clauses(self, monkeypatch):
        # With no new params the defaults reproduce the pre-feature behaviour:
        # sort=date / dir=desc and operator_clauses still None (no q, no chips).
        list_calls: list = []
        _patch_list_emails(monkeypatch, list_filtered_calls=list_calls)
        listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)
        assert list_calls[0]["sort"] == "date"
        assert list_calls[0]["sort_dir"] == "desc"
        assert list_calls[0]["operator_clauses"] is None

    def test_db_error_on_count_filtered_translated(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_list_emails(monkeypatch)

        def _raise(_aids, _box, _tokens, **_kwargs):
            raise DbQueryError("count fail")

        monkeypatch.setattr(
            listado.email_metadata_store, "count_filtered", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)

    def test_unexpected_error_on_count_filtered_raises_email_list_error(self, monkeypatch):
        _patch_list_emails(monkeypatch)

        def _raise(_aids, _box, _tokens, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            listado.email_metadata_store, "count_filtered", _raise,
        )
        with pytest.raises(
            EmailListError,
            match="Failed to count emails while paginating the mailbox listing",
        ):
            listado.list_emails(_MAILBOX_ID, "ALL_MAIL", _USER_ID, _ACCOUNT_ID)


def _patch_count_unread(
    monkeypatch,
    *,
    accounts_for_mailbox=None,
    counts=None,
    count_calls=None,
):
    """Apply monkeypatches for count_unread_emails tests.

    Patches the three symbols the service reads on the ``listado``
    module (it imports them by name): ``ensure_mailbox_access``,
    ``account_store.list_by_mailbox`` and
    ``email_metadata_store.count_unread_by_account``. ``counts`` is the
    per-account ``{account_id: unread}`` mapping the store returns (a
    ``GROUP BY`` omits accounts with 0). ``count_calls`` optionally records
    the ``(account_ids, box)`` the store was called with so a test can
    assert the short-circuit (it stays empty) or the box propagation.
    """
    monkeypatch.setattr(
        listado, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        listado.account_store, "list_by_mailbox",
        lambda _mb: accounts_for_mailbox if accounts_for_mailbox is not None else [_fake_account()],
    )

    def _count(account_ids, box):
        if count_calls is not None:
            count_calls.append({"account_ids": account_ids, "box": box})
        return counts if counts is not None else {}

    monkeypatch.setattr(
        listado.email_metadata_store, "count_unread_by_account", _count,
    )


class TestCountUnreadEmails:

    def test_happy_path_breakdown_and_total(self, monkeypatch):
        accounts = [_fake_account(account_id="a1"), _fake_account(account_id="a2")]
        _patch_count_unread(
            monkeypatch, accounts_for_mailbox=accounts, counts={"a1": 5, "a2": 7},
        )
        result = listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
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
        result = listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
        assert result.total == 3
        a2 = next(d for d in result.accounts if d.account_id == "a2")
        assert a2.unread == 0

    def test_mailbox_without_accounts_short_circuits_without_db_call(self, monkeypatch):
        count_calls: list = []
        _patch_count_unread(
            monkeypatch, accounts_for_mailbox=[], count_calls=count_calls,
        )
        result = listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
        assert result.total == 0
        assert result.accounts == []
        # No accounts ⇒ the per-account count query must never run.
        assert count_calls == []

    def test_spam_box_is_propagated_to_store_and_response(self, monkeypatch):
        count_calls: list = []
        _patch_count_unread(
            monkeypatch, counts={_ACCOUNT_ID: 4}, count_calls=count_calls,
        )
        result = listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "SPAM")
        assert count_calls[0]["box"] == "SPAM"
        assert result.box == "SPAM"

    def test_ownership_error_propagates_unwrapped(self, monkeypatch):
        _patch_count_unread(monkeypatch)
        monkeypatch.setattr(
            listado, "ensure_mailbox_access",
            lambda _mb, _uid: (_ for _ in ()).throw(MailboxNotFound("foreign mailbox")),
        )
        with pytest.raises(MailboxNotFound):
            listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_db_error_on_count_is_translated_not_wrapped(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_count_unread(monkeypatch)

        def _raise(_ids, _box):
            raise DbQueryError("count fail")

        monkeypatch.setattr(
            listado.email_metadata_store, "count_unread_by_account", _raise,
        )
        # A DatabaseError must surface as 503 DatabaseQueryError via
        # translate_database_error — NOT as the 500 UnreadCountError.
        with pytest.raises(DatabaseQueryError):
            listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_unexpected_error_on_count_is_wrapped_in_unread_count_error(self, monkeypatch):
        _patch_count_unread(monkeypatch)

        def _raise(_ids, _box):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            listado.email_metadata_store, "count_unread_by_account", _raise,
        )
        with pytest.raises(
            UnreadCountError,
            match="Failed to count unread emails while building the mailbox unread badge",
        ):
            listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_unexpected_error_on_account_listing_is_wrapped(self, monkeypatch):
        _patch_count_unread(monkeypatch)

        def _raise(_mb):
            raise RuntimeError("boom")

        monkeypatch.setattr(listado.account_store, "list_by_mailbox", _raise)
        with pytest.raises(
            UnreadCountError, match="Failed to load mailbox accounts for unread count",
        ):
            listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")

    def test_db_error_on_account_listing_is_translated_not_wrapped(self, monkeypatch):
        from api.errors.exceptions import DatabaseQueryError
        from database.errors.exceptions import QueryError as DbQueryError
        _patch_count_unread(monkeypatch)

        def _raise(_mb):
            raise DbQueryError("list fail")

        monkeypatch.setattr(listado.account_store, "list_by_mailbox", _raise)
        # Mirror of test_db_error_on_count_is_translated_not_wrapped for the
        # FIRST try block (account listing): a DatabaseError must surface as a
        # 503 DatabaseQueryError via translate_database_error, NOT the 500
        # UnreadCountError. Without this, swapping the two except clauses on the
        # listing block would silently downgrade 503→500 and stay green (the
        # sibling unexpected-error test only injects RuntimeError).
        with pytest.raises(DatabaseQueryError):
            listado.count_unread_emails(_MAILBOX_ID, _USER_ID, "ALL_MAIL")
