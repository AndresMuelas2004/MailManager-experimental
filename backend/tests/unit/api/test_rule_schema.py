"""Unit tests for the rule Pydantic schemas (carpetas-y-reglas).

Pure validation contracts. Two invariants carry weight: (1) ``RuleCreate``
enforces "at least one condition" via a ``model_validator`` (a 422 with the
FastAPI ``{"detail":[...]}`` envelope), while ``RuleUpdate`` does NOT — a PATCH
cannot see the stored state, so the merged-rule re-validation lives in the
service (a different ``rule_validation_error`` service envelope). (2)
``match_from_email`` is normalised to trimmed lower-case (so it matches
``lower(from_email)`` exactly) and shape-checked with a light e-mail regex.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.rule import (
    RuleApplyStatusOut,
    RuleCreate,
    RuleUpdate,
)


class TestRuleCreate:
    def test_valid_with_only_from_email(self):
        model = RuleCreate(match_from_email="boss@example.com", target_folder_id="f1")
        assert model.match_from_email == "boss@example.com"
        assert model.match_subject_contains is None
        assert model.is_enabled is True
        assert model.apply_to_existing is False

    def test_valid_with_only_subject(self):
        model = RuleCreate(match_subject_contains="invoice", target_folder_id="f1")
        assert model.match_subject_contains == "invoice"
        assert model.match_from_email is None

    def test_requires_at_least_one_condition(self):
        # The model_validator rejects a rule with neither condition.
        with pytest.raises(ValidationError):
            RuleCreate(target_folder_id="f1")

    def test_blank_from_email_normalises_to_none_and_then_fails_the_condition_check(self):
        # "" normalises to None, so with no subject either the ≥1-condition
        # invariant fires (blank is not a usable condition).
        with pytest.raises(ValidationError):
            RuleCreate(match_from_email="   ", target_folder_id="f1")

    def test_from_email_is_trimmed_and_lowercased(self):
        model = RuleCreate(match_from_email="  Boss@Example.COM ", target_folder_id="f1")
        assert model.match_from_email == "boss@example.com"

    def test_invalid_email_shape_rejected(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_from_email="not-an-email", target_folder_id="f1")

    def test_email_without_dotted_domain_rejected(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_from_email="user@localhost", target_folder_id="f1")

    def test_empty_subject_rejected_by_min_length(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_subject_contains="", target_folder_id="f1")

    def test_target_folder_id_required(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_from_email="a@b.com")

    def test_empty_target_folder_id_rejected(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_from_email="a@b.com", target_folder_id="")

    def test_unknown_key_rejected_by_extra_forbid(self):
        with pytest.raises(ValidationError):
            RuleCreate(match_subject_contains="x", target_folder_id="f1", action="delete")


class TestRuleUpdate:
    def test_empty_body_is_valid_at_schema_level(self):
        # The ≥1-condition invariant is re-checked in the SERVICE against the
        # merged rule, NOT here — an all-empty PATCH is schema-valid.
        model = RuleUpdate()
        assert model.match_from_email is None
        assert model.apply_to_existing is False

    def test_clearing_both_conditions_is_schema_valid(self):
        # Explicit nulls are accepted at the schema; the service returns the
        # rule_validation_error for the merged result.
        model = RuleUpdate(match_from_email=None, match_subject_contains=None)
        assert "match_from_email" in model.model_fields_set

    def test_from_email_normalised(self):
        assert RuleUpdate(match_from_email=" A@B.COM ").match_from_email == "a@b.com"

    def test_invalid_email_shape_rejected(self):
        with pytest.raises(ValidationError):
            RuleUpdate(match_from_email="bad")

    def test_empty_subject_rejected(self):
        with pytest.raises(ValidationError):
            RuleUpdate(match_subject_contains="")

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            RuleUpdate(nope=1)


class TestRuleApplyStatusOut:
    def test_accepts_the_none_status(self):
        # "none" is the sentinel for a rule that was never applied (no job).
        model = RuleApplyStatusOut(status="none", processed_count=0, active=False)
        assert model.status == "none"
        assert model.active is False

    def test_running_status_is_active(self):
        model = RuleApplyStatusOut(status="running", processed_count=42, active=True)
        assert model.processed_count == 42
        assert model.active is True
