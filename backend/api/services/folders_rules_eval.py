"""
Sync-time rule evaluation (carpetas-y-reglas §5.2).

Runs as a post-response background task scheduled by ``sync_email_metadata``:
for the messages NEWLY upserted in this sync, evaluate the user's ACTIVE rules
and assign the target folder to each match (Provider-First, multi-membership).

Best-effort throughout — a failure on one message/rule never aborts the rest
(the task runs after the sync response is already sent). Ordering guarantee: the
membership reconciliation (§5.1) is SYNCHRONOUS in the sync loop, so it always
precedes this task; the labels this task adds surface in the NEXT sync's
snapshot, where reconciliation preserves them.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import FolderOperationError
from api.services.services_helpers import assign_folder_provider_first
from core.email import EmailManager
from database import folder_store, rule_store


def _norm(value: str | None) -> str:
    """Accent- and case-insensitive normalisation, approximating Postgres
    ``unaccent(lower(...))`` so the sync-time subject match agrees with the
    SQL used by the "apply to existing" scan."""
    if not value:
        return ""
    stripped = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


def _rule_matches(rule: dict[str, Any], from_email: str, subject: str) -> bool:
    """True when the message satisfies the rule (from_email exact AND/OR subject
    substring). Mirrors the SQL predicates: exact lower-cased email, accent-/
    case-insensitive subject substring. At least one condition is guaranteed by
    the rule CHECK; a rule with neither never matches (defensive)."""
    match_from = rule.get("match_from_email")
    match_subject = rule.get("match_subject_contains")
    if match_from is None and match_subject is None:
        return False
    if match_from is not None:
        if (from_email or "").strip().lower() != str(match_from).strip().lower():
            return False
    if match_subject is not None:
        if _norm(str(match_subject)) not in _norm(subject):
            return False
    return True


def run_rule_evaluation(
    manager: EmailManager,
    user_id: str,
    targets: list[tuple[str, str, list[Any]]],
) -> None:
    """Evaluate the user's active rules over the newly-upserted messages.

    ``targets`` is ``(account_label, account_id, [EmailMetadata, ...])`` per
    synced account (only the NEW upserts of this sync, so the scan is bounded).
    Reuses the ``manager`` already authenticated during the sync. Best-effort:
    every failure is logged and swallowed.
    """
    if not targets:
        return
    try:
        rules = rule_store.list_active_by_owner(user_id)
    except Exception as exc:
        logger.warning(
            "Rule evaluation skipped: failed to load active rules (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
        return
    if not rules:
        return
    try:
        folder_names = {
            str(f["folder_id"]): str(f["name"]) for f in folder_store.list_by_owner(user_id)
        }
    except Exception as exc:
        logger.warning(
            "Rule evaluation skipped: failed to load folders (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
        return

    for account_label, account_id, upserts in targets:
        for meta in upserts:
            for rule in rules:
                if not _rule_matches(rule, meta.from_email, meta.subject):
                    continue
                folder_id = str(rule["target_folder_id"])
                folder_name = folder_names.get(folder_id)
                if folder_name is None:
                    continue  # folder deleted between load and eval
                try:
                    assign_folder_provider_first(
                        manager, account_label, account_id, meta.provider_message_id,
                        folder_id, folder_name, fallback=FolderOperationError,
                    )
                except Exception as exc:
                    logger.warning(
                        "Rule assignment failed for message %s -> folder %s (%s): %s",
                        meta.provider_message_id, folder_id, type(exc).__name__, exc,
                        exc_info=exc,
                    )
                    continue
