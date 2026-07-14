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
from database.repositories.email_metadata_repository import (
    _build_order_by,
    _build_recipient_token_predicate,
    _escape_like,
)
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


# ===== update_read_status_by_thread =====


def test_update_read_status_by_thread_happy_path(monkeypatch):
    cursor = FakeCursor(rowcounts=[3])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.update_read_status_by_thread("acc1", ["m1"], True)
    assert result == 3


def test_update_read_status_by_thread_empty_returns_zero(monkeypatch):
    assert em_module.email_metadata_store.update_read_status_by_thread("acc1", [], True) == 0


def test_update_read_status_by_thread_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to update email read status by thread"):
        em_module.email_metadata_store.update_read_status_by_thread("acc1", ["m1"], True)


def test_update_read_status_by_thread_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.update_read_status_by_thread("acc1", ["m1"], True)


def test_update_read_status_by_thread_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.update_read_status_by_thread("acc1", ["m1"], True)


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


# ===== list_unread_recent_uncached =====
# Content-prefetch target selection. Returns the provider_message_ids of an
# account's unread / recent (<=48h) / ALL_MAIL / not-yet-cached messages,
# most-recent first. Error capture clones the list-returning
# ``get_trash_emails_by_ids`` shape (RealDictCursor + InvalidTextRepresentation
# → [] soft fallback).


def test_list_unread_recent_uncached_returns_message_ids(monkeypatch):
    rows = [{"provider_message_id": "m3"}, {"provider_message_id": "m1"}]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_unread_recent_uncached("acc1", 50)
    # Order is preserved from the query (most-recent first); ids are projected
    # as plain strings.
    assert result == ["m3", "m1"]
    _sql, params = cursor.executed[0]
    assert params == {"account_id": "acc1", "limit": 50}


