"""Politica de reintentos con backoff exponencial (D-16) y extraccion de detalle de errores HTTP."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def http_error_detail(exc: Any) -> tuple[str, str]:
    """Extract (status, reason) from a googleapiclient HttpError."""
    status = getattr(getattr(exc, "resp", None), "status", "unknown")
    reason = getattr(exc, "reason", "unknown")
    return status, reason


# ---------------------------------------------------------------------------
# Retry helper (D-16)
# ---------------------------------------------------------------------------


_DEFAULT_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS_SECONDS,
    is_retryable: Callable[[Exception], bool] | None = None,
    retry_after_extractor: Callable[[Exception], float | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``fn`` with up to ``attempts`` total tries on retryable errors.

    Default delays are 1s, 2s, 4s — fixed exponential backoff (D-16). If
    the caller passes ``retry_after_extractor`` and it returns a
    positive float for the failing exception, that value overrides the
    default delay (honours ``Retry-After`` from the provider).

    ``is_retryable(exc)`` decides which exceptions trigger a retry.
    Non-retryable exceptions propagate immediately. The default predicate
    retries any ``OSError`` / ``IOError`` (network noise) — provider-
    specific retry policy belongs in the caller.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if is_retryable is None:
        is_retryable = lambda exc: isinstance(exc, OSError)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = delays[min(attempt, len(delays) - 1)] if delays else 0.0
            if retry_after_extractor is not None:
                hint = retry_after_extractor(exc)
                if hint is not None and hint > 0:
                    delay = hint
            sleep(delay)
    assert last_exc is not None
    raise last_exc
