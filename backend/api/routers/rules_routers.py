"""
Rule routers (carpetas-y-reglas).

User-level (no ``/mailboxes/{id}`` prefix). CRUD plus the "apply to existing"
trigger and its status poll — all local-only (the apply itself runs in the
background worker).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.routers.routers_helpers import require_session
from api.schemas.rule import (
    RuleApplyStatusOut,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)
from api.services import rules_service


router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(user_id: str = Depends(require_session)) -> list[RuleOut]:
    return rules_service.list_rules(user_id)


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(
    payload: RuleCreate,
    user_id: str = Depends(require_session),
) -> RuleOut:
    return rules_service.create_rule(user_id, payload)


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(
    rule_id: str,
    user_id: str = Depends(require_session),
) -> RuleOut:
    return rules_service.get_rule(rule_id, user_id)


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    user_id: str = Depends(require_session),
) -> RuleOut:
    return rules_service.update_rule(rule_id, user_id, payload)


@router.delete("/{rule_id}")
def delete_rule(
    rule_id: str,
    user_id: str = Depends(require_session),
) -> dict[str, str]:
    rules_service.delete_rule(rule_id, user_id)
    return {"status": "deleted"}


@router.post("/{rule_id}/apply", response_model=RuleApplyStatusOut)
def apply_rule(
    rule_id: str,
    user_id: str = Depends(require_session),
) -> RuleApplyStatusOut:
    return rules_service.apply_rule(rule_id, user_id)


@router.get("/{rule_id}/apply-status", response_model=RuleApplyStatusOut)
def get_apply_status(
    rule_id: str,
    user_id: str = Depends(require_session),
) -> RuleApplyStatusOut:
    return rules_service.get_apply_status(rule_id, user_id)