def test_list_unread_recent_uncached_empty_when_no_rows(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.list_unread_recent_uncached("acc1", 50) == []


def test_list_unread_recent_uncached_invalid_uuid_returns_empty(monkeypatch):
    # A malformed account UUID collapses to "no results" (treated as ``exists``
    # does), never a 500 that would abort the prefetch.
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.list_unread_recent_uncached("not-a-uuid", 50) == []


def test_list_unread_recent_uncached_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to list unread recent uncached messages"):
        em_module.email_metadata_store.list_unread_recent_uncached("acc1", 50)


def test_list_unread_recent_uncached_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.list_unread_recent_uncached("acc1", 50)


def test_list_unread_recent_uncached_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.list_unread_recent_uncached("acc1", 50)


# ===== get_metadata =====
# Single-row read of a message's full metadata (incl. thread_id), backing
# the conversation endpoint's base-message lookup. The error-capture
# technique clones ``exists`` (InvalidTextRepresentation → None;
# DatabaseError → raise; psycopg2.Error → QueryError; generic → QueryError
# with the exception class name).


def test_get_metadata_returns_row_as_dict(monkeypatch):
    row = _row(thread_id="thr-7")
    cursor = FakeCursor(fetchone_results=[row])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.get_metadata("acc1", "m1")
    assert result is not None
    # The returned value is a plain dict carrying thread_id (used to fetch
    # the thread) plus the presentation columns (used to map the singleton).
    assert isinstance(result, dict)
    assert result["thread_id"] == "thr-7"
    assert result["provider_message_id"] == "m1"
    # The query projects mailbox_id from the JOIN on accounts.
    assert result["mailbox_id"] == "mb1"
    sql, params = cursor.executed[0]
    assert params["account_id"] == "acc1"
    assert params["provider_message_id"] == "m1"
    # Reads through the accounts JOIN so the row maps without special-casing.
    assert "JOIN accounts" in sql


def test_get_metadata_returns_none_when_row_absent(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.get_metadata("acc1", "missing") is None


def test_get_metadata_invalid_uuid_returns_none(monkeypatch):
    # A malformed account UUID collapses to "not found", consistent with
    # ``exists`` — never a 500.
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.get_metadata("not-a-uuid", "m1") is None


def test_get_metadata_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get email metadata row by message id"):
        em_module.email_metadata_store.get_metadata("acc1", "m1")


def test_get_metadata_generic_exception_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.get_metadata("acc1", "m1")


def test_get_metadata_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.get_metadata("acc1", "m1")


# ===== list_metadata_by_thread =====
# Lean projection of a thread's identity columns (provider_message_id +
# received_at/from_email/subject) ordered ``received_at DESC, provider_message_id``.
# Backs the conversation lazy-sync's id reconciliation: Outlook hands the same
# physical message different REST ids per endpoint, so the viewer maps each
# fetched member onto the stored row of the same physical message. An empty
# thread_id short-circuits to [] WITHOUT touching the DB; a malformed UUID
# collapses to [] (like ``exists`` / the other lean list reads).


def test_list_metadata_by_thread_returns_rows_as_dicts(monkeypatch):
    rows = [
        {
            "provider_message_id": "A",
            "received_at": datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc),
            "from_email": "a@b.com",
            "subject": "Hi",
        },
        {
            "provider_message_id": "B",
            "received_at": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            "from_email": "a@b.com",
            "subject": "Hi",
        },
    ]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_metadata_by_thread("acc1", "thr-1")
    assert result == rows
    assert all(isinstance(r, dict) for r in result)
    sql, params = cursor.executed[0]
    # Runs exactly LIST_METADATA_BY_THREAD with the thread-scoped params.
    assert sql == em_module.queries.LIST_METADATA_BY_THREAD
    assert params == {"account_id": "acc1", "thread_id": "thr-1"}


def test_list_metadata_by_thread_empty_thread_id_short_circuits(monkeypatch):
    # A threadless base row must not hit the DB: an empty thread_id returns []
    # BEFORE touching the connection — mirrors the empty-account_ids guard.
    def _explode():
        raise AssertionError("get_connection must not be called for an empty thread_id")

    monkeypatch.setattr(em_module.connection, "get_connection", _explode)
    assert em_module.email_metadata_store.list_metadata_by_thread("acc1", "") == []


def test_list_metadata_by_thread_invalid_uuid_returns_empty(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.list_metadata_by_thread("not-a-uuid", "thr-1") == []


def test_list_metadata_by_thread_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to list email metadata by thread"):
        em_module.email_metadata_store.list_metadata_by_thread("acc1", "thr-1")


def test_list_metadata_by_thread_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.list_metadata_by_thread("acc1", "thr-1")


def test_list_metadata_by_thread_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.list_metadata_by_thread("acc1", "thr-1")


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


# ===== list_filtered — operator_clauses SQL surface (lupa operators) =====
# The lupa's Gmail-style operators (from:/to:/subject:/has:/before:/after:/
# is:) reach the repository as a list of (kind, value) pairs resolved against
# _OPERATOR_CLAUSE_BUILDERS. Their parameter names are ``op{idx}`` per
# occurrence — disjoint from the ``extra_*`` (saved filters) and ``tok{i}``
# (free text) namespaces, so repeated operators and overlaps with saved
# filters never collide. These lock the exact clause + param emitted, mirroring
# the extra_filters surface tests above.


def test_list_filtered_no_operator_clauses_emits_no_extra_predicate(monkeypatch):
    # Zero-regression promise: with neither extra_filters NOR operator_clauses
    # the emitted SQL carries no extra ``AND`` clause — byte-for-byte identical
    # to the pre-operator query.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=None,
    )
    sql, params = cursor.executed[0]
    # No operator parameter leaks and no operator clause is emitted. (Bare
    # ``has_attachments`` appears as a projected column in the SELECT, so the
    # sentinel must be the operator clause shape, not the column name.)
    assert not any(k.startswith("op") for k in params)
    assert "has_attachments = %(op0)s" not in sql
    assert "ILIKE unaccent(lower(%(op0)s))" not in sql


def test_list_filtered_operator_clauses_use_unique_op_indexed_params(monkeypatch):
    # ``from:a from:b`` → two clauses with distinct op0/op1 params so repeated
    # operators do not overwrite each other.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("from_contains", "a"), ("from_contains", "b")],
    )
    _, params = cursor.executed[0]
    assert params["op0"] == "%a%"
    assert params["op1"] == "%b%"


