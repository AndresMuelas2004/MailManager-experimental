"""Helpers de carpetas (carpetas-y-reglas): materializacion perezosa,
asignacion Provider-First, reconciliacion de pertenencia y chips.

Leaf shared by ``folders_service`` (per-email assign), the sync-time rule
evaluation (``emails_service``) and the rule-apply worker (``backfill_worker``),
so the Provider-First assignment logic lives in exactly one place — none of those
flat modules may import another's private submodule.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from api.schemas.folder import FolderRef
from core.email import CoreError, EmailManager
from database import DatabaseError, account_store, folder_store

from .contexto_cuentas import (
    build_manager_for_accounts,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    unwrap_secret,
)
from .traduccion_errores import translate_core_error, translate_database_error


def authenticate_single_account(
    account: dict[str, Any],
    mailbox_id: str,
    *,
    fallback: type[ApiError],
) -> tuple[EmailManager, str]:
    """Run the Auth Context Sequence for ONE account and return
    ``(manager, account_label)``.

    Follows the canonical order (build manager → load wrapped creds/tokens →
    silent auth → persist refreshed tokens → raise on silent-auth errors). A
    token-persist failure surfaces as *fallback* (never a generic 500); a
    silent-auth failure surfaces as ``AccountNotConnected`` (409).
    """
    provider = str(account.get("provider") or "").lower()
    account_id = str(account.get("account_id") or "")
    account_label = f"{mailbox_id}__{account_id}"

    manager = build_manager_for_accounts([account])
    app_credentials = load_wrapped_app_credentials(provider)
    tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
    updated_tokens = manager.authenticate_all_silent(
        {account_label: (app_credentials, tokens)},
    )
    if updated_tokens:
        for _label, payload in updated_tokens.items():
            data = dict(payload or {})
            data["access_token"] = unwrap_secret(data.get("access_token"))
            data["refresh_token"] = unwrap_secret(data.get("refresh_token"))
            try:
                account_store.upsert_tokens(mailbox_id, account_id, provider, data)
            except DatabaseError as exc:
                raise translate_database_error(exc, fallback=fallback) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected token refresh persist error during folder op (%s): %s",
                    type(exc).__name__, exc,
                )
                raise fallback("Failed to persist refreshed tokens during folder operation.") from exc
    raise_on_silent_auth_errors(manager.get_last_errors(), fallback=fallback)
    return manager, account_label


def ensure_folder_materialized(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    folder_id: str,
    folder_name: str,
    *,
    fallback: type[ApiError],
) -> str:
    """Return the provider_ref of (folder, account), materialising it lazily.

    Reads the stored ``folder_account_links`` first so a repeated assign never
    re-lists the provider's labels; only the FIRST use in an account calls the
    provider (``ensure_folder_ref``) and persists the link (adoption).
    Provider-First: the provider call precedes the local link write.
    """
    try:
        link = folder_store.get_link(folder_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected folder link lookup error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to look up folder materialisation.") from exc
    if link is not None:
        return str(link["provider_ref"])

    try:
        provider_ref = manager.ensure_folder_ref(account_label, folder_name)
    except CoreError as exc:
        raise translate_core_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected ensure_folder_ref error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to materialise folder at the provider.") from exc

    try:
        folder_store.upsert_link(folder_id, account_id, provider_ref)
    except DatabaseError as exc:
        raise translate_database_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected folder link upsert error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to persist folder materialisation link.") from exc
    return provider_ref


def assign_folder_provider_first(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    provider_message_id: str,
    folder_id: str,
    folder_name: str,
    *,
    fallback: type[ApiError],
) -> None:
    """Assign a folder to a message Provider-First: materialise the folder, apply
    the label/category at the provider, and ONLY on success add the local member."""
    provider_ref = ensure_folder_materialized(
        manager, account_label, account_id, folder_id, folder_name, fallback=fallback,
    )
    try:
        manager.assign_folder_to_message(
            account_label, provider_message_id, provider_ref, folder_name,
        )
    except CoreError as exc:
        raise translate_core_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected assign_folder error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to assign folder at the provider.") from exc
    try:
        folder_store.add_member(provider_message_id, account_id, folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected folder member add error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to persist folder membership after the provider succeeded.") from exc


def unassign_folder_provider_first(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    provider_message_id: str,
    folder_id: str,
    folder_name: str,
    *,
    fallback: type[ApiError],
) -> None:
    """Remove a folder from a message Provider-First: remove the label/category at
    the provider (when the folder is materialised) then delete the local member."""
    try:
        link = folder_store.get_link(folder_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected folder link lookup error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to look up folder materialisation for unassign.") from exc

    if link is not None:
        try:
            manager.unassign_folder_from_message(
                account_label, provider_message_id, str(link["provider_ref"]), folder_name,
            )
        except CoreError as exc:
            raise translate_core_error(exc, fallback=fallback) from exc
        except Exception as exc:
            logger.warning("Unexpected unassign_folder error (%s): %s", type(exc).__name__, exc)
            raise fallback("Failed to remove folder at the provider.") from exc

    try:
        folder_store.remove_member(provider_message_id, account_id, folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc, fallback=fallback) from exc
    except Exception as exc:
        logger.warning("Unexpected folder member remove error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to remove folder membership after the provider succeeded.") from exc


def reconcile_folder_memberships(
    account_id: str,
    upserts: list[Any],
    label_updates: list[Any],
) -> None:
    """Reconcile ``email_folder_members`` for one account against the provider's
    labels/categories carried by this sync (decision 14). BEST-EFFORT — every
    failure is logged and swallowed so it can never abort the sync.

    Feeds from BOTH the full upserts (``provider_labels`` always a list) AND the
    incremental label updates (``provider_labels`` ``None`` = "do not touch").
    Short-circuits when the account has no materialised folder (the common case,
    and always during a new account's backfill).
    """
    try:
        ref_map = folder_store.get_ref_map(account_id)
        if not ref_map:
            return
        managed_folder_ids = list(set(ref_map.values()))

        items: list[tuple[str, list[str] | None]] = [
            (m.provider_message_id, m.provider_labels) for m in upserts
        ]
        items += [(lu.provider_message_id, lu.provider_labels) for lu in label_updates]

        seen: list[str] = []
        present: list[tuple[str, str]] = []
        for pmid, labels in items:
            if labels is None:
                continue  # "do not touch" (Outlook partial delta without categories)
            seen.append(pmid)
            for ref in labels:
                folder_id = ref_map.get(ref)
                if folder_id is not None:
                    present.append((pmid, folder_id))
        if not seen:
            return
        folder_store.reconcile_memberships(account_id, present, seen, managed_folder_ids)
    except Exception as exc:
        logger.warning(
            "Folder membership reconciliation failed for account %s (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )


def fetch_folder_chips(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[FolderRef]]:
    """Return ``{(provider_message_id, account_id): [FolderRef, ...]}`` for a page
    of messages, in one round trip. BEST-EFFORT — returns ``{}`` on any failure
    (chips are a non-essential enrichment; the listing must still render)."""
    if not pairs:
        return {}
    try:
        rows = folder_store.list_folders_for_messages(pairs)
    except Exception as exc:
        logger.warning("Folder chips fetch failed (%s): %s", type(exc).__name__, exc, exc_info=exc)
        return {}
    result: dict[tuple[str, str], list[FolderRef]] = {}
    for row in rows:
        key = (str(row["provider_message_id"]), str(row["account_id"]))
        result.setdefault(key, []).append(
            FolderRef(folder_id=str(row["folder_id"]), name=row["name"], color=row.get("color")),
        )
    return result


def enrich_items_with_folders(items: list[Any]) -> None:
    """Populate ``item.folders`` on each ``EmailMetadataOut`` of a page in place.

    A single batch query for the whole page (§4.6). BEST-EFFORT via
    ``fetch_folder_chips`` — on failure the items keep their default empty
    ``folders`` and the listing still renders.
    """
    if not items:
        return
    pairs = [(item.provider_message_id, item.account_id) for item in items]
    chips = fetch_folder_chips(pairs)
    if not chips:
        return
    for item in items:
        item.folders = chips.get((item.provider_message_id, item.account_id), [])
