"""
Unit tests for ``api.services.contacts_service``.

The recipient-autocomplete service has two store dependencies —
``account_store.list_account_ids_by_user`` (the user's owned-account set)
and ``email_metadata_store.list_recipient_suggestions`` (the aggregation
query). Both are monkeypatched so tests run without DB or provider
access, mirroring ``test_virtual_mailboxes_service.py`` (which resolves
the same owned-account set via the JOIN-based ``list_account_ids_by_user``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import DatabaseQueryError, RecipientSuggestionsError
from api.schemas.contact import ContactSuggestionOut
from api.services import contacts_service
from database.errors.exceptions import QueryError


_USER_ID = "user-1"


def _suggestion_row(*, email, name="Someone", frequency=1, last_seen=None):
    """A row shaped exactly as ``list_recipient_suggestions`` returns it."""
    return {
        "email": email,
        "name": name,
        "frequency": frequency,
        "last_seen": last_seen or datetime(2026, 5, 10, tzinfo=timezone.utc),
    }


def _patch_accounts(monkeypatch, *, accounts):
    monkeypatch.setattr(
        contacts_service.account_store, "list_account_ids_by_user",
        lambda _uid: list(accounts),
    )


def _patch_suggestions(monkeypatch, *, rows=None, capture=None, exc=None):
    def _list(account_ids, tokens, limit):
        if capture is not None:
            capture["account_ids"] = account_ids
            capture["tokens"] = tokens
            capture["limit"] = limit
        if exc is not None:
            raise exc
        return rows or []

    monkeypatch.setattr(
        contacts_service.email_metadata_store, "list_recipient_suggestions", _list,
    )


# ---------------------------------------------------------------------------
# Short-circuits — neither path should touch the metadata store
# ---------------------------------------------------------------------------


def test_whitespace_only_query_returns_empty_without_querying_store(monkeypatch):
    # ``q`` passed the router's ``min_length`` but collapses to no tokens.
    # The aggregation store must never be reached.
    _patch_accounts(monkeypatch, accounts=["acc-1"])

    def _explode(*_a, **_kw):
        raise AssertionError("list_recipient_suggestions must not be called")

    monkeypatch.setattr(
        contacts_service.email_metadata_store, "list_recipient_suggestions", _explode,
    )
    assert contacts_service.suggest_contacts(_USER_ID, "   ", 8) == []


def test_user_without_accounts_returns_empty_without_querying_store(monkeypatch):
    _patch_accounts(monkeypatch, accounts=[])

    def _explode(*_a, **_kw):
        raise AssertionError("list_recipient_suggestions must not be called")

    monkeypatch.setattr(
        contacts_service.email_metadata_store, "list_recipient_suggestions", _explode,
    )
    assert contacts_service.suggest_contacts(_USER_ID, "amparo", 8) == []


# ---------------------------------------------------------------------------
# Happy path + name normalisation
# ---------------------------------------------------------------------------


def test_happy_path_maps_rows_to_contact_suggestions(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(monkeypatch, rows=[
        _suggestion_row(email="amparo@ejemplo.com", name="Amparo López"),
        _suggestion_row(email="soporte@empresa.com", name="Soporte"),
    ])
    result = contacts_service.suggest_contacts(_USER_ID, "am", 8)
    assert result == [
        ContactSuggestionOut(email="amparo@ejemplo.com", name="Amparo López"),
        ContactSuggestionOut(email="soporte@empresa.com", name="Soporte"),
    ]


def test_empty_string_name_is_normalised_to_none(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(monkeypatch, rows=[_suggestion_row(email="a@b.com", name="")])
    result = contacts_service.suggest_contacts(_USER_ID, "a@", 8)
    assert result[0].name is None


def test_null_name_is_preserved_as_none(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(monkeypatch, rows=[_suggestion_row(email="a@b.com", name=None)])
    result = contacts_service.suggest_contacts(_USER_ID, "a@", 8)
    assert result[0].name is None


def test_limit_and_owned_accounts_are_forwarded_to_store(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1", "acc-2"])
    capture: dict = {}
    _patch_suggestions(monkeypatch, rows=[], capture=capture)
    contacts_service.suggest_contacts(_USER_ID, "amp", 5)
    assert capture["limit"] == 5
    assert capture["account_ids"] == ["acc-1", "acc-2"]
    # The service tokenises ``q`` via ``parse_search_tokens`` before the store.
    assert capture["tokens"] == ["amp"]


# ---------------------------------------------------------------------------
# Error translation — DatabaseError → translate_database_error; other → 500
# ---------------------------------------------------------------------------


def test_account_lookup_database_error_translates_to_query_error(monkeypatch):
    def _raise(_uid):
        raise QueryError("simulated DB failure listing accounts")

    monkeypatch.setattr(
        contacts_service.account_store, "list_account_ids_by_user", _raise,
    )
    with pytest.raises(DatabaseQueryError):
        contacts_service.suggest_contacts(_USER_ID, "amp", 8)


def test_account_lookup_unexpected_error_raises_recipient_suggestions_error(monkeypatch):
    def _raise(_uid):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        contacts_service.account_store, "list_account_ids_by_user", _raise,
    )
    with pytest.raises(RecipientSuggestionsError):
        contacts_service.suggest_contacts(_USER_ID, "amp", 8)


def test_suggestions_database_error_translates_to_query_error(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(
        monkeypatch, exc=QueryError("simulated DB failure on suggestions"),
    )
    with pytest.raises(DatabaseQueryError):
        contacts_service.suggest_contacts(_USER_ID, "amp", 8)


def test_suggestions_unexpected_error_raises_recipient_suggestions_error(monkeypatch):
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(monkeypatch, exc=RuntimeError("boom"))
    with pytest.raises(RecipientSuggestionsError):
        contacts_service.suggest_contacts(_USER_ID, "amp", 8)


def test_recipient_suggestions_error_message_does_not_leak_internal_details(monkeypatch):
    # api/CLAUDE.md §9 rule 4: the ``except Exception`` fallback must use a
    # generic message — no ``str(exc)`` / class name leaking to the client.
    _patch_accounts(monkeypatch, accounts=["acc-1"])
    _patch_suggestions(monkeypatch, exc=RuntimeError("secret-internal-detail"))
    with pytest.raises(RecipientSuggestionsError) as excinfo:
        contacts_service.suggest_contacts(_USER_ID, "amp", 8)
    assert "secret-internal-detail" not in excinfo.value.message
    assert "RuntimeError" not in excinfo.value.message