def test_list_filtered_from_contains_emits_email_and_name_or(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("from_contains", "linkedin")],
    )
    sql, params = cursor.executed[0]
    assert "unaccent(lower(coalesce(from_email, ''))) ILIKE" in sql
    assert "unaccent(lower(coalesce(from_name, ''))) ILIKE" in sql
    assert params["op0"] == "%linkedin%"


def test_list_filtered_to_contains_emits_to_email_and_name_or(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("to_contains", "ana")],
    )
    sql, params = cursor.executed[0]
    assert "unaccent(lower(coalesce(to_email, ''))) ILIKE" in sql
    assert "unaccent(lower(coalesce(to_name, ''))) ILIKE" in sql
    assert params["op0"] == "%ana%"


def test_list_filtered_subject_op_emits_only_subject(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("subject_contains_op", "factura")],
    )
    sql, params = cursor.executed[0]
    assert "unaccent(lower(coalesce(subject, ''))) ILIKE unaccent(lower(%(op0)s))" in sql
    # The subject operator is a single-column clause — op0 appears exactly
    # once (unlike from:/to: which reference their param twice in an OR).
    assert sql.count("%(op0)s") == 1
    assert params["op0"] == "%factura%"


def test_list_filtered_has_attachments_emits_boolean_param(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("has_attachments", True)],
    )
    sql, params = cursor.executed[0]
    assert "has_attachments = %(op0)s" in sql
    assert params["op0"] is True


def test_list_filtered_is_read_op_emits_boolean_param(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("is_read_op", False)],
    )
    sql, params = cursor.executed[0]
    assert "is_read = %(op0)s" in sql
    assert params["op0"] is False


def test_list_filtered_is_favorite_op_emits_boolean_param(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("is_favorite_op", True)],
    )
    sql, params = cursor.executed[0]
    assert "is_favorite = %(op0)s" in sql
    assert params["op0"] is True


def test_list_filtered_received_after_and_before_emit_range(monkeypatch):
    # The tz-aware datetime is passed through verbatim (psycopg2 adapts it for
    # the TIMESTAMPTZ comparison); after → >=, before → <.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    after_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    before_dt = datetime(2026, 2, 1, tzinfo=timezone.utc)
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[
            ("received_after", after_dt),
            ("received_before", before_dt),
        ],
    )
    sql, params = cursor.executed[0]
    assert "received_at >= %(op0)s" in sql
    assert "received_at < %(op1)s" in sql
    assert params["op0"] is after_dt
    assert params["op1"] is before_dt


def test_list_filtered_operator_value_metacharacters_are_escaped(monkeypatch):
    # ILIKE metacharacters in an operator value are escaped via _escape_like,
    # same format as the free-text token escaping.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("from_contains", "50%_x")],
    )
    _, params = cursor.executed[0]
    assert params["op0"] == "%50\\%\\_x%"


def test_list_filtered_unknown_operator_kind_is_ignored(monkeypatch):
    # A kind with no builder is silently skipped (defensive) — no clause, no
    # param leaks.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[("not_a_real_kind", "x")],
    )
    sql, params = cursor.executed[0]
    assert not any(k.startswith("op") for k in params)
    assert "not_a_real_kind" not in sql


def test_list_filtered_operator_and_saved_filter_same_column_coexist(monkeypatch):
    # A saved filter and an operator touching the SAME column emit TWO
    # independent ANDed ILIKE clauses with disjoint params (extra_* vs op0) —
    # no collision, no overwrite.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], None, [], 50, 0,
        extra_filters={"subject_contains": "a"},
        operator_clauses=[("subject_contains_op", "b")],
    )
    sql, params = cursor.executed[0]
    assert params["extra_subject_contains"] == "%a%"
    assert params["op0"] == "%b%"
    # Two distinct subject ILIKE comparisons survive in the SQL.
    assert sql.count("unaccent(lower(coalesce(subject, ''))) ILIKE") == 2


def test_count_filtered_operator_clauses_emit_same_predicate(monkeypatch):
    # The count is driven through the same builder so it counts exactly the set
    # the listing returns.
    cursor = FakeCursor(fetchone_results=[(3,)])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.count_filtered(
        ["acc-1"], "ALL_MAIL", [],
        operator_clauses=[("has_attachments", True)],
    )
    sql, params = cursor.executed[0]
    assert "has_attachments = %(op0)s" in sql
    assert params["op0"] is True
    assert "COUNT(*)" in sql


