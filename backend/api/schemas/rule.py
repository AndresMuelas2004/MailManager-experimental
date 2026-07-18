"""
Pydantic schemas for the internal rule-engine endpoints (carpetas-y-reglas).

A rule condition is an exact ``from_email`` (normalised to lower/trim) and/or a
``subject`` substring — at least one is required. The action is fixed in this
version: assign the matched message to ``target_folder_id``. Rules are evaluated
by MISSELA at sync time (no native Gmail Filters / Outlook messageRules).

``apply_to_existing`` (create/update only, not persisted) enqueues a background
"apply to existing" job that classifies the already-synced mail matching the
rule.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# A light e-mail shape check — deliberately not the full RFC (nor the
# ``email-validator`` dependency EmailStr needs): one ``@`` between non-space
# runs, a dotted domain. The value is normalised to trimmed lower-case so the
# stored condition matches ``lower(from_email)`` exactly.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalise_from_email(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip().lower()
        if value == "":
            return None
    return value


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    match_from_email: str | None = Field(default=None, max_length=320)
    match_subject_contains: str | None = Field(default=None, min_length=1, max_length=500)
    target_folder_id: str = Field(..., min_length=1)
    is_enabled: bool = True
    apply_to_existing: bool = False

    @field_validator("match_from_email", mode="before")
    @classmethod
    def _normalise_email(cls, value: Any) -> Any:
        return _normalise_from_email(value)

    @field_validator("match_from_email")
    @classmethod
    def _validate_email_shape(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError("match_from_email must be a valid email address.")
        return value

    @model_validator(mode="after")
    def _require_a_condition(self) -> "RuleCreate":
        if self.match_from_email is None and self.match_subject_contains is None:
            raise ValueError(
                "A rule needs at least one condition (match_from_email or "
                "match_subject_contains)."
            )
        return self


class RuleUpdate(BaseModel):
    """Partial PATCH — every field optional. The at-least-one-condition rule is
    re-validated against the MERGED rule in the service (a PATCH cannot see the
    stored state), which surfaces a 422 ``rule_validation_error`` if the merge
    would clear both conditions."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    match_from_email: str | None = Field(default=None, max_length=320)
    match_subject_contains: str | None = Field(default=None, min_length=1, max_length=500)
    target_folder_id: str | None = Field(default=None, min_length=1)
    is_enabled: bool | None = None
    apply_to_existing: bool = False

    @field_validator("match_from_email", mode="before")
    @classmethod
    def _normalise_email(cls, value: Any) -> Any:
        return _normalise_from_email(value)

    @field_validator("match_from_email")
    @classmethod
    def _validate_email_shape(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError("match_from_email must be a valid email address.")
        return value


class RuleOut(BaseModel):
    rule_id: str
    owner_user_id: str
    name: str | None = None
    is_enabled: bool
    match_from_email: str | None = None
    match_subject_contains: str | None = None
    target_folder_id: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: Any) -> Any:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value


class RuleApplyStatusOut(BaseModel):
    """Progress of a rule's "apply to existing" job (mirrors backfill-status).

    ``status`` is the job status (``pending`` / ``running`` / ``completed`` /
    ``failed``) or ``none`` when the rule was never applied. ``active`` is
    ``True`` while the job is ``pending`` / ``running``.
    """

    status: str
    processed_count: int
    active: bool
