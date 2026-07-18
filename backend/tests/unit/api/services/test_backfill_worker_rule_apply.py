"""Unit tests for the rule-apply ("apply to existing") job in ``backfill_worker``.

Unlike backfill / draft-sync (single account, single provider), a rule apply
spans EVERY account of the user, so ``_run_rule_apply_job`` builds a
multi-account manager and routes each matched message to ITS OWN
``account_label``. The names it uses are imported by name, so patches target the
``backfill_worker`` module directly (facade re-exports would not intercept). The
write gate is a ``nullcontext`` here (``_DB_WRITE_GATE`` is None without a
dispatcher), so the direct calls need no pool.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from api.services import backfill_worker


@pytest.fixture(autouse=True)
def _no_db_gate(monkeypatch):
    # Guarantee the nullcontext gate regardless of test ordering (the dispatcher
    # loop reassigns _DB_WRITE_GATE via ``global`` in sibling tests).
    monkeypatch.setattr(backfill_worker, "_DB_WRITE_GATE", None)


def _stop_event() -> threading.Event:
    return threading.Event()


def _row(pmid="m1", account_id="acc-1", mailbox_id="mb-1"):
    return {
        "account_id": account_id,
        "mailbox_id": mailbox_id,
        "provider_message_id": pmid,
        "received_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def _wire_rule_apply(monkeypatch, *, rule, folder, records=None, rows=None, label_by_account=None):
    monkeypatch.setattr(backfill_worker.rule_store, "get", lambda _rid: rule)
    monkeypatch.setattr(backfill_worker.folder_store, "get", lambda _fid: folder)
    monkeypatch.setattr(
        backfill_worker, "_resolve_user_account_records",
        lambda _uid: records if records is not None else [],
    )
    monkeypatch.setattr(
        backfill_worker, "_authenticate_multi_account",
        lambda _records: ("MANAGER", label_by_account or {}),
    )
    monkeypatch.setattr(
        backfill_worker.email_metadata_store, "list_messages_matching_rule",
        lambda *a, **k: list(rows or []),
    )


class TestRunRuleApplyJob:
    def test_missing_rule_marks_completed_without_scan(self, monkeypatch):
        completed = []
        monkeypatch.setattr(backfill_worker.rule_store, "get", lambda _rid: None)
        monkeypatch.setattr(backfill_worker.rule_apply_store, "mark_completed", lambda rid: completed.append(rid))
        # If the scan ran it would need list_messages_matching_rule; leaving it
        # unstubbed proves the early mark_completed path is taken.
        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        assert completed == ["r1"]

    def test_missing_folder_marks_completed(self, monkeypatch):
        completed = []
        monkeypatch.setattr(backfill_worker.rule_store, "get", lambda _rid: {"target_folder_id": "f1"})
        monkeypatch.setattr(backfill_worker.folder_store, "get", lambda _fid: None)
        monkeypatch.setattr(backfill_worker.rule_apply_store, "mark_completed", lambda rid: completed.append(rid))
        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        assert completed == ["r1"]

    def test_no_records_marks_completed(self, monkeypatch):
        completed = []
        _wire_rule_apply(
            monkeypatch,
            rule={"target_folder_id": "f1", "match_from_email": "a@b.com", "match_subject_contains": None},
            folder={"name": "Universidad"},
            records=[],
        )
        monkeypatch.setattr(backfill_worker.rule_apply_store, "mark_completed", lambda rid: completed.append(rid))
        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        assert completed == ["r1"]

    def test_happy_path_assigns_each_row_to_its_own_label_then_completes(self, monkeypatch):
        assigned = []
        completed = []
        _wire_rule_apply(
            monkeypatch,
            rule={"target_folder_id": "f1", "match_from_email": "a@b.com", "match_subject_contains": None},
            folder={"name": "Universidad"},
            records=[{"account_id": "acc-1"}],
            rows=[_row(pmid="m1"), _row(pmid="m2")],
            label_by_account={"acc-1": "mb-1__acc-1"},
        )
        monkeypatch.setattr(
            backfill_worker, "assign_folder_provider_first",
            lambda manager, label, aid, pmid, fid, fname, **k: assigned.append((label, pmid, fid, fname)),
        )
        monkeypatch.setattr(backfill_worker.rule_apply_store, "update_progress", lambda *a, **k: None)
        monkeypatch.setattr(backfill_worker.rule_apply_store, "mark_completed", lambda rid: completed.append(rid))

        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        assert assigned == [
            ("mb-1__acc-1", "m1", "f1", "Universidad"),
            ("mb-1__acc-1", "m2", "f1", "Universidad"),
        ]
        assert completed == ["r1"]

    def test_per_message_failure_is_swallowed_and_job_completes(self, monkeypatch):
        completed = []
        _wire_rule_apply(
            monkeypatch,
            rule={"target_folder_id": "f1", "match_from_email": "a@b.com", "match_subject_contains": None},
            folder={"name": "Universidad"},
            records=[{"account_id": "acc-1"}],
            rows=[_row(pmid="m1")],
            label_by_account={"acc-1": "mb-1__acc-1"},
        )

        def _boom(*a, **k):
            raise RuntimeError("dead token account")

        monkeypatch.setattr(backfill_worker, "assign_folder_provider_first", _boom)
        monkeypatch.setattr(backfill_worker.rule_apply_store, "update_progress", lambda *a, **k: None)
        monkeypatch.setattr(backfill_worker.rule_apply_store, "mark_completed", lambda rid: completed.append(rid))

        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        # One bad message never fails the whole apply.
        assert completed == ["r1"]

    def test_unexpected_error_marks_failed(self, monkeypatch):
        failed = []
        monkeypatch.setattr(backfill_worker.rule_store, "get", lambda _rid: {"target_folder_id": "f1"})
        monkeypatch.setattr(backfill_worker.folder_store, "get", lambda _fid: {"name": "Universidad"})
        monkeypatch.setattr(backfill_worker, "_resolve_user_account_records", lambda _uid: [{"account_id": "acc-1"}])
        monkeypatch.setattr(backfill_worker, "_authenticate_multi_account", lambda _r: ("M", {"acc-1": "mb__acc"}))

        def _boom(*a, **k):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(backfill_worker.email_metadata_store, "list_messages_matching_rule", _boom)
        monkeypatch.setattr(
            backfill_worker.rule_apply_store, "mark_failed",
            lambda rid, err: failed.append((rid, err)),
        )
        backfill_worker._run_rule_apply_job(
            {"rule_id": "r1", "owner_user_id": "u1", "processed_count": 0, "page_cursor": None},
            _stop_event(),
        )
        assert failed == [("r1", "unexpected")]


class TestApplyCursor:
    def test_encode_decode_round_trip(self):
        encoded = backfill_worker._encode_apply_cursor(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "acc-1", "m1",
        )
        received_at, account_id, pmid = backfill_worker._decode_apply_cursor(encoded)
        assert account_id == "acc-1"
        assert pmid == "m1"
        assert received_at.startswith("2026-01-02")

    def test_none_cursor_decodes_to_none(self):
        assert backfill_worker._decode_apply_cursor(None) is None

    def test_corrupt_cursor_restarts_scan(self):
        # A corrupt checkpoint decodes to None so the scan restarts from the top
        # (assignment is idempotent, so a restart is safe).
        assert backfill_worker._decode_apply_cursor("{not valid json") is None
        assert backfill_worker._decode_apply_cursor('{"missing":"keys"}') is None


class TestResolveUserAccountRecords:
    def test_n_plus_one_resolves_records_and_skips_missing(self, monkeypatch):
        monkeypatch.setattr(
            backfill_worker.account_store, "list_account_ids_by_user",
            lambda _uid: ["a1", "a2", "a3"],
        )
        resolved = {"a1": {"account_id": "a1"}, "a2": None, "a3": {"account_id": "a3"}}
        monkeypatch.setattr(
            backfill_worker.account_store, "get_by_id_for_user",
            lambda aid, _uid: resolved[aid],
        )
        records = backfill_worker._resolve_user_account_records("u1")
        assert [r["account_id"] for r in records] == ["a1", "a3"]

    def test_list_failure_degrades_to_empty(self, monkeypatch):
        def _boom(_uid):
            raise RuntimeError("db down")

        monkeypatch.setattr(backfill_worker.account_store, "list_account_ids_by_user", _boom)
        assert backfill_worker._resolve_user_account_records("u1") == []


class TestAuthenticateMultiAccount:
    def test_labels_each_account_to_its_own_mailbox(self, monkeypatch):
        class _FakeManager:
            def authenticate_all_silent(self, payloads):
                return {}

        monkeypatch.setattr(backfill_worker, "build_manager_for_accounts", lambda _r: _FakeManager())
        monkeypatch.setattr(backfill_worker, "load_wrapped_app_credentials", lambda _p: {})
        monkeypatch.setattr(backfill_worker, "load_wrapped_account_tokens", lambda *a: {})
        records = [
            {"account_id": "a1", "mailbox_id": "mbA", "provider": "gmail"},
            {"account_id": "a2", "mailbox_id": "mbB", "provider": "outlook"},
        ]
        _manager, label_by_account = backfill_worker._authenticate_multi_account(records)
        assert label_by_account == {"a1": "mbA__a1", "a2": "mbB__a2"}

    def test_refreshed_tokens_are_persisted(self, monkeypatch):
        class _FakeManager:
            def authenticate_all_silent(self, payloads):
                return {"mbA__a1": {"access_token": "new"}}

        persisted = {"called": False}
        monkeypatch.setattr(backfill_worker, "build_manager_for_accounts", lambda _r: _FakeManager())
        monkeypatch.setattr(backfill_worker, "load_wrapped_app_credentials", lambda _p: {})
        monkeypatch.setattr(backfill_worker, "load_wrapped_account_tokens", lambda *a: {})
        monkeypatch.setattr(
            backfill_worker, "_persist_refreshed_tokens",
            lambda refreshed, lookup, *, fallback: persisted.__setitem__("called", True),
        )
        backfill_worker._authenticate_multi_account([{"account_id": "a1", "mailbox_id": "mbA", "provider": "gmail"}])
        assert persisted["called"] is True