# ===== _build_order_by (pure function — the sort whitelist) =====
# Pure helper that turns the public (sort, sort_dir) pair into the trusted
# ORDER BY body interpolated into the {order_by} slot of the four LIST
# templates. No mocks: input → output. The body is always UNPREFIXED so the
# same string resolves to em.* / d.* across all four templates.


def test_build_order_by_default_is_received_at_desc_with_pk_tiebreak():
    # None/None reproduces the historical fixed ordering exactly.
    assert _build_order_by(None, None) == "received_at DESC, account_id, provider_message_id"


def test_build_order_by_date_asc_flips_only_the_primary_direction():
    assert _build_order_by("date", "asc") == "received_at ASC, account_id, provider_message_id"


def test_build_order_by_date_desc_matches_the_default():
    assert _build_order_by("date", "desc") == "received_at DESC, account_id, provider_message_id"


@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_build_order_by_sender_uses_unaccent_name_then_email_fallback(sort_dir):
    direction = sort_dir.upper()
    result = _build_order_by("sender", sort_dir)
    # Sender sorts on the display name, falling back to email, accent/case-
    # insensitive (the natural order the rest of the app uses).
    assert result.startswith(
        f"lower(unaccent(coalesce(nullif(from_name, ''), from_email))) {direction} NULLS LAST,"
    )
    # A non-date sort always groups same-sender rows newest-first and ends
    # with the PK tie-break, regardless of the primary direction.
    assert result.endswith("received_at DESC, account_id, provider_message_id")


@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_build_order_by_subject_uses_unaccent_subject(sort_dir):
    direction = sort_dir.upper()
    result = _build_order_by("subject", sort_dir)
    assert result.startswith(f"lower(unaccent(coalesce(subject, ''))) {direction} NULLS LAST,")
    assert result.endswith("received_at DESC, account_id, provider_message_id")


def test_build_order_by_unknown_sort_falls_back_to_date_desc():
    # An unknown sort (reachable only via direct repository calls — the router
    # Literal blocks it over HTTP) falls back to the default without doubling
    # ``received_at`` and without injecting the raw value into the SQL.
    result = _build_order_by("size", "up")
    assert result == "received_at DESC, account_id, provider_message_id"
    assert "size" not in result


def test_build_order_by_unknown_sort_dir_defaults_to_desc():
    # A junk direction with a valid sort still resolves: only "asc" yields ASC.
    assert _build_order_by("subject", "sideways").startswith(
        "lower(unaccent(coalesce(subject, ''))) DESC NULLS LAST,"
    )


def test_build_order_by_always_ends_with_pk_tiebreak():
    # The total-ordering tie-break is non-negotiable for stable OFFSET paging
    # in every branch of the whitelist.
    for sort in ("date", "sender", "subject", "size"):
        for sort_dir in ("asc", "desc"):
            assert _build_order_by(sort, sort_dir).endswith("account_id, provider_message_id")


# ===== list_filtered — sort / sort_dir (the {order_by} slot) =====
# These are SQL-surface tests (FakeCursor): they assert the ORDER BY string
# produced by _build_order_by reaches the executed SQL, in all four LIST
# templates, and that the rest of each template (predicates, inner DISTINCT
# ON, WINDOW, LIMIT/OFFSET) is untouched by the slot change.


