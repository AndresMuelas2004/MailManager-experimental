"""Unit tests for ``api.services.rules_service``.

The rule / rule-apply / folder stores are monkeypatched. The load-bearing
service-only invariant is the PATCH-merge re-validation: ``RuleUpdate`` does NOT
enforce "≥1 condition" (a partial body cannot see the stored state), so the
service re-checks the MERGED rule and surfaces a 422 ``rule_validation_error``
when the merge would clear both conditions — driven by ``model_fields_set``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import (
    DatabaseQueryError,
    FolderNotFound,
    RuleNotFound,
    RuleValidationError,
)
from api.schemas.rule import RuleCreate, RuleUpdate
from api.services import rules_service
from database.errors.exceptions import QueryError


_USER_ID = "user-1"
_RULE_ID = "rule-1"
_FOLDER_ID = "folder-1"
_ISO = datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat()


def _rule_record(
    *,
    rule_id=_RULE_ID,
    owner_user_id=_USER_ID,
    name=None,
    is_enabled=True,
    match_from_email="boss@example.com",
    match_subject_contains=None,
    target_folder_id=_FOLDER_ID,
):
    return {
        "rule_id": rule_id,
        "owner_user_id": owner_user_id,
        "name": name,
        "is_enabled": is_enabled,
        "match_from_email": match_from_email,
        "match_subject_contains": match_subject_contains,
        "target_folder_id": target_folder_id,
        "created_at": _ISO,
        "updated_at": _ISO,
    }


def _owned_folder(owner_user_id=_USER_ID):
    return {"folder_id": _FOLDER_ID, "owner_user_id": owner_user_id, "name": "Universidad"}


class TestCreateRule:
    def test_happy_path_returns_model(self, monkeypatch):
        monkeypatch.setattr(rules_service.folder_store, "get", lambda _fid: _owned_folder())
        monkeypatch.setattr(rules_service.rule_store, "create", lambda row: _rule_record(**{
            k: v for k, v in row.items() if k != "rule_id"
        }, rule_id=row["rule_id"]))
        result = rules_service.create_rule(
            _USER_ID, RuleCreate(match_from_email="boss@example.com", target_folder_id=_FOLDER_ID),
        )
        assert result.match_from_email == "boss@example.com"
        assert result.target_folder_id == _FOLDER_ID

    def test_foreign_target_folder_is_404(self, monkeypatch):
        monkeypatch.setattr(rules_service.folder_store, "get", lambda _fid: _owned_folder(owner_user_id="other"))
        with pytest.raises(FolderNotFound):
            rules_service.create_rule(
                _USER_ID, RuleCreate(match_subject_contains="x", target_folder_id=_FOLDER_ID),
            )

    def test_apply_to_existing_enqueues_job(self, monkeypatch):
        monkeypatch.setattr(rules_service.folder_store, "get", lambda _fid: _owned_folder())
        monkeypatch.setattr(rules_service.rule_store, "create", lambda row: _rule_record(rule_id=row["rule_id"]))
        enqueued = {"called": False}
        monkeypatch.setattr(
            rules_service.rule_apply_store, "enqueue",
            lambda rid, uid: enqueued.__setitem__("called", True),
        )
        rules_service.create_rule(
            _USER_ID,
            RuleCreate(match_subject_contains="x", target_folder_id=_FOLDER_ID, apply_to_existing=True),
        )
        assert enqueued["called"] is True

    def test_does_not_enqueue_without_flag(self, monkeypatch):
        monkeypatch.setattr(rules_service.folder_store, "get", lambda _fid: _owned_folder())
        monkeypatch.setattr(rules_service.rule_store, "create", lambda row: _rule_record(rule_id=row["rule_id"]))
        enqueued = {"called": False}
        monkeypatch.setattr(
            rules_service.rule_apply_store, "enqueue",
            lambda rid, uid: enqueued.__setitem__("called", True),
        )
        rules_service.create_rule(
            _USER_ID, RuleCreate(match_subject_contains="x", target_folder_id=_FOLDER_ID),
        )
        assert enqueued["called"] is False


class TestRuleOwnership:
    def test_get_rule_foreign_owner_is_404(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record(owner_user_id="other"))
        with pytest.raises(RuleNotFound):
            rules_service.get_rule(_RULE_ID, _USER_ID)

    def test_get_rule_missing_is_404(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: None)
        with pytest.raises(RuleNotFound):
            rules_service.get_rule(_RULE_ID, _USER_ID)


class TestUpdateRule:
    def test_patch_clearing_both_conditions_is_422(self, monkeypatch):
        # Stored rule has only a from_email condition; clearing it leaves the
        # merged rule with NO condition — the service re-validation fires.
        monkeypatch.setattr(
            rules_service.rule_store, "get",
            lambda _rid: _rule_record(match_from_email="boss@example.com", match_subject_contains=None),
        )
        with pytest.raises(RuleValidationError):
            rules_service.update_rule(_RULE_ID, _USER_ID, RuleUpdate(match_from_email=None))

    def test_patch_keeping_a_condition_updates(self, monkeypatch):
        monkeypatch.setattr(
            rules_service.rule_store, "get",
            lambda _rid: _rule_record(match_from_email="boss@example.com"),
        )
        captured = {}

        def _update(merged):
            captured["merged"] = merged
            return _rule_record(match_from_email=merged["match_from_email"])

        monkeypatch.setattr(rules_service.rule_store, "update", _update)
        rules_service.update_rule(_RULE_ID, _USER_ID, RuleUpdate(match_from_email="new@example.com"))
        assert captured["merged"]["match_from_email"] == "new@example.com"

    def test_patch_target_folder_ownership_checked(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        monkeypatch.setattr(rules_service.folder_store, "get", lambda _fid: _owned_folder(owner_user_id="other"))
        with pytest.raises(FolderNotFound):
            rules_service.update_rule(_RULE_ID, _USER_ID, RuleUpdate(target_folder_id="new-folder"))

    def test_update_row_vanished_is_404(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        monkeypatch.setattr(rules_service.rule_store, "update", lambda _merged: None)
        with pytest.raises(RuleNotFound):
            rules_service.update_rule(_RULE_ID, _USER_ID, RuleUpdate(name="renamed"))


class TestDeleteRule:
    def test_delete_zero_rows_is_404(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        monkeypatch.setattr(rules_service.rule_store, "delete", lambda _rid: False)
        with pytest.raises(RuleNotFound):
            rules_service.delete_rule(_RULE_ID, _USER_ID)

    def test_db_error_translates(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())

        def _boom(_rid):
            raise QueryError("boom")

        monkeypatch.setattr(rules_service.rule_store, "delete", _boom)
        with pytest.raises(DatabaseQueryError):
            rules_service.delete_rule(_RULE_ID, _USER_ID)


class TestApplyStatus:
    def test_apply_enqueues_and_returns_status(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        enqueued = {"called": False}
        monkeypatch.setattr(
            rules_service.rule_apply_store, "enqueue",
            lambda rid, uid: enqueued.__setitem__("called", True),
        )
        monkeypatch.setattr(
            rules_service.rule_apply_store, "get",
            lambda _rid: {"status": "pending", "processed_count": 0},
        )
        result = rules_service.apply_rule(_RULE_ID, _USER_ID)
        assert enqueued["called"] is True
        assert result.status == "pending"
        assert result.active is True

    def test_status_none_when_no_job(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        monkeypatch.setattr(rules_service.rule_apply_store, "get", lambda _rid: None)
        result = rules_service.get_apply_status(_RULE_ID, _USER_ID)
        assert result.status == "none"
        assert result.processed_count == 0
        assert result.active is False

    def test_completed_status_is_not_active(self, monkeypatch):
        monkeypatch.setattr(rules_service.rule_store, "get", lambda _rid: _rule_record())
        monkeypatch.setattr(
            rules_service.rule_apply_store, "get",
            lambda _rid: {"status": "completed", "processed_count": 17},
        )
        result = rules_service.get_apply_status(_RULE_ID, _USER_ID)
        assert result.status == "completed"
        assert result.processed_count == 17
        assert result.active is False
