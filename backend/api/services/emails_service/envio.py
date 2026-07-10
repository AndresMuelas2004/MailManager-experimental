"""Envio de correos (Provider-First): sanea el HTML, envia por el proveedor y persiste metadatos."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailSendError,
)
from core.email import CoreError
from api.schemas.email import EmailSendRequest
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    persist_email_metadata_batch,
    raise_on_silent_auth_errors,
    sanitize_outbound_html,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


def send_email(mailbox_id: str, payload: EmailSendRequest, user_id: str) -> dict[str, str]:
    ensure_mailbox_access(mailbox_id, user_id)
    # Sanitise the rich-text HTML body at the trust boundary before it
    # reaches the provider (Gmail multipart/alternative, Outlook HTML).
    # Fail-soft (never raises). NOTE: ``sanitize_outbound_html`` (outbound,
    # strict allowlist) is distinct from ``sanitize_email_html`` (the
    # inbound viewer pipeline) — they are NOT interchangeable.
    sanitized_body = sanitize_outbound_html(payload.body)
    try:
        account = account_store.get(mailbox_id, payload.account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error (%s): %s", type(exc).__name__, exc)
        raise EmailSendError("Failed to look up account for email send.") from exc
    if account is None:
        raise AccountNotFound(f"Account '{payload.account_id}' not found.")

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{payload.account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailSendError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailSendError)

        try:
            sent_metadata = manager.send_email_from_account(
                account_label=account_label,
                subject=payload.subject,
                body=sanitized_body,
                recipients=payload.recipients,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=EmailSendError,
                context={"account_id": payload.account_id, "account_label": account_label},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider send_email_from_account (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailSendError(
                "Unexpected provider failure while sending email from account."
            ) from exc

        # Best-effort: email already sent, don't fail the response on metadata persist failure
        try:
            persist_email_metadata_batch(payload.account_id, [sent_metadata], fallback=EmailSendError)
        except Exception as exc:
            logger.warning(
                "Email sent but metadata persistence failed for account '%s' (%s): %s",
                payload.account_id, type(exc).__name__, exc,
            )

        return {"status": "sent"}
    except ApiError:
        raise
    except Exception as exc:
        logger.warning("Unexpected send error (%s): %s", type(exc).__name__, exc)
        raise EmailSendError("Failed to send email.") from exc