def test_list_filtered_default_sort_emits_unprefixed_received_at_order(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(["acc1"], "ALL_MAIL", [], 50, 0)
    sql, _ = cursor.executed[0]
    # Default (no sort args) → the historical order, UNPREFIXED in the slot.
    assert "ORDER BY received_at DESC, account_id, provider_message_id" in sql
    # Nothing leaked the legacy prefixed form into the regular template.
    assert "ORDER BY em.received_at DESC" not in sql


def test_list_filtered_sort_subject_asc_emits_subject_order(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0, sort="subject", sort_dir="asc",
    )
    sql, _ = cursor.executed[0]
    assert (
        "ORDER BY lower(unaccent(coalesce(subject, ''))) ASC NULLS LAST, "
        "received_at DESC, account_id, provider_message_id"
    ) in sql


def test_list_filtered_sort_sender_desc_emits_sender_order(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0, sort="sender", sort_dir="desc",
    )
    sql, _ = cursor.executed[0]
    assert (
        "ORDER BY lower(unaccent(coalesce(nullif(from_name, ''), from_email))) DESC NULLS LAST, "
        "received_at DESC, account_id, provider_message_id"
    ) in sql


def test_list_filtered_sort_reaches_distinct_template_without_breaking_inner_order(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], None, [], 50, 0,
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
        sort="subject", sort_dir="asc",
    )
    sql, _ = cursor.executed[0]
    # The external ORDER BY carries the chosen sort.
    assert "ORDER BY lower(unaccent(coalesce(subject, ''))) ASC NULLS LAST," in sql
    # The inner DISTINCT ON winner-selection ORDER BY is UNTOUCHED — sort only
    # reorders the page, it must not change which row survives the dedup.
    assert "DISTINCT ON (em.provider_message_id)" in sql
    assert "btrim(coalesce(em.to_email, '')) <> ''" in sql
    assert "em.received_at DESC NULLS LAST" in sql


def test_list_filtered_sort_reaches_grouped_template_without_breaking_partition(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=2)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0,
        group_by_thread=True, sort="sender", sort_dir="asc",
    )
    sql, _ = cursor.executed[0]
    # The external ORDER BY carries the chosen sort in the grouped template too.
    assert "ORDER BY lower(unaccent(coalesce(nullif(from_name, ''), from_email))) ASC NULLS LAST," in sql
    # The thread aggregation and partition key are untouched.
    assert "thread_message_count" in sql
    assert "PARTITION BY em.account_id, COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id)" in sql


def test_list_filtered_sort_reaches_grouped_distinct_template(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=3)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], None, [], 50, 0,
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
        group_by_thread=True, sort="subject", sort_dir="desc",
    )
    sql, _ = cursor.executed[0]
    assert "ORDER BY lower(unaccent(coalesce(subject, ''))) DESC NULLS LAST," in sql
    # The inner dedup + thread-key partition stay intact.
    assert "DISTINCT ON (em.provider_message_id)" in sql
    assert "PARTITION BY d1.thread_key" in sql


def test_list_filtered_chips_combined_emit_independent_op_params(monkeypatch):
    # The three quick-filter chips arrive as plain operator clauses; combined
    # they AND together with independent op{idx} params (no collision), exactly
    # like a repeated lupa operator.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.list_filtered(
        ["acc-1"], "ALL_MAIL", [], 50, 0,
        operator_clauses=[
            ("is_read_op", False),
            ("has_attachments", True),
            ("is_favorite_op", True),
        ],
    )
    sql, params = cursor.executed[0]
    assert "is_read = %(op0)s" in sql
    assert "has_attachments = %(op1)s" in sql
    assert "is_favorite = %(op2)s" in sql
    assert params["op0"] is False
    assert params["op1"] is True
    assert params["op2"] is True


def test_count_filtered_has_no_order_by_slot(monkeypatch):
    # count_filtered takes no sort params and its SQL never carries an ORDER BY
    # (nor an unresolved {order_by} slot) — COUNT does not order.
    cursor = FakeCursor(fetchone_results=[(7,)])
    patch_connection(monkeypatch, em_module, [cursor])
    em_module.email_metadata_store.count_filtered(["acc1"], "ALL_MAIL", [])
    sql, _ = cursor.executed[0]
    assert "ORDER BY" not in sql
    assert "{order_by}" not in sql


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
    # Python sort lacked, so OFFSET paging is total/stable. The {order_by}
    # slot now carries an UNPREFIXED body (resolves to ``d.*`` through the
    # single outer alias — see the ORDER-BY ALIAS NOTE in the queries module).
    assert "ORDER BY received_at DESC, account_id, provider_message_id" in sql
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


# ===== list_filtered / count_filtered — group_by_thread (conversation view) =====
# The (group_by_thread, distinct) matrix is a 2x2 selecting one of four
# LIST/COUNT template pairs. These lock that selection by asserting on the
# distinctive SQL surface of each template (window aggregates, partition
# key, the inner provider_message_id dedup for the virtual variant). The
# COUNT selector must track the LIST selector so the paginated total counts
# exactly what the page lists.


