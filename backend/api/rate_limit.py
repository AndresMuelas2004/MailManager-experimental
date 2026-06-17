"""
In-process fixed-window rate limiter.

The engine keeps request counters in memory (one ``TTLCache`` per window
length) and applies a fixed-window algorithm. It is framework-agnostic and
pure (no FastAPI imports), so it can be unit-tested with an injected ``now``.

State lives in the process memory: it is enough for the single-worker MVP and
is lost on restart (accepted). If the deployment ever scales to several
workers, the migration boundary is this module — swap ``_caches`` / ``check``
for a shared backend (e.g. Redis) and the dependency wiring stays untouched.
"""

from __future__ import annotations

import os
import threading
import time

from cachetools import TTLCache

_TRUE_VALUES = {"1", "true", "yes", "on"}
_RATE_LIMIT_ENABLED_ENV_VAR = "RATE_LIMIT_ENABLED"

# Balanced profile. Each bucket maps to a tuple of windows ``(limit, seconds)``.
# This is the SINGLE source of truth for the figures; the tests monkeypatch it.
RATE_LIMITS: dict[str, tuple[tuple[int, int], ...]] = {
    "auth_login": ((10, 60),),
    "email_send": ((20, 60), (200, 3600)),
    "provider_sync": ((30, 60),),
    "global": ((300, 60),),
}

# Upper bound on live keys per window length (caps memory under a dispersed
# flood). When exceeded, ``TTLCache`` evicts the oldest entries (LRU); the
# worst case is resetting the counter of a very spread-out attacker.
_MAX_TRACKED_KEYS = 100_000

_lock = threading.Lock()
# window_seconds -> TTLCache(key -> count). One cache per window length because
# ``TTLCache`` has a single ttl; the ttl equals the window length, so stale keys
# expire on their own once the window rolls over.
_caches: dict[int, TTLCache] = {}


def rate_limiting_enabled() -> bool:
    """True only when ``RATE_LIMIT_ENABLED`` is explicitly truthy (opt-in)."""
    return os.getenv(_RATE_LIMIT_ENABLED_ENV_VAR, "").strip().lower() in _TRUE_VALUES


def _cache_for(window_seconds: int) -> TTLCache:
    cache = _caches.get(window_seconds)
    if cache is None:
        cache = TTLCache(maxsize=_MAX_TRACKED_KEYS, ttl=window_seconds)
        _caches[window_seconds] = cache
    return cache


def reset() -> None:
    """Drop every counter. Used by the tests for isolation."""
    with _lock:
        _caches.clear()


def check(bucket: str, identity: str, *, now: float | None = None) -> int | None:
    """Count one request of ``(bucket, identity)`` across ALL its windows.

    Returns ``None`` when the request is allowed, or the seconds to wait
    (``Retry-After``) when any window has been exceeded. ``now`` is injectable
    for deterministic tests. The request is blocked if ANY window is over the
    limit; ``retry_after`` is the wait of the most restrictive exceeded window.

    Each window's counter is incremented BEFORE its over-limit check, so a
    request blocked by one window still consumes a slot in the others (an
    accepted fixed-window trade-off). Do NOT "fix" this into a
    pre-check-then-increment pass: reads and writes happen under ``_lock`` as a
    single critical section, and splitting them would open a TOCTOU race.
    """
    windows = RATE_LIMITS.get(bucket)
    if not windows:
        return None  # Unknown bucket = no limit (defensive no-op).
    if now is None:
        now = time.time()

    blocked = False
    retry_after = 0
    with _lock:
        for limit, window_seconds in windows:
            cache = _cache_for(window_seconds)
            window_index = int(now // window_seconds)
            key = f"{bucket}:{identity}:{window_seconds}:{window_index}"
            count = cache.get(key, 0) + 1
            cache[key] = count
            if count > limit:
                blocked = True
                seconds_to_reset = (window_index + 1) * window_seconds - now
                retry_after = max(retry_after, int(seconds_to_reset) + 1)
    return retry_after if blocked else None


def client_ip(forwarded_for: str | None, client_host: str | None) -> str:
    """Resolve the client IP: the first hop of ``X-Forwarded-For`` (trustworthy
    behind Caddy, which strips any inbound value), falling back to the direct
    peer's IP (dev / tests) and finally ``"unknown"``. A present-but-empty or
    whitespace-only first hop (malformed proxy header) also falls back instead
    of becoming an empty-string identity that would collapse those clients into
    one bucket."""
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    return client_host or "unknown"
