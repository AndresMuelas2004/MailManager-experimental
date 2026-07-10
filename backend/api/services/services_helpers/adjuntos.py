"""Adjuntos: recalculo y persistencia del flag denormalizado ``has_attachments`` (D-09)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from database import email_metadata_store, DatabaseError

from .traduccion_errores import translate_database_error


def recompute_has_attachments(
    account_id: str,
    provider_message_id: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """Recalculate and persist ``email_metadata.has_attachments`` (D-09).

    The single call site is the cache-miss branch of
    ``get_email_content`` — after upserting the new ``email_attachments``
    rows, this helper rewrites the denormalised flag so the inbox
    listing reflects the discovery without an extra round trip. The
    underlying SQL is idempotent (it derives the flag from the live
    count of non-inline rows) so calling twice in a row is harmless.
    """
    try:
        email_metadata_store.update_has_attachments(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected has_attachments recompute error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback(
            "Failed to recompute has_attachments after attachment upsert."
        ) from exc