def test_list_filtered_grouped_regular_uses_account_thread_partition(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=2)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0, group_by_thread=True,
    )
    sql, _ = cursor.executed[0]
    # group_by_thread=True + distinct=False → LIST_GROUPED_BY_THREAD.
    assert "thread_message_count" in sql
    assert "bool_and(em.is_read)" in sql
    assert "bool_or(em.has_attachments)" in sql
    assert "bool_or(em.is_favorite)" in sql
    # Regular grouped key partitions by (account_id, thread_key) so two
    # distinct accounts never merge into one thread row.
    assert "PARTITION BY em.account_id, COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id)" in sql
    # threadless ('' / NULL) messages key by their own provider_message_id.
    assert "COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id)" in sql
    # NOT the virtual variant (no inner provider_message_id dedup).
    assert "DISTINCT ON (em.provider_message_id)" not in sql


def test_list_filtered_grouped_virtual_dedups_then_partitions_by_thread_key(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=3)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1", "acc2"], None, [], 50, 0,
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
        group_by_thread=True,
    )
    sql, _ = cursor.executed[0]
    # group_by_thread=True + distinct=True → LIST_GROUPED_BY_THREAD_DISTINCT.
    assert "thread_message_count" in sql
    # Inner level dedups by provider_message_id BEFORE grouping (collapses one
    # provider account connected under two mailboxes).
    assert "DISTINCT ON (em.provider_message_id)" in sql
    # Outer grouping partitions by thread_key ALONE (no account_id) — that is
    # what merges the same provider account across two mailboxes.
    assert "PARTITION BY d1.thread_key" in sql
    # Shared predicates still apply through the same helper.
    assert "NOT (box = ANY(%(box_not_in_list)s))" in sql


def test_list_filtered_grouped_chip_surfaces_thread_via_window_or_not_where(monkeypatch):
    # A quick-filter chip (here is:unread) must NOT land in the inner WHERE of
    # the grouped template — that would drop the thread's non-matching siblings
    # from the partition and corrupt the representative + count. Instead the
    # clause rides the {match_predicate} slot, OR-ed over the thread window
    # (bool_or(...) OVER w AS thread_matches), and the outer wrapper keeps only
    # surfacing threads (WHERE d.thread_matches). The box predicate stays in
    # the inner WHERE. Mirrors docs/features/ordenar-y-filtrar-listado.md § 5.
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=3)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0,
        group_by_thread=True,
        operator_clauses=[("is_read_op", False)],
    )
    sql, params = cursor.executed[0]
    # The chip becomes the windowed match-OR, not a row-level filter.
    assert "bool_or(is_read = %(op0)s)  OVER w AS thread_matches" in sql
    assert "WHERE d.thread_matches" in sql
    assert params["op0"] is False
    # Only the box predicate filters rows inside the inner WHERE; the chip
    # clause is NOT ANDed there (it only appears inside the bool_or SELECT
    # expression). Isolate the WHERE body (between the account_ids predicate
    # and the WINDOW clause).
    inner_where = sql.split("WHERE em.account_id")[1].split("WINDOW w")[0]
    assert "AND box = %(box)s" in inner_where
    assert "is_read = %(op0)s" not in inner_where


