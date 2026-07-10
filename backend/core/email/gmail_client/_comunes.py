"""Privados transversales del cliente Gmail: reintentos, lotes y parseo de direcciones."""

from __future__ import annotations

import logging
import os
from email.utils import getaddresses
from typing import Any

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


_BATCH_SIZE = 100
_BATCH_MAX_RETRIES = 4
_BATCH_RETRY_DELAY = 1.0  # seconds


def _parse_max_workers() -> int:
    raw = os.environ.get("GMAIL_BATCH_MAX_WORKERS", "5")
    try:
        return max(int(raw), 1)
    except (ValueError, TypeError):
        logger.warning("Invalid GMAIL_BATCH_MAX_WORKERS=%r, defaulting to 5", raw)
        return 5

_PARALLEL_MAX_WORKERS = _parse_max_workers()


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exception: Any) -> bool:
    """Classify whether a batch callback exception is worth retrying.

    Returns True for rate-limit (429), server errors (5xx), and non-HTTP
    exceptions (network timeouts, connection resets).  Returns False for
    client errors like 404, 400, 403, 410 — those are permanent failures.
    """
    if isinstance(exception, HttpError):
        status = getattr(getattr(exception, "resp", None), "status", None)
        return status in _RETRYABLE_STATUS_CODES
    return True


def _split_address_header(header_value: str) -> list[str]:
    """Decompose an RFC 5322 ``To`` / ``Cc`` / ``Reply-To`` header into
    a list of plain email addresses (no display name, no angle brackets).

    Uses :py:func:`email.utils.getaddresses` to tolerate display names,
    quoted local parts, multiple addresses separated by commas, and
    rare RFC 5322 § 3.4 group syntax. Empty / whitespace strings
    return ``[]``. Addresses without an ``@`` are dropped silently —
    they are usually leftover ``"Undisclosed recipients:;"`` group
    headers or malformed senders that would break downstream.
    """
    raw = (header_value or "").strip()
    if not raw:
        return []
    pairs = getaddresses([raw])
    out: list[str] = []
    for _name, addr in pairs:
        cleaned = (addr or "").strip()
        if not cleaned or "@" not in cleaned:
            continue
        out.append(cleaned)
    return out


def _first_recipient_from_to_header(header_value: str) -> tuple[str, str]:
    """Extract ``(name, email)`` of the first valid recipient in a ``To`` header.

    Same RFC 5322 parser as :py:func:`_split_address_header` but
    preserves the display name for the leading entry — used to populate
    ``EmailMetadata.to_email`` / ``to_name`` during sync. Returns
    ``("", "")`` when the header is empty or carries no address with
    an ``@`` (rare; service-side notifications mass-mailed via Bcc).
    """
    raw = (header_value or "").strip()
    if not raw:
        return "", ""
    for name, addr in getaddresses([raw]):
        cleaned_addr = (addr or "").strip()
        if not cleaned_addr or "@" not in cleaned_addr:
            continue
        return (name or "").strip(), cleaned_addr
    return "", ""
