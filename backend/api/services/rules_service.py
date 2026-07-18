"""
Service layer for the internal rule engine (carpetas-y-reglas).

Rules are user-level. CRUD plus the "apply to existing" enqueue and its status
read. The at-least-one-condition invariant is enforced at the schema for create
and re-checked here for a PATCH (a partial update cannot see the stored state).
The target folder must belong to the user (a foreign / missing folder → 404).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    FolderNotFound,
    RuleApplyStatusError,
    RuleNotFound,
    RuleOperationError,
    RuleValidationError,
)
from api.schemas.rule import (
    RuleApplyStatusOut,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)
from api.services.services_helpers import translate_database_error
from database import (
    DatabaseError,
    folder_store,
    rule_apply_store,
    rule_store,
)


def _load_owned_rule(rule_id: str, user_id: str) -> dict[str, Any]:
    try:
        record = rule_store.get(rule_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule lookup error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to look up rule.") from exc
    if record is None or str(record.get("owner_user_id")) != str(user_id):
        raise RuleNotFound(f"Rule '{rule_id}' not found.")
    return record


def _ensure_folder_owned(target_folder_id: str, user_id: str) -> None:
    try:
        folder = folder_store.get(target_folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder lookup error during rule op (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to look up target folder for rule.") from exc
    if folder is None or str(folder.get("owner_user_id")) != str(user_id):
        raise FolderNotFound(
            f"Target folder '{target_folder_id}' not found while validating rule."
        )


def _enqueue_apply(rule_id: str, user_id: str) -> None:
    """Enqueue an "apply to existing" job. INDEPENDENT of the worker flag — the
    job simply waits ``pending`` until the worker runs (no fallback path)."""
    try:
        rule_apply_store.enqueue(rule_id, user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule apply enqueue error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to enqueue the rule apply-to-existing job.") from exc


def create_rule(user_id: str, payload: RuleCreate) -> RuleOut:
    _ensure_folder_owned(payload.target_folder_id, user_id)
    rule_id = str(uuid.uuid4())
    try:
        record = rule_store.create({
            "rule_id": rule_id,
            "owner_user_id": user_id,
            "name": payload.name,
            "is_enabled": payload.is_enabled,
            "match_from_email": payload.match_from_email,
            "match_subject_contains": payload.match_subject_contains,
            "target_folder_id": payload.target_folder_id,
        })
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule create error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to create rule.") from exc
    if payload.apply_to_existing:
        _enqueue_apply(rule_id, user_id)
    return RuleOut(**record)


def list_rules(user_id: str) -> list[RuleOut]:
    try:
        rows = rule_store.list_by_owner(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule listing error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to list rules.") from exc
    return [RuleOut(**row) for row in rows]


def get_rule(rule_id: str, user_id: str) -> RuleOut:
    return RuleOut(**_load_owned_rule(rule_id, user_id))


def update_rule(rule_id: str, user_id: str, payload: RuleUpdate) -> RuleOut:
    current = _load_owned_rule(rule_id, user_id)
    fields_set = payload.model_fields_set

    merged = {
        "rule_id": rule_id,
        "name": payload.name if "name" in fields_set else current.get("name"),
        "is_enabled": payload.is_enabled if "is_enabled" in fields_set else current.get("is_enabled"),
        "match_from_email": (
            payload.match_from_email if "match_from_email" in fields_set
            else current.get("match_from_email")
        ),
        "match_subject_contains": (
            payload.match_subject_contains if "match_subject_contains" in fields_set
            else current.get("match_subject_contains")
        ),
        "target_folder_id": (
            payload.target_folder_id if "target_folder_id" in fields_set
            else current.get("target_folder_id")
        ),
    }

    # Re-validate the at-least-one-condition invariant against the MERGED rule —
    # a PATCH clearing both conditions is a 422 the schema cannot catch.
    if merged["match_from_email"] is None and merged["match_subject_contains"] is None:
        raise RuleValidationError(
            "A rule needs at least one condition after the update "
            "(match_from_email or match_subject_contains)."
        )

    if "target_folder_id" in fields_set:
        _ensure_folder_owned(merged["target_folder_id"], user_id)

    try:
        record = rule_store.update(merged)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule update error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to update rule.") from exc
    if record is None:
        raise RuleNotFound(f"Rule '{rule_id}' disappeared during update.")

    if payload.apply_to_existing:
        _enqueue_apply(rule_id, user_id)
    return RuleOut(**record)


def delete_rule(rule_id: str, user_id: str) -> None:
    _load_owned_rule(rule_id, user_id)
    try:
        deleted = rule_store.delete(rule_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule delete error (%s): %s", type(exc).__name__, exc)
        raise RuleOperationError("Failed to delete rule.") from exc
    if not deleted:
        raise RuleNotFound(f"Rule '{rule_id}' disappeared before delete.")


def apply_rule(rule_id: str, user_id: str) -> RuleApplyStatusOut:
    """Trigger (or restart) the "apply to existing" job and return its status."""
    _load_owned_rule(rule_id, user_id)
    _enqueue_apply(rule_id, user_id)
    return get_apply_status(rule_id, user_id)


def get_apply_status(rule_id: str, user_id: str) -> RuleApplyStatusOut:
    """Return the rule's apply-to-existing progress. Local-only, cheap poll."""
    _load_owned_rule(rule_id, user_id)
    try:
        job = rule_apply_store.get(rule_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected rule apply-status error (%s): %s", type(exc).__name__, exc)
        raise RuleApplyStatusError("Failed to load the rule apply status.") from exc
    if job is None:
        return RuleApplyStatusOut(status="none", processed_count=0, active=False)
    status = str(job.get("status") or "none")
    return RuleApplyStatusOut(
        status=status,
        processed_count=int(job.get("processed_count") or 0),
        active=status in ("pending", "running"),
    )