def test_list_filtered_grouped_no_search_or_chip_match_predicate_is_true(monkeypatch):
    # With neither search tokens nor chips/operators, the {match_predicate}
    # collapses to the literal TRUE so every thread surfaces (pre-feature
    # behaviour) — the windowed OR is a tautology, never an empty result.
    cursor = FakeCursor(fetchall_results=[[_row(thread_message_count=2)]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_filtered(
        ["acc1"], "ALL_MAIL", [], 50, 0, group_by_thread=True,
    )
    sql, _ = cursor.executed[0]
    assert "bool_or(TRUE)  OVER w AS thread_matches" in sql


def test_count_filtered_grouped_regular_counts_distinct_account_thread(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(4,)])
    patch_connection(monkeypatch, em_module, [cursor])

    total = em_module.email_metadata_store.count_filtered(
        ["acc1"], "ALL_MAIL", [], group_by_thread=True,
    )
    assert total == 4
    sql, _ = cursor.executed[0]
    # group_by_thread=True + distinct=False → COUNT_GROUPED_BY_THREAD: counts
    # threads (DISTINCT ON (account_id, thread_key)) that SURFACE — i.e. whose
    # match-OR over the partition is true — mirroring the LIST partition. With
    # no search/chip the match predicate is TRUE so every thread is counted.
    assert "COUNT(*)" in sql
    assert ("DISTINCT ON (em.account_id, COALESCE(NULLIF(em.thread_id, ''), "
            "em.provider_message_id))") in sql
    assert "WHERE t.thread_matches" in sql


def test_count_filtered_grouped_virtual_counts_distinct_thread_key(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(2,)])
    patch_connection(monkeypatch, em_module, [cursor])

    total = em_module.email_metadata_store.count_filtered(
        ["acc1", "acc2"], None, [],
        box_not_in=["TRASH", "SPAM"],
        distinct_provider_message_id=True,
        group_by_thread=True,
    )
    assert total == 2
    sql, _ = cursor.executed[0]
    # group_by_thread=True + distinct=True → COUNT_GROUPED_BY_THREAD_DISTINCT:
    # counts DISTINCT thread_key (account_id dropped), mirroring the LIST. The
    # vmbox surface filters message-by-message BEFORE grouping (no match
    # surfacing), so this template keeps the plain COUNT(DISTINCT thread_key).
    assert "COUNT(DISTINCT COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id))" in sql
    assert "em.account_id," not in sql.split("COUNT(DISTINCT")[1].split(")")[0]


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


# ===== _build_recipient_token_predicate (pure helper) =====
# Same shape as the search predicate but for one (email, name) column pair.
# Both UNION ALL branches of LIST_RECIPIENT_SUGGESTIONS reference the SAME
# rtok{i} params, so the helper is called once per branch and writes the
# same values each time (idempotent overwrite).


def test_build_recipient_token_predicate_empty_returns_empty_string_no_params():
    params: dict = {}
    assert _build_recipient_token_predicate([], "from_email", "from_name", params) == ""
    # No keys leak into params for an empty token list.
    assert params == {}


def test_build_recipient_token_predicate_starts_with_and_and_ors_the_pair():
    params: dict = {}
    clause = _build_recipient_token_predicate(["foo"], "from_email", "from_name", params)
    assert clause.startswith("AND ")
    # One OR block over the two columns of the pair, ILIKE on each side.
    assert clause.count("ILIKE") == 2
    assert "coalesce(from_email, '')" in clause
    assert "coalesce(from_name, '')" in clause
    assert " OR " in clause


def test_build_recipient_token_predicate_uses_the_columns_passed():
    # The to-side branch must reference to_email / to_name, not from_*.
    params: dict = {}
    clause = _build_recipient_token_predicate(["foo"], "to_email", "to_name", params)
    assert "coalesce(to_email, '')" in clause
    assert "coalesce(to_name, '')" in clause
    assert "from_email" not in clause
    assert "from_name" not in clause


def test_build_recipient_token_predicate_and_chains_multiple_tokens():
    params: dict = {}
    clause = _build_recipient_token_predicate(
        ["foo", "bar"], "from_email", "from_name", params,
    )
    # Two tokens → two parenthesised OR blocks joined by AND (4 ILIKEs total).
    assert clause.count("ILIKE") == 4
    assert params["rtok0"] == "%foo%"
    assert params["rtok1"] == "%bar%"
    # Tokens are AND-combined inside the clause.
    assert " AND " in clause[len("AND "):]


def test_build_recipient_token_predicate_escapes_metacharacters_per_token():
    params: dict = {}
    _build_recipient_token_predicate(
        ["50%_off\\bar"], "from_email", "from_name", params,
    )
    # Wrapped with % on both sides AFTER _escape_like, so the original
    # %, _ and \ arrive pre-escaped and do not act as ILIKE metacharacters.
    assert params["rtok0"] == "%50\\%\\_off\\\\bar%"


def test_build_recipient_token_predicate_is_idempotent_across_both_branches():
    # Called once for the from-pair and once for the to-pair against the
    # SAME params dict: the second call overwrites rtok{i} with identical
    # values (the shared named params are the whole point).
    params: dict = {}
    _build_recipient_token_predicate(["foo", "bar"], "from_email", "from_name", params)
    snapshot = dict(params)
    _build_recipient_token_predicate(["foo", "bar"], "to_email", "to_name", params)
    assert params == snapshot
    assert set(params) == {"rtok0", "rtok1"}


