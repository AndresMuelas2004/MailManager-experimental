"""Unit tests for ``api.services.folders_rules_eval`` (sync-time rule eval).

Covers the pure matcher (exact lower/trim sender AND accent-/case-insensitive
subject substring, AND-combined, no-condition never matches) and the best-effort
``run_rule_evaluation`` orchestration (no-op without targets/rules, per-message
swallow, folder-deleted-between-load skip).
"""

from __future__ import annotations

import pytest

from api.services import folders_rules_eval
from tests.shared.email_fakes import build_metadata


_USER_ID = "user-1"
_FOLDER_ID = "folder-1"


def _rule(*, from_email=None, subject=None, target=_FOLDER_ID):
    return {
        "match_from_email": from_email,
        "match_subject_contains": subject,
        "target_folder_id": target,
    }


class TestNorm:
    def test_strips_accents_and_lowercases(self):
        assert folders_rules_eval._norm("Facturación") == "facturacion"

    def test_none_becomes_empty(self):
        assert folders_rules_eval._norm(None) == ""


class TestRuleMatches:
    def test_sender_exact_case_and_trim_insensitive(self):
        rule = _rule(from_email="boss@example.com")
        assert folders_rules_eval._rule_matches(rule, "  Boss@Example.COM ", "anything")

    def test_sender_mismatch_fails(self):
        rule = _rule(from_email="boss@example.com")
        assert not folders_rules_eval._rule_matches(rule, "other@example.com", "x")

    def test_subject_substring_accent_insensitive(self):
        rule = _rule(subject="facturacion")
        assert folders_rules_eval._rule_matches(rule, "x@y.com", "Tu Facturación mensual")

    def test_subject_not_contained_fails(self):
        rule = _rule(subject="invoice")
        assert not folders_rules_eval._rule_matches(rule, "x@y.com", "newsletter")

    def test_both_conditions_are_anded(self):
        rule = _rule(from_email="boss@example.com", subject="report")
        assert folders_rules_eval._rule_matches(rule, "boss@example.com", "Weekly report")
        # Sender matches but subject does not → no match.
        assert not folders_rules_eval._rule_matches(rule, "boss@example.com", "unrelated")

    def test_no_condition_never_matches(self):
        assert not folders_rules_eval._rule_matches(_rule(), "a@b.com", "subject")


class TestRunRuleEvaluation:
    def test_no_targets_short_circuits(self, monkeypatch):
        # A rule store that raises proves the empty-targets guard runs first.
        def _boom(_uid):
            raise AssertionError("must not load rules for empty targets")

        monkeypatch.setattr(folders_rules_eval.rule_store, "list_active_by_owner", _boom)
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, [])

    def test_no_active_rules_short_circuits(self, monkeypatch):
        monkeypatch.setattr(folders_rules_eval.rule_store, "list_active_by_owner", lambda _uid: [])
        called = {"folders": False}
        monkeypatch.setattr(
            folders_rules_eval.folder_store, "list_by_owner",
            lambda _uid: called.__setitem__("folders", True) or [],
        )
        targets = [("mb__acc", "acc", [build_metadata(provider_message_id="m1")])]
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, targets)
        assert called["folders"] is False

    def test_matching_message_is_assigned(self, monkeypatch):
        monkeypatch.setattr(
            folders_rules_eval.rule_store, "list_active_by_owner",
            lambda _uid: [_rule(from_email="boss@example.com")],
        )
        monkeypatch.setattr(
            folders_rules_eval.folder_store, "list_by_owner",
            lambda _uid: [{"folder_id": _FOLDER_ID, "name": "Universidad"}],
        )
        assigned = []
        monkeypatch.setattr(
            folders_rules_eval, "assign_folder_provider_first",
            lambda manager, label, aid, pmid, fid, fname, **k: assigned.append((pmid, fid, fname)),
        )
        meta = build_metadata(provider_message_id="m1", from_email="boss@example.com")
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, [("mb__acc", "acc", [meta])])
        assert assigned == [("m1", _FOLDER_ID, "Universidad")]

    def test_non_matching_message_is_not_assigned(self, monkeypatch):
        monkeypatch.setattr(
            folders_rules_eval.rule_store, "list_active_by_owner",
            lambda _uid: [_rule(from_email="boss@example.com")],
        )
        monkeypatch.setattr(
            folders_rules_eval.folder_store, "list_by_owner",
            lambda _uid: [{"folder_id": _FOLDER_ID, "name": "Universidad"}],
        )
        assigned = []
        monkeypatch.setattr(
            folders_rules_eval, "assign_folder_provider_first",
            lambda *a, **k: assigned.append(a),
        )
        meta = build_metadata(provider_message_id="m1", from_email="stranger@example.com")
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, [("mb__acc", "acc", [meta])])
        assert assigned == []

    def test_folder_deleted_between_load_and_eval_is_skipped(self, monkeypatch):
        # Rule targets a folder that is no longer in the loaded name map.
        monkeypatch.setattr(
            folders_rules_eval.rule_store, "list_active_by_owner",
            lambda _uid: [_rule(from_email="boss@example.com", target="ghost-folder")],
        )
        monkeypatch.setattr(folders_rules_eval.folder_store, "list_by_owner", lambda _uid: [])
        assigned = []
        monkeypatch.setattr(
            folders_rules_eval, "assign_folder_provider_first",
            lambda *a, **k: assigned.append(a),
        )
        meta = build_metadata(provider_message_id="m1", from_email="boss@example.com")
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, [("mb__acc", "acc", [meta])])
        assert assigned == []

    def test_assignment_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            folders_rules_eval.rule_store, "list_active_by_owner",
            lambda _uid: [_rule(from_email="boss@example.com")],
        )
        monkeypatch.setattr(
            folders_rules_eval.folder_store, "list_by_owner",
            lambda _uid: [{"folder_id": _FOLDER_ID, "name": "Universidad"}],
        )

        def _boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(folders_rules_eval, "assign_folder_provider_first", _boom)
        meta = build_metadata(provider_message_id="m1", from_email="boss@example.com")
        # Best-effort: the post-response task must NOT raise on a per-message fail.
        folders_rules_eval.run_rule_evaluation(object(), _USER_ID, [("mb__acc", "acc", [meta])])
