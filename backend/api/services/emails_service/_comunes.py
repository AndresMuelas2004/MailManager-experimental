"""Helpers transversales del servicio de emails: contexto de auth y persistencia de tokens refrescados."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from api.services.services_helpers import (
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    translate_database_error,
    unwrap_secret,
)
from database import (
    account_store,
    DatabaseError,
)


def _persist_refreshed_tokens(
    updated_tokens: dict[str, dict[str, Any]],
    label_lookup: dict[str, tuple[str, str, str]],
    *,
    fallback: type[ApiError],
) -> None:
    # ``fallback`` is a required keyword: every caller passes the ApiError
    # subclass matching its operation. A base ``ApiError`` default would emit
    # a code-less 500 if a future caller forgot it — a required arg fails
    # loudly at call time instead (every current call site is explicit).
    for account_label, token_payload in updated_tokens.items():
        ids = label_lookup.get(account_label)
        if not ids:
            continue
        mailbox_id, account_id, provider = ids
        payload = dict(token_payload or {})
        payload["access_token"] = unwrap_secret(payload.get("access_token"))
        payload["refresh_token"] = unwrap_secret(payload.get("refresh_token"))
        try:
            account_store.upsert_tokens(mailbox_id, account_id, provider, payload)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning("Unexpected token refresh persist error (%s): %s", type(exc).__name__, exc)
            raise fallback("Failed to persist refreshed tokens.") from exc


def _physical_message_identity(
    received_at: Any, from_email: str | None, subject: str | None,
) -> tuple[Any, str, str]:
    """Endpoint-independent identity of a physical message.

    Outlook returns a DIFFERENT REST id for the same physical message
    depending on which endpoint is asked (folder-delta sync vs. the
    mailbox-wide ``$filter=conversationId`` / ``$filter=flag/flagStatus``
    routes), and ``Prefer: IdType="ImmutableId"`` does NOT reconcile them
    (verified live — external-apis-used/Outlook/08). These three fields are
    parsed identically on every endpoint (all go through
    ``_parse_graph_message``), so together they identify the same physical
    message across them. ``received_at`` (a tz-aware datetime; equal
    instants hash equal even from different tzinfo) is the real
    discriminator — two distinct messages differ by send time — and
    ``from_email`` + ``subject`` harden it.

    Shared by the conversation lazy-sync (``conversacion.py``, thread-scoped)
    and the favourites sync reconciliation (``favoritos.py``, account-scoped)
    so both use the exact same normalisation and never silently diverge.
    """
    return (
        received_at,
        (from_email or "").strip().lower(),
        (subject or "").strip(),
    )


def _build_auth_context(
    accounts: list[dict[str, Any]],
    mailbox_id: str,
) -> tuple[
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    dict[str, tuple[str, str, str]],
]:
    """Build auth_payloads and label_lookup for accounts."""
    auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    label_lookup: dict[str, tuple[str, str, str]] = {}
    credentials_cache: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        provider = str(account.get("provider") or "").lower()
        if not account_id or not provider:
            continue
        if provider not in credentials_cache:
            credentials_cache[provider] = load_wrapped_app_credentials(provider)
        account_label = f"{mailbox_id}__{account_id}"
        auth_payloads[account_label] = (
            credentials_cache[provider],
            load_wrapped_account_tokens(mailbox_id, account_id, provider),
        )
        label_lookup[account_label] = (mailbox_id, account_id, provider)
    return auth_payloads, label_lookup
