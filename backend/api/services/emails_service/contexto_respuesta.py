"""Contexto de respuesta/reenvio para el compositor (Reply / Reply All / Forward), solo lectura."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailNotFound,
    EmailReplyContextError,
)
from core.email import (
    CoreError,
    build_in_reply_to_and_references,
    build_quoted_body_html,
    build_reply_subject,
    compute_reply_recipients,
    validate_reply_threading_coherence,
)
from api.schemas.email import ReplyContextOut
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    email_metadata_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


def get_reply_context(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    action: str,
    user_id: str,
) -> ReplyContextOut:
    """Build the data the composer needs to open Reply / Reply All / Forward.

    Single Provider-call read (no DB mutations). Follows the standard
    cascade: ``ensure_mailbox_access`` → account lookup → metadata
    existence pre-check → silent auth → ``manager.fetch_reply_context``
    → recipient / subject / quoted-body computation in pure helpers
    → coherence guard for Gmail-bound replies → assemble
    :py:class:`ReplyContextOut`.

    The local-existence pre-check via ``email_metadata_store.exists``
    matches the favourites toggle pattern: a missing row collapses
    to 404 without spending a provider round trip.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if action not in ("reply", "reply_all", "forward"):
        raise EmailReplyContextError(
            f"Invalid reply action '{action}' while preparing reply context "
            f"for message '{provider_message_id}'.",
            detail={"action": action},
        )

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during reply context fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to look up account while preparing reply context."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during reply context fetch."
        )

    try:
        metadata_exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata existence check error during reply context fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to verify email existence for reply context fetch."
        ) from exc
    if not metadata_exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            f"in mailbox '{mailbox_id}' during reply context fetch."
        )

    provider = str(account.get("provider") or "").lower()
    current_email = (account.get("email_address") or "").strip() or None

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailReplyContextError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailReplyContextError)

        try:
            reply_context = manager.fetch_reply_context(account_label, provider_message_id)
        except CoreError as exc:
            raise translate_core_error(exc, fallback=EmailReplyContextError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider fetch_reply_context (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailReplyContextError(
                "Unexpected provider failure while fetching reply context."
            ) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected reply context fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to prepare reply context for the composer."
        ) from exc

    to_recipients, cc_recipients = compute_reply_recipients(
        original_from=reply_context.from_email,
        original_reply_to=reply_context.reply_to,
        original_to=reply_context.to_recipients,
        original_cc=reply_context.cc_recipients,
        current_account_email=current_email,
        action=action,
        original_box=reply_context.box,
    )

    new_subject = build_reply_subject(reply_context.subject, action)
    in_reply_to, references = build_in_reply_to_and_references(
        reply_context.message_id, reply_context.references,
    )
    quoted_body = build_quoted_body_html(
        reply_context.body_html,
        reply_context.body_text,
        from_name=reply_context.from_name,
        from_email=reply_context.from_email,
        received_at=reply_context.received_at,
        action=action,
        to_recipients=reply_context.to_recipients,
        cc_recipients=reply_context.cc_recipients,
        subject=reply_context.subject,
    )

    # Gmail-bound replies must satisfy the triple-requirement guard
    # (threadId + In-Reply-To/References + matching Subject). For
    # Forward we skip the subject check because the prefix changes
    # ("Fwd:" vs original) and Gmail does not require subject match
    # for forwards (the user reaches new recipients with a new id).
    if provider == "gmail" and action in ("reply", "reply_all") and reply_context.thread_id:
        try:
            validate_reply_threading_coherence(
                thread_id=reply_context.thread_id,
                in_reply_to=in_reply_to,
                references=references,
                original_message_id=reply_context.message_id,
                original_thread_id=reply_context.thread_id,
                original_subject=reply_context.subject,
                new_subject=new_subject,
            )
        except CoreError as exc:
            raise translate_core_error(exc, fallback=EmailReplyContextError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during reply threading coherence check (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailReplyContextError(
                "Unexpected failure during reply threading coherence check."
            ) from exc

    return ReplyContextOut(
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        bcc_recipients=[],
        subject=new_subject,
        body=quoted_body,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=reply_context.thread_id,
        reply_to_message_id=reply_context.provider_message_id,
        reply_kind=action,  # type: ignore[arg-type]
        original_from_email=reply_context.from_email,
    )
