"""Contenido de correos: lectura/persistencia de cuerpos cacheados, TTL deslizante y seleccion de prefetch."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from api.services.email_html_pipeline import prepare_email_html, rewrite_remote_images
from api.services.image_proxy_signing import build_proxy_sentinel_url
from database import (
    email_content_store,
    email_metadata_store,
    DatabaseError,
)

from .traduccion_errores import translate_database_error


def sanitize_email_html(html: str) -> str:
    """Sanitise inbound email HTML, then rewrite remote images to the proxy.

    Composes the pure 7-step rendering pipeline (``prepare_email_html``) with
    the post-pipeline remote-image rewrite, so every cached body carries signed
    proxy sentinel URLs instead of raw remote image URLs (privacy — the cached
    HTML never holds a raw remote URL). ``prepare_email_html`` itself stays
    pure and untouched; the rewrite runs on its output. Re-exported from
    ``services_helpers`` under this name so existing callers do not change.
    """
    return rewrite_remote_images(prepare_email_html(html), build_proxy_sentinel_url)


def get_email_content(
    account_id: str,
    provider_message_id: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> dict[str, Any] | None:
    """Read cached email content from DB. Returns dict or None."""
    try:
        return email_content_store.get(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected email content read error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to read email content from database.") from exc


def persist_email_content(
    account_id: str,
    provider_message_id: str,
    html_body: str | None,
    text_body: str | None,
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """Persist email content to DB. CAN raise — caller decides best-effort wrapping."""
    try:
        email_content_store.upsert(account_id, provider_message_id, html_body, text_body)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected email content persist error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to persist email content.") from exc


def touch_email_content_last_accessed(
    account_id: str, provider_message_id: str,
) -> None:
    """Refresh the cached body's ``last_accessed_at`` (sliding TTL).

    Best-effort by design: a failure to bump the TTL must NEVER affect the
    content response — the body is already served from cache. Swallows and
    logs every error (does not re-raise). Called on a cache HIT.
    """
    try:
        email_content_store.touch_last_accessed(account_id, provider_message_id)
    except Exception as exc:
        logger.warning(
            "Failed to touch email content last_accessed (%s): %s",
            type(exc).__name__, exc,
            exc_info=exc,
        )


def purge_expired_email_content(account_ids: list[str]) -> int:
    """Evict cached bodies idle for 7+ days for the given accounts (sliding TTL).

    Best-effort: runs in the post-sync background task and must never raise.
    Returns the number of rows purged, or 0 on any error.
    """
    try:
        return email_content_store.purge_expired_for_accounts(account_ids)
    except Exception as exc:
        logger.warning(
            "Failed to purge expired email content (%s): %s",
            type(exc).__name__, exc,
            exc_info=exc,
        )
        return 0


def list_unread_recent_uncached(
    account_id: str,
    limit: int,
    *,
    fallback: type[ApiError] = ApiError,
) -> list[str]:
    """List the content-prefetch targets for one account (CAN raise).

    Thin translation wrapper over
    ``email_metadata_store.list_unread_recent_uncached``. The prefetch
    caller wraps this in its own best-effort try/except, so this follows
    the standard translation pattern rather than swallowing.
    """
    try:
        return email_metadata_store.list_unread_recent_uncached(account_id, limit)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected unread recent uncached listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback("Failed to list unread recent uncached messages.") from exc
