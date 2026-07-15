"""Transporte HTTP de Microsoft Graph: peticiones con reintentos, cabecera ImmutableId y parseo comun."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..errors import EmailExternalAPIError

logger = logging.getLogger(__name__)


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


# All attachment-touching calls send this header per request — see
# repository_guide.md and adjuntos-outlook.md § 6.1. Without it, Graph
# may interpret the path id as a mutable folder-scoped id and our
# stored ImmutableId lookups fail.
_PREFER_IMMUTABLE_HEADERS: dict[str, str] = {"Prefer": 'IdType="ImmutableId"'}


_OUTLOOK_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)


# Transient Graph statuses worth retrying. Kept identical to the literal
# already classified inline by the attachment loops (and to Gmail's
# ``_RETRYABLE_STATUS_CODES``) so both providers share one transient set.
# ``509`` / ``409`` are deliberately excluded in MVP — see
# docs/limits/favoritos.md.
_OUTLOOK_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# Deterministic fallback for empty/malformed Graph timestamps. It MUST be a
# constant — never ``now()``: the conversation lazy-sync reconciles Outlook's
# per-endpoint id drift through the endpoint-independent identity
# ``(received_at, from_email, subject)``, so a non-reproducible fallback gives
# the same physical message a DIFFERENT identity on every read, the remap
# never matches, and the duplicate-row factory resurrects for any message
# without a parseable date.
_GRAPH_DATETIME_FALLBACK = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_graph_datetime(raw: Any) -> datetime:
    """Parse a Graph ISO-8601 timestamp (``…Z``) into an aware ``datetime``.

    Falls back to the deterministic ``_GRAPH_DATETIME_FALLBACK`` (epoch, UTC)
    when the value is empty or malformed, so a single bad timestamp never
    aborts a thread/sync parse AND the same payload always parses to the same
    instant (load-bearing for the conversation id reconciliation — see the
    constant's comment).
    """
    if not raw:
        return _GRAPH_DATETIME_FALLBACK
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return _GRAPH_DATETIME_FALLBACK


def _retry_after_seconds(headers: dict[str, str] | None) -> float | None:
    """Extract ``Retry-After`` (seconds) from a Graph response header dict.

    Both 429 and (occasionally) 503 carry this header. Outlook on Graph
    confirms it is the only way to know how long to wait —
    ``Rate-Limit-*`` headers are absent on Graph (see
    ``adjuntos-outlook.md`` § 5.4).
    """
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class OutlookTransporteMixin:
    """Peticiones HTTP a Graph de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def _graph_request_json_with_retries(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        operation: str,
    ) -> dict[str, Any]:
        """Run one Graph request with ``Retry-After``-aware retries.

        Drives :py:meth:`_graph_request_raw` through the same manual
        backoff loop as :py:meth:`fetch_attachment_binary`: transient
        statuses (``_OUTLOOK_RETRYABLE_STATUS_CODES`` plus the synthetic
        ``503`` ``_graph_request_raw`` folds ``URLError`` into) back off
        and retry, honouring ``Retry-After`` when present and falling
        back to ``_OUTLOOK_RETRY_DELAYS_SECONDS`` (3 attempts total).
        Any other status propagates immediately as
        :py:class:`EmailExternalAPIError` without consuming a retry —
        permanent errors (``400``/``401``/``403``/``404``/``409``/``422``)
        never retry. ``_PREFER_IMMUTABLE_HEADERS`` is re-sent on every
        attempt (and, for paginated callers, every page) because Graph
        does not remember the preference across calls.

        Used by the favourite PATCH (``set_favorite``) and each page of
        the favourite listing (``list_favorite_ids``). ``self._sleep`` is
        the injection point that keeps the retry waits instant in tests.
        """
        for attempt, delay in enumerate(_OUTLOOK_RETRY_DELAYS_SECONDS, start=1):
            status, headers, payload = self._graph_request_raw(
                method, url, body=body, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            if 200 <= status < 300:
                if status == 204 or not payload:
                    return {}
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                    raise EmailExternalAPIError(
                        f"Outlook {operation} returned an unparseable response "
                        f"({type(exc).__name__})."
                    ) from exc
                return parsed if isinstance(parsed, dict) else {}
            if status not in _OUTLOOK_RETRYABLE_STATUS_CODES:
                # Permanent failure — fail immediately without retrying.
                raise EmailExternalAPIError(
                    f"Outlook {operation} failed (HTTP {status})."
                )
            # Retryable status: back off and retry, unless this was the
            # last attempt — then fall through to the post-loop raise.
            if attempt < len(_OUTLOOK_RETRY_DELAYS_SECONDS):
                retry_after = _retry_after_seconds(headers)
                self._sleep(retry_after if retry_after is not None else delay)
        raise EmailExternalAPIError(
            f"Outlook {operation} exhausted retries on transient errors."
        )

    def _graph_request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated JSON request to Microsoft Graph API."""
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 204:
                    return {}
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = error_body
            try:
                error_json = json.loads(error_body) if error_body else {}
                if isinstance(error_json, dict):
                    err_code = error_json.get("error", {})
                    if isinstance(err_code, dict):
                        detail = f"{err_code.get('code', 'error')}: {err_code.get('message', '')}"
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            raise EmailExternalAPIError(f"Outlook failed Graph API call: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmailExternalAPIError(f"Outlook failed to reach Graph API: {exc.reason}") from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook failed Graph API request ({type(exc).__name__}): {exc}"
            ) from exc

    def _graph_request_raw(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Authenticated Graph request returning raw status + headers + bytes.

        Used by ``fetch_attachment_binary`` to read ``/$value`` (binary
        body, not JSON) and by the favourite retry loops (``set_favorite``
        PATCH / ``list_favorite_ids`` GET) which need the response headers
        to honour ``Retry-After``. Errors do not raise from this method —
        the caller drives retry / classification per status.

        When ``body`` is provided it is JSON-serialised and sent with a
        ``Content-Type: application/json`` header (the favourite PATCH);
        ``body=None`` keeps the bodyless GET behaviour unchanged.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return (
                    int(response.status),
                    dict(response.headers),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return (int(exc.code), dict(exc.headers or {}), exc.read())
        except urllib.error.URLError as exc:
            # Convert connection-level failures (DNS, timeout, refused) to a
            # synthetic 503 so the caller's retry loop treats them the same
            # as a transient provider 5xx (D-16). Without this, the retry
            # loop in ``fetch_attachment_binary`` only matches by status
            # code and a network failure would skip retries entirely.
            logger.warning(
                "Outlook Graph raw request network error (treated as 503): %s",
                exc.reason,
            )
            return (503, {}, b"")