# ===== list_recipient_suggestions =====
# Aggregates recipient-autocomplete candidates from from_* (received) and
# to_* (sent) across the user's accounts, dedups by lower(email), excludes
# the user's own account addresses, orders by frequency then recency.


def _suggestion_row(**overrides):
    base = {
        "email": "alice@example.com",
        "name": "Alice",
        "frequency": 2,
        "last_seen": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return base


def test_list_recipient_suggestions_empty_account_ids_returns_empty_without_db_call(monkeypatch):
    # Mirror list_filtered: empty scope short-circuits BEFORE the connection
    # is touched (no leaking suggestions from an unauthorised account set).
    def _explode():
        raise AssertionError("get_connection must not be called for empty account_ids")

    monkeypatch.setattr(em_module.connection, "get_connection", _explode)
    assert em_module.email_metadata_store.list_recipient_suggestions([], ["am"], 8) == []


def test_list_recipient_suggestions_happy_path_sql_surface_and_params(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_suggestion_row()]])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_recipient_suggestions(
        ["acc1"], ["am"], 8,
    )
    assert len(result) == 1
    assert result[0]["email"] == "alice@example.com"
    sql, params = cursor.executed[0]
    # The aggregation CTE with both candidate branches.
    assert "WITH candidates" in sql
    assert sql.count("UNION ALL") == 1
    # Both UNION branches must restrict to non-SPAM/TRASH/DELETED boxes — a
    # regression dropping this would leak SPAM/TRASH addresses into the
    # recipient suggestions (load-bearing per repository_guide.md).
    assert sql.count("box NOT IN ('SPAM', 'TRASH', 'DELETED')") == 2
    # The own-address exclusion subquery against accounts.email_address.
    assert "lower(a.email_address)" in sql
    # Both branches share the rtok0 named param built from the single token.
    assert params["rtok0"] == "%am%"
    assert params["account_ids"] == ["acc1"]
    assert params["limit"] == 8


def test_list_recipient_suggestions_no_tokens_omits_token_predicate(monkeypatch):
    # An empty token list leaves both SQL slots empty: no ILIKE / rtok params.
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_recipient_suggestions(["acc1"], [], 8)
    sql, params = cursor.executed[0]
    assert "ILIKE" not in sql
    assert not any(k.startswith("rtok") for k in params)


def test_list_recipient_suggestions_escapes_token_metacharacters(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, em_module, [cursor])

    em_module.email_metadata_store.list_recipient_suggestions(
        ["acc1"], ["50%_off\\bar"], 8,
    )
    _, params = cursor.executed[0]
    assert params["rtok0"] == "%50\\%\\_off\\\\bar%"


def test_list_recipient_suggestions_returns_dicts_with_name_none_passed_through(monkeypatch):
    # The store does NOT normalise name=None/'' — that is the service's job.
    rows = [
        _suggestion_row(email="a@b.com", name=None),
        _suggestion_row(email="c@d.com", name=""),
    ]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, em_module, [cursor])

    result = em_module.email_metadata_store.list_recipient_suggestions(
        ["acc1"], ["am"], 8,
    )
    assert all(isinstance(r, dict) for r in result)
    assert result[0]["name"] is None
    assert result[1]["name"] == ""


def test_list_recipient_suggestions_invalid_text_returns_empty(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, em_module, [cursor])

    assert em_module.email_metadata_store.list_recipient_suggestions(
        ["not-a-uuid"], ["am"], 8,
    ) == []


def test_list_recipient_suggestions_psycopg2_error_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="Failed to list recipient suggestions"):
        em_module.email_metadata_store.list_recipient_suggestions(["acc1"], ["am"], 8)


def test_list_recipient_suggestions_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, em_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        em_module.email_metadata_store.list_recipient_suggestions(["acc1"], ["am"], 8)


def test_list_recipient_suggestions_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, em_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        em_module.email_metadata_store.list_recipient_suggestions(["acc1"], ["am"], 8)
