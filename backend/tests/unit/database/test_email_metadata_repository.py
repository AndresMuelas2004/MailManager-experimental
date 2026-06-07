"""
Unit tests for PgEmailMetadataStore.

Follows the same pattern as test_mailbox_repository.py / test_account_repository.py:
monkeypatch get_connection to inject FakeCursors.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from database.repositories import email_metadata_repository as em_module
from database.repositories.email_metadata_repository import _escape_like
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


def _stub_execute_values(cur, sql, rows, **kwargs):
    """Stub for psycopg2.extras.execute_values that records the call."""
    cur.executed.append((sql, rows))
    cur.rowcount = len(rows)


# ===== _escape_like =====


def test_escape_like_passes_normal_text_unchanged():
    assert _escape_like("hello") == "hello"
    assert _escape_like("Sprint planning") == "Sprint planning"


def test_escape_like_handles_empty_string():
    assert _escape_like("") == ""


def test_escape_like_escapes_percent():
    assert _escape_like("50%") == "50\\%"


def test_escape_like_escapes_underscore():
    assert _escape_like("a_b") == "a\\_b"


def test_escape_like_escapes_backslash_first():
    # The backslash must be escaped first; otherwise %/_ escapes get re-escaped.
    assert _escape_like("a\\b") == "a\\\\b"


def test_escape_like_escapes_combined_metacharacters():
    # All three metacharacters present at once: backslash → \\, then % → \%, then _ → \_
    assert _escape_like("a\\b%c_d") == "a\\\\b\\%c\\_d"


# ===== upsert_batch =====


def test_upsert_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("m1", "acc1", "t1", "a@b.com", "A", "subj", datetime.now(timezone.utc), False, "ALL_MAIL")]
    result = em_module.email_metadata_store.upsert_batch("acc1", rows)
    assert result == 1
    assert len(cursor.executed) == 1


def test_upsert_batch_empty_rows_returns_zero(monkeypatch):
    result = em_module.email_metadata_store.upsert_batch("acc1", [])
    assert result == 0


def test_upsert_batch_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to upsert email metadata"):
        em_module.email_metadata_store.upsert_batch("acc1", [("m1",)])


def test_upsert_batch_generic_exception_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.upsert_batch("acc1", [("m1",)])


def test_upsert_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.upsert_batch("acc1", [("m1",)])


# ===== delete_batch_by_message_ids =====


def test_delete_batch_happy_path(monkeypatch):
    cursor = FakeCursor(rowcounts=[3])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.delete_batch_by_message_ids("acc1", ["m1", "m2", "m3"])
    assert result == 3


def test_delete_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.delete_batch_by_message_ids("acc1", []) == 0


def test_delete_batch_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to delete email metadata batch"):
        em_module.email_metadata_store.delete_batch_by_message_ids("acc1", ["m1"])


def test_delete_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.delete_batch_by_message_ids("acc1", ["m1"])


def test_delete_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.delete_batch_by_message_ids("acc1", ["m1"])


# ===== update_labels_batch =====


def test_update_labels_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("m1", "acc1", True, "INBOX")]
    result = em_module.email_metadata_store.update_labels_batch("acc1", rows)
    assert result == 1
    assert len(cursor.executed) == 1


def test_update_labels_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.update_labels_batch("acc1", []) == 0


def test_update_labels_batch_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to update email metadata labels"):
        em_module.email_metadata_store.update_labels_batch("acc1", [("m1",)])


def test_update_labels_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.update_labels_batch("acc1", [("m1",)])


def test_update_labels_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.update_labels_batch("acc1", [("m1",)])


# ===== get_trash_emails_by_ids =====


def test_get_trash_emails_by_ids_happy_path(monkeypatch):
    rows = [
        {"provider_message_id": "m1", "account_id": "acc1", "box": "TRASH", "previous_box": "ALL_MAIL"},
    ]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.get_trash_emails_by_ids("acc1", ["m1"])
    assert len(result) == 1
    assert result[0]["provider_message_id"] == "m1"


def test_get_trash_emails_by_ids_empty_ids_returns_empty(monkeypatch):
    assert em_module.email_metadata_store.get_trash_emails_by_ids("acc1", []) == []


def test_get_trash_emails_by_ids_invalid_text_returns_empty(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.get_trash_emails_by_ids("not-uuid", ["m1"]) == []


def test_get_trash_emails_by_ids_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get trash emails"):
        em_module.email_metadata_store.get_trash_emails_by_ids("acc1", ["m1"])


def test_get_trash_emails_by_ids_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.get_trash_emails_by_ids("acc1", ["m1"])


def test_get_trash_emails_by_ids_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.get_trash_emails_by_ids("acc1", ["m1"])


# ===== mark_as_deleted_batch =====


def test_mark_as_deleted_batch_happy_path(monkeypatch):
    cursor = FakeCursor(rowcounts=[2])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.mark_as_deleted_batch("acc1", ["m1", "m2"])
    assert result == 2


def test_mark_as_deleted_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.mark_as_deleted_batch("acc1", []) == 0


def test_mark_as_deleted_batch_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to mark emails as deleted"):
        em_module.email_metadata_store.mark_as_deleted_batch("acc1", ["m1"])


def test_mark_as_deleted_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.mark_as_deleted_batch("acc1", ["m1"])


def test_mark_as_deleted_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.mark_as_deleted_batch("acc1", ["m1"])


# ===== restore_from_trash_batch =====


def test_restore_from_trash_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("old_m1", "new_m1", "acc1")]
    result = em_module.email_metadata_store.restore_from_trash_batch("acc1", rows)
    assert result == 1
    assert len(cursor.executed) == 1


def test_restore_from_trash_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.restore_from_trash_batch("acc1", []) == 0


def test_restore_from_trash_batch_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to restore emails from trash"):
        em_module.email_metadata_store.restore_from_trash_batch("acc1", [("m1", "m1", "acc1")])


def test_restore_from_trash_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.restore_from_trash_batch("acc1", [("m1", "m1", "acc1")])


def test_restore_from_trash_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.restore_from_trash_batch("acc1", [("m1", "m1", "acc1")])


# ===== restore_from_trash_discovered_batch =====


def test_restore_from_trash_discovered_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("old_m1", "new_m1", "acc1", "SENT")]
    result = em_module.email_metadata_store.restore_from_trash_discovered_batch("acc1", rows)
    assert result == 1
    assert len(cursor.executed) == 1


def test_restore_from_trash_discovered_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.restore_from_trash_discovered_batch("acc1", []) == 0


def test_restore_from_trash_discovered_batch_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to restore emails with discovered box"):
        em_module.email_metadata_store.restore_from_trash_discovered_batch(
            "acc1", [("m1", "m1", "acc1", "SENT")],
        )


def test_restore_from_trash_discovered_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.restore_from_trash_discovered_batch(
            "acc1", [("m1", "m1", "acc1", "SENT")],
        )


# ===== move_to_trash_batch =====


def test_move_to_trash_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("m1", "m1", "acc1"), ("m2", "m2", "acc1")]
    result = em_module.email_metadata_store.move_to_trash_batch("acc1", rows)
    assert result == 2
    assert len(cursor.executed) == 1


def test_move_to_trash_batch_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.move_to_trash_batch("acc1", []) == 0


def test_move_to_trash_batch_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("fail")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to move emails to trash"):
        em_module.email_metadata_store.move_to_trash_batch("acc1", [("m1", "m1", "acc1")])


def test_move_to_trash_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.move_to_trash_batch("acc1", [("m1", "m1", "acc1")])


def test_move_to_trash_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.move_to_trash_batch("acc1", [("m1", "m1", "acc1")])


# ===== update_read_status_batch =====


def test_update_read_status_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("m1", "acc1", True), ("m2", "acc1", True)]
    result = em_module.email_metadata_store.update_read_status_batch("acc1", rows)
    assert result == 2
    assert len(cursor.executed) == 1


def test_update_read_status_batch_empty_returns_zero(monkeypatch):
    result = em_module.email_metadata_store.update_read_status_batch("acc1", [])
    assert result == 0


def test_update_read_status_batch_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to update email read status"):
        em_module.email_metadata_store.update_read_status_batch("acc1", [("m1",)])


def test_update_read_status_batch_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.update_read_status_batch("acc1", [("m1",)])


# ===== update_spam_status_batch =====


def test_update_spam_status_batch_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _stub_execute_values)

    rows = [("old_m1", "acc1", "new_m1", "SPAM"), ("old_m2", "acc1", "new_m2", "SPAM")]
    result = em_module.email_metadata_store.update_spam_status_batch("acc1", rows)
    assert result == 2
    assert len(cursor.executed) == 1


def test_update_spam_status_batch_empty_returns_zero(monkeypatch):
    result = em_module.email_metadata_store.update_spam_status_batch("acc1", [])
    assert result == 0


def test_update_spam_status_batch_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="Failed to update email spam status"):
        em_module.email_metadata_store.update_spam_status_batch("acc1", [("old_m1",)])


def test_update_spam_status_batch_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])

    def _raise_execute_values(cur, sql, rows, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(em_module.psycopg2.extras, "execute_values", _raise_execute_values)

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.update_spam_status_batch("acc1", [("old_m1",)])


def test_update_spam_status_batch_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.update_spam_status_batch("acc1", [("old_m1",)])


# ===== list_filtered =====


def _row(**overrides):
    base = {
        "provider_message_id": "m1", "account_id": "acc1", "mailbox_id": "mb1",
        "thread_id": "t1",
        "from_email": "a@b.com", "from_name": "A", "subject": "s",
        "received_at": datetime.now(timezone.utc), "is_read": False, "box": "ALL_MAIL",
    }
    base.update(overrides)
    return base


def test_list_filtered_empty_account_ids_returns_empty_without_db_call(monkeypatch):
    # If account_ids is empty, the repository must short-circuit BEFORE touching
    # the connection — otherwise a misconfigured deployment could leak rows of
    # an unauthorized mailbox. Inject an exploding connection to assert no
    # cursor is requested.
    def _explode():
        raise AssertionError("get_connection must not be called for empty account_ids")

    monkeypatch.setattr(em_module.connection, "get_connection", _explode)
    assert em_module.email_metadata_store.list_filtered([], "ALL_MAIL", [], 200, 0) == []


def test_list_filtered_no_tokens_omits_search_predicate(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 200, 0,
    )
    assert len(result) == 1
    sql, params = cursor.executed[0]
    # No search predicate when tokens are empty: ILIKE/unaccent must not appear.
    assert "ILIKE" not in sql
    assert "unaccent" not in sql
    # Base predicate parameters are passed through as named placeholders.
    assert params["account_ids"] == ["acc1"]
    assert params["box"] == "ALL_MAIL"
    assert params["limit"] == 200
    assert params["offset"] == 0
    # No search-token placeholders.
    assert "tok0" not in params


def test_list_filtered_with_tokens_builds_and_block_with_named_placeholders(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], "ALL_MAIL", ["foo", "bar"], 50, 10,
    )
    sql, params = cursor.executed[0]
    # Per-token placeholders must follow the tok0/tok1/... naming scheme.
    assert params["tok0"] == "%foo%"
    assert params["tok1"] == "%bar%"
    # Each token contributes a 3-column OR block joined by AND.
    assert sql.count("ILIKE") == 6  # 3 columns × 2 tokens
    assert sql.count("unaccent(lower(coalesce(subject, '')))") == 2
    assert sql.count("unaccent(lower(coalesce(from_email, '')))") == 2
    assert sql.count("unaccent(lower(coalesce(from_name, '')))") == 2
    # Tokens are AND-combined: there must be exactly two parenthesised blocks
    # joined by " AND " inside the generated predicate.
    assert " AND " in sql


def test_list_filtered_escapes_token_metacharacters_before_wrapping(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", ["50%_off\\bar"], 200, 0,
    )
    _, params = cursor.executed[0]
    # The token is wrapped with % on both sides AFTER escaping. The original
    # %, _ and \ inside the user input must arrive at the DB pre-escaped so
    # they do not act as ILIKE metacharacters.
    assert params["tok0"] == "%50\\%\\_off\\\\bar%"


def test_list_filtered_invalid_text_returns_empty(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.list_filtered(
        ["not-a-uuid"], "ALL_MAIL", [], 200, 0,
    ) == []


def test_list_filtered_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to list filtered email metadata"):
        em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 200, 0)


def test_list_filtered_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 200, 0)


def test_list_filtered_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 200, 0)


def test_list_filtered_returns_dicts_for_each_row(monkeypatch):
    rows = [
        _row(provider_message_id="m1", account_id="acc1", box="SPAM"),
        _row(provider_message_id="m2", account_id="acc2", box="SPAM"),
    ]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], "SPAM", [], 200, 0,
    )
    assert len(result) == 2
    assert result[0]["provider_message_id"] == "m1"
    assert result[1]["provider_message_id"] == "m2"
    # The repository normalises rows to plain dicts even when the cursor returns
    # RealDictRow / mapping-like objects.
    assert all(isinstance(r, dict) for r in result)


def test_list_filtered_joins_accounts_and_selects_mailbox_id(monkeypatch):
    # Virtual mailboxes with scope='all' / 'accounts' can mix emails
    # from several real mailboxes — the listing must surface each
    # email's REAL mailbox_id (not the route's). The repository owns
    # that join: it must SELECT ``mailbox_id`` from the joined
    # ``accounts`` table and emit it in every row. Regressing this
    # join silently re-introduces the ``account_not_found`` 404 the
    # frontend used to hit when opening an email's content from a
    # virtual mailbox.
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 200, 0)
    sql, _ = cursor.executed[0]
    assert "JOIN accounts" in sql
    assert "a.mailbox_id" in sql


# ===== exists =====


def test_exists_returns_true_when_row_present(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(1,)])
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.exists("acc1", "m1") is True
    assert len(cursor.executed) == 1


def test_exists_returns_false_when_row_absent(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.exists("acc1", "m-missing") is False


def test_exists_invalid_uuid_returns_false(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.exists("not-a-uuid", "m1") is False


def test_exists_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to check email metadata existence"):
        em_module.email_metadata_store.exists("acc1", "m1")


def test_exists_generic_exception_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.exists("acc1", "m1")


def test_exists_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.exists("acc1", "m1")


# ===== update_has_attachments (D-09) =====
# Recomputes ``email_metadata.has_attachments`` from the live count of
# non-inline rows in ``email_attachments``. The query is idempotent so
# calling twice in a row is harmless; the tests verify error wrapping
# and the InvalidTextRepresentation soft fallback.


def test_update_has_attachments_executes_with_correct_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.update_has_attachments("acc-1", "msg-1")
    assert len(cursor.executed) == 1
    _sql, params = cursor.executed[0]
    assert params["account_id"] == "acc-1"
    assert params["provider_message_id"] == "msg-1"


def test_update_has_attachments_invalid_uuid_raises_query_error(monkeypatch):
    # Phase 2.2 fix: bad UUID at the boundary now raises instead of
    # returning silently. Letting it pass would leave ``has_attachments``
    # stale without any signal that the helper failed (B.lazy invariant).
    from database.errors import QueryError as DbQueryError
    cursor = FakeCursor(
        execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad uuid"),
    )
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(DbQueryError):
        em_module.email_metadata_store.update_has_attachments("not-a-uuid", "msg-1")


def test_update_has_attachments_db_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("db down"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError):
        em_module.email_metadata_store.update_has_attachments("acc-1", "msg-1")


def test_update_has_attachments_unexpected_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.update_has_attachments("acc-1", "msg-1")


def test_update_has_attachments_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool"))
    with pytest.raises(ConnectionPoolError):
        em_module.email_metadata_store.update_has_attachments("acc-1", "msg-1")


# ===== update_favorite (M11) =====
# Provider-First favourite toggle persist. Uses ``RETURNING`` so a zero-row
# match (race: row deleted between the existence pre-check and the UPDATE)
# surfaces as ``False`` for the service to map to 404.


def test_update_favorite_happy_path_returns_true(monkeypatch):
    cursor = FakeCursor(fetchone_results=[("msg-1",)])
    patch_connection(monkeypatch, em_module, [cursor])
    result = em_module.email_metadata_store.update_favorite("acc-1", "msg-1", True)
    assert result is True
    _sql, params = cursor.executed[0]
    assert params == {
        "account_id": "acc-1",
        "provider_message_id": "msg-1",
        "is_favorite": True,
    }


def test_update_favorite_zero_rows_returns_false(monkeypatch):
    # RETURNING yields no row → fetchone() is None → race lost → False.
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, em_module, [cursor])
    assert em_module.email_metadata_store.update_favorite("acc-1", "msg-1", False) is False


def test_update_favorite_db_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("db down"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError):
        em_module.email_metadata_store.update_favorite("acc-1", "msg-1", True)


def test_update_favorite_unexpected_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.update_favorite("acc-1", "msg-1", True)


def test_update_favorite_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool"))
    with pytest.raises(ConnectionPoolError):
        em_module.email_metadata_store.update_favorite("acc-1", "msg-1", True)


# ===== sync_favorites_for_account (M11) =====
# Single-statement full replacement: every row of the account is set to
# ``is_favorite = (provider_message_id = ANY(true_ids))``. ``rowcount`` is the
# total rows touched (drives FavoriteSyncResponse.total_synced).


def test_sync_favorites_happy_path_returns_rowcount(monkeypatch):
    cursor = FakeCursor(rowcounts=[5])
    patch_connection(monkeypatch, em_module, [cursor])
    result = em_module.email_metadata_store.sync_favorites_for_account("acc-1", ["m1", "m2"])
    assert result == 5
    _sql, params = cursor.executed[0]
    assert params == {"account_id": "acc-1", "true_ids": ["m1", "m2"]}


def test_sync_favorites_clear_all_with_empty_list(monkeypatch):
    # favorite_ids=[] is valid: ``= ANY('{}')`` is FALSE for every row, so the
    # statement clears all favourites. rowcount still counts the touched rows.
    cursor = FakeCursor(rowcounts=[10])
    patch_connection(monkeypatch, em_module, [cursor])
    result = em_module.email_metadata_store.sync_favorites_for_account("acc-1", [])
    assert result == 10
    _sql, params = cursor.executed[0]
    assert params["true_ids"] == []


def test_sync_favorites_db_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("db down"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError):
        em_module.email_metadata_store.sync_favorites_for_account("acc-1", ["m1"])


def test_sync_favorites_unexpected_error_wrapped(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.sync_favorites_for_account("acc-1", ["m1"])


def test_sync_favorites_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool"))
    with pytest.raises(ConnectionPoolError):
        em_module.email_metadata_store.sync_favorites_for_account("acc-1", ["m1"])


# ===== list_filtered — extra_filters / box_not_in SQL surface (M12) =====
# The guide flags this builder as the SQL-injection / silent-422 boundary, so
# the exact clause + parameter emitted for each filter key is asserted here.


def test_list_filtered_subject_contains_emits_escaped_ilike(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], None, [], 50, 0,
        extra_filters={"subject_contains": "50%"},
    )
    sql, params = cursor.executed[0]
    assert "unaccent(lower(coalesce(subject, ''))) ILIKE" in sql
    # _escape_like("50%") -> "50\%", wrapped as "%50\%%".
    assert params["extra_subject_contains"] == "%50\\%%"


def test_list_filtered_is_favorite_emits_clause_and_param(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], None, [], 50, 0,
        extra_filters={"is_favorite": True},
    )
    sql, params = cursor.executed[0]
    assert "is_favorite = %(extra_is_favorite)s" in sql
    assert params["extra_is_favorite"] is True


def test_list_filtered_unknown_key_is_dropped(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], None, [], 50, 0,
        extra_filters={"from_domain": "example.com"},  # legacy / removed key
    )
    sql, params = cursor.executed[0]
    # No clause and no parameter leaks for an unregistered key.
    assert "from_domain" not in sql
    assert not any(k.startswith("extra_") for k in params)


def test_list_filtered_box_not_in_emits_negated_any(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], None, [], 50, 0,
        box_not_in=["TRASH", "SPAM"],
    )
    sql, params = cursor.executed[0]
    assert "NOT (box = ANY(%(box_not_in_list)s))" in sql
    assert params["box_not_in_list"] == ["TRASH", "SPAM"]


def test_list_filtered_empty_account_ids_short_circuits(monkeypatch):
    # No account scope → empty result without touching the connection.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    result = em_module.email_metadata_store.list_filtered([], None, [], 50, 0)
    assert result == []
    assert cursor.executed == []


# ===== list_filtered — distinct_provider_message_id (vmbox dedup) =====
# The Python dedup was removed; dedup now happens in SQL. These lock the
# SQL surface of the DISTINCT variant and its tie-break parity with the
# old _dedupe_rows_by_provider_message_id (.strip() == btrim(coalesce)).


def test_list_filtered_distinct_uses_distinct_on_and_btrim_tiebreak(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], None, [], 50, 0,
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
    )
    sql, params = cursor.executed[0]
    # Dedups by provider_message_id in SQL before paginating.
    assert "DISTINCT ON (em.provider_message_id)" in sql
    # Parity with the removed Python ``isinstance(str) and col.strip()``:
    # NULL, '' and whitespace-only all score as empty → btrim(coalesce()).
    assert "btrim(coalesce(em.to_email, '')) <> ''" in sql
    assert "btrim(coalesce(em.to_name,  '')) <> ''" in sql
    # Outer ORDER BY restores presentation order WITH the PK tie-break the
    # Python sort lacked, so OFFSET paging is total/stable.
    assert "ORDER BY d.received_at DESC, d.account_id, d.provider_message_id" in sql
    # Shared predicates still apply through the same helper.
    assert "NOT (box = ANY(%(box_not_in_list)s))" in sql
    assert params["box_not_in_list"] == ["TRASH", "SPAM"]
    assert params["limit"] == 50
    assert params["offset"] == 0


def test_list_filtered_non_distinct_does_not_dedup(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 50, 0)
    sql, _ = cursor.executed[0]
    # The regular listing must use the plain LIST_FILTERED (no DISTINCT ON).
    assert "DISTINCT ON" not in sql


# ===== count_filtered =====


def test_count_filtered_empty_account_ids_returns_zero_without_db_call(monkeypatch):
    # Mirror list_filtered: empty scope short-circuits to 0 BEFORE the
    # connection is touched (no leaking an unauthorized count).
    def _explode():
        raise AssertionError("get_connection must not be called for empty account_ids")

    monkeypatch.setattr(em_module.connection, "get_connection", _explode)
    assert em_module.email_metadata_store.count_filtered([], "ALL_MAIL", []) == 0


def test_count_filtered_returns_total_from_first_column(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(42,)])
    patch_connection(monkeypatch, em_module, [cursor])

    total = em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", [])
    assert total == 42
    sql, params = cursor.executed[0]
    assert "COUNT(*)" in sql
    # No ORDER BY / LIMIT / OFFSET on the count.
    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql
    assert "OFFSET" not in sql
    assert params["account_ids"] == ["acc1"]
    assert params["box"] == "ALL_MAIL"


def test_count_filtered_no_row_returns_zero(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", []) == 0


def test_count_filtered_shares_token_predicate_with_listing(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(3,)])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.count_filtered(
        ["acc1"], "ALL_MAIL", ["foo", "bar"],
    )
    sql, params = cursor.executed[0]
    # Same per-token placeholders + 3-column OR block as the listing.
    assert params["tok0"] == "%foo%"
    assert params["tok1"] == "%bar%"
    assert sql.count("ILIKE") == 6


def test_count_filtered_escapes_token_metacharacters(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(0,)])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.count_filtered(
        ["acc1"], "ALL_MAIL", ["50%_off\\bar"],
    )
    _, params = cursor.executed[0]
    assert params["tok0"] == "%50\\%\\_off\\\\bar%"


def test_count_filtered_subject_contains_emits_escaped_ilike(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(0,)])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.count_filtered(
        ["acc-1"], None, [],
        extra_filters={"subject_contains": "50%"},
    )
    sql, params = cursor.executed[0]
    assert "unaccent(lower(coalesce(subject, ''))) ILIKE" in sql
    assert params["extra_subject_contains"] == "%50\\%%"


def test_count_filtered_box_not_in_emits_negated_any(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(0,)])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.count_filtered(
        ["acc-1"], None, [],
        box_not_in=["TRASH", "SPAM"],
    )
    sql, params = cursor.executed[0]
    assert "NOT (box = ANY(%(box_not_in_list)s))" in sql
    assert params["box_not_in_list"] == ["TRASH", "SPAM"]


def test_count_filtered_distinct_uses_count_distinct(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(7,)])
    patch_connection(monkeypatch, em_module, [cursor])

    total = em_module.email_metadata_store.count_filtered(
        ["acc1", "acc2"], None, [],
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
    )
    assert total == 7
    sql, _ = cursor.executed[0]
    # Deduplicated count collapses the same provider message across two
    # accounts into one — must NOT over-count.
    assert "COUNT(DISTINCT em.provider_message_id)" in sql
    # No tie-break ORDER BY needed: it only counts distinct keys.
    assert "DISTINCT ON" not in sql


def test_count_filtered_invalid_uuid_returns_zero(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.count_filtered(
        ["not-a-uuid"], "ALL_MAIL", [],
    ) == 0


def test_count_filtered_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to count filtered email metadata"):
        em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", [])


def test_count_filtered_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", [])


def test_count_filtered_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", [])
