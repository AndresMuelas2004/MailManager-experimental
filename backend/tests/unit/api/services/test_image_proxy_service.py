"""
Unit tests for ``image_proxy_service`` — the cache-aside serve + admin purge,
mocked at the store / fetcher boundary so no DB or network runs.

The three concrete ``core.image_proxy`` fetcher errors are mapped BY HAND in the
service (not through ``translate_core_error``), so each mapping is pinned here.
"""

from __future__ import annotations

import hashlib
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from api.errors.exceptions import (
    ApiError,
    DatabaseConnectionError,
    DatabaseQueryError,
    ImageProxyBlockedTarget,
    ImageProxyForbidden,
    ImageProxyUpstreamError,
    InvalidAdminToken,
    PurgeDisabled,
)
from api.services import image_proxy_service
from api.services.image_proxy_signing import build_proxy_sentinel_url
from core.image_proxy import (
    FetchedImage,
    ImageProxyBlocked,
    ImageProxyNotAnImage,
    ImageProxyUnfetchable,
)
from database.errors import ConnectionPoolError, QueryError as DbQueryError


_URL = "https://cdn.example.com/logo.png"
_URL_HASH = hashlib.sha256(_URL.encode("utf-8")).hexdigest()

_PURGE_ENV = "IMAGE_PROXY_PURGE_TOKEN"


@pytest.fixture(autouse=True)
def _reset_touch_throttle():
    """Isolate the in-memory touch throttle between tests: it is module-level
    state keyed by url_hash, so a touch in one test would otherwise suppress a
    touch of the same hash in the next (making order-dependent failures)."""
    image_proxy_service.reset_touch_throttle()
    yield
    image_proxy_service.reset_touch_throttle()


def _valid_us(url: str = _URL) -> tuple[str, str]:
    """Mint a genuine ``(u, s)`` pair via the production signer."""
    query = parse_qs(urlsplit(build_proxy_sentinel_url(url)).query)
    return query["u"][0], query["s"][0]


def _forbid_fetch(monkeypatch):
    def _explode(_url):
        raise AssertionError("fetch_remote_image must not be called")

    monkeypatch.setattr(image_proxy_service, "fetch_remote_image", _explode)


# ── get_proxied_image — signature gate ─────────────────────────────


class TestSignatureGate:

    def test_malformed_payload_raises_forbidden(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get",
            lambda _h: (_ for _ in ()).throw(AssertionError("cache must not be read")),
        )
        with pytest.raises(ImageProxyForbidden):
            image_proxy_service.get_proxied_image("not*base64", "deadbeef")

    def test_tampered_signature_raises_forbidden(self, monkeypatch):
        u, s = _valid_us()
        tampered = s[:-1] + ("0" if s[-1] != "0" else "1")
        with pytest.raises(ImageProxyForbidden):
            image_proxy_service.get_proxied_image(u, tampered)


# ── get_proxied_image — cache-aside ────────────────────────────────


class TestCacheAside:

    def test_cache_hit_serves_without_fetching(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get",
            lambda _h: {"content_type": "image/png", "image_bytes": b"CACHED"},
        )
        _forbid_fetch(monkeypatch)

        u, s = _valid_us()
        content_type, chunks, url_hash = image_proxy_service.get_proxied_image(u, s)
        assert content_type == "image/png"
        assert b"".join(chunks) == b"CACHED"
        assert url_hash == _URL_HASH

    def test_cache_miss_fetches_and_persists(self, monkeypatch):
        upserts: list[tuple] = []
        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", lambda _h: None)
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "upsert",
            lambda *args: upserts.append(args),
        )
        monkeypatch.setattr(
            image_proxy_service, "fetch_remote_image",
            lambda url: FetchedImage(content_type="image/gif", data=b"GIF89a"),
        )

        u, s = _valid_us()
        content_type, chunks, _hash = image_proxy_service.get_proxied_image(u, s)
        assert content_type == "image/gif"
        assert b"".join(chunks) == b"GIF89a"
        # The freshly-fetched image is persisted under the URL hash.
        assert upserts == [(_URL_HASH, _URL, "image/gif", b"GIF89a")]

    def test_persist_failure_still_serves_the_image(self, monkeypatch):
        """Persist is best-effort: a cache-write failure must not fail the serve."""
        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", lambda _h: None)
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "upsert",
            lambda *_a: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        monkeypatch.setattr(
            image_proxy_service, "fetch_remote_image",
            lambda url: FetchedImage(content_type="image/png", data=b"PNG"),
        )

        u, s = _valid_us()
        content_type, chunks, _hash = image_proxy_service.get_proxied_image(u, s)
        assert content_type == "image/png"
        assert b"".join(chunks) == b"PNG"


# ── get_proxied_image — fetcher error mapping (by hand) ─────────────


class TestFetcherErrorMapping:

    @pytest.mark.parametrize(
        "core_exc, api_exc",
        [
            (ImageProxyBlocked("blocked"), ImageProxyBlockedTarget),
            (ImageProxyNotAnImage("not image"), ImageProxyUpstreamError),
            (ImageProxyUnfetchable("upstream down"), ImageProxyUpstreamError),
            (RuntimeError("unexpected"), ImageProxyUpstreamError),
        ],
    )
    def test_each_fetcher_error_maps_to_the_right_api_error(self, monkeypatch, core_exc, api_exc):
        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", lambda _h: None)
        monkeypatch.setattr(
            image_proxy_service, "fetch_remote_image",
            lambda _url: (_ for _ in ()).throw(core_exc),
        )
        u, s = _valid_us()
        with pytest.raises(api_exc):
            image_proxy_service.get_proxied_image(u, s)


class TestCacheReadError:

    def test_database_error_is_translated(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get",
            lambda _h: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        u, s = _valid_us()
        with pytest.raises(DatabaseQueryError):
            image_proxy_service.get_proxied_image(u, s)

    def test_unexpected_read_error_is_upstream_error(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get",
            lambda _h: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        u, s = _valid_us()
        with pytest.raises(ImageProxyUpstreamError):
            image_proxy_service.get_proxied_image(u, s)


# ── serve gate (pool protection for image bursts) ───────────────────


class TestServeGate:

    def test_serve_gate_is_a_bounded_semaphore_sized_to_the_constant(self):
        # The gate MUST be a BoundedSemaphore sized to _MAX_CONCURRENT_SERVES so
        # a cold-open burst of dozens of images WAITS for a slot instead of each
        # handler grabbing a pool connection at once — the reproduced failure
        # mode was getconn() raising instantly at pool exhaustion, turning a
        # random subset of a 43-image newsletter into 503s. A regression to a
        # plain int / wrong size silently reopens it.
        assert isinstance(image_proxy_service._SERVE_GATE, threading.BoundedSemaphore)
        assert image_proxy_service._SERVE_GATE._value == image_proxy_service._MAX_CONCURRENT_SERVES

    def test_serve_releases_the_gate_on_success(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get",
            lambda _h: {"content_type": "image/png", "image_bytes": b"CACHED"},
        )
        _forbid_fetch(monkeypatch)
        before = image_proxy_service._SERVE_GATE._value
        u, s = _valid_us()
        image_proxy_service.get_proxied_image(u, s)
        assert image_proxy_service._SERVE_GATE._value == before

    def test_serve_releases_the_gate_on_error(self, monkeypatch):
        # The permit must be released when the serve fails, or repeated errors
        # would drain the gate and stall every future image serve.
        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", lambda _h: None)
        monkeypatch.setattr(
            image_proxy_service, "fetch_remote_image",
            lambda _url: (_ for _ in ()).throw(ImageProxyUnfetchable("down")),
        )
        before = image_proxy_service._SERVE_GATE._value
        u, s = _valid_us()
        with pytest.raises(ImageProxyUpstreamError):
            image_proxy_service.get_proxied_image(u, s)
        assert image_proxy_service._SERVE_GATE._value == before

    def test_invalid_signature_never_takes_a_gate_slot(self, monkeypatch):
        # The gate is acquired AFTER the signature check: a 403 must not spend a
        # serve slot (an attacker with garbage signatures could otherwise queue
        # behind legitimate serves).
        acquired: list[bool] = []

        class _SpyGate:
            def __enter__(self):
                acquired.append(True)

            def __exit__(self, *_a):
                return False

        monkeypatch.setattr(image_proxy_service, "_SERVE_GATE", _SpyGate())
        with pytest.raises(ImageProxyForbidden):
            image_proxy_service.get_proxied_image("not*base64", "deadbeef")
        assert acquired == []


# ── pool-exhaustion retry (wait, don't 503) ─────────────────────────


@pytest.fixture()
def _instant_retries(monkeypatch):
    """Zero out the retry sleeps so exhaustion tests stay fast and deterministic."""
    monkeypatch.setattr(image_proxy_service, "_POOL_RETRY_DELAYS_S", (0, 0))


class TestPoolExhaustionRetry:

    def test_cache_read_pool_exhaustion_is_retried_until_it_succeeds(
        self, monkeypatch, _instant_retries
    ):
        # A transient pool-exhaustion (sync fan-out / prefetch / backfill holding
        # the pool at open time) must be absorbed by the brief retry instead of
        # surfacing as an instant 503 (a grey image in the viewer).
        calls: list[int] = []

        def _flaky_get(_h):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionPoolError("connection pool exhausted")
            return {"content_type": "image/png", "image_bytes": b"CACHED"}

        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", _flaky_get)
        _forbid_fetch(monkeypatch)
        u, s = _valid_us()
        content_type, chunks, _hash = image_proxy_service.get_proxied_image(u, s)
        assert content_type == "image/png"
        assert b"".join(chunks) == b"CACHED"
        assert len(calls) == 3

    def test_cache_read_exhaustion_beyond_the_budget_translates_to_503(
        self, monkeypatch, _instant_retries
    ):
        # Sustained exhaustion still fails — the retry is a brief absorber, not
        # an unbounded wait — and keeps the existing DatabaseConnectionError
        # (503) translation.
        calls: list[int] = []

        def _always_exhausted(_h):
            calls.append(1)
            raise ConnectionPoolError("connection pool exhausted")

        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "get", _always_exhausted,
        )
        u, s = _valid_us()
        with pytest.raises(DatabaseConnectionError):
            image_proxy_service.get_proxied_image(u, s)
        assert len(calls) == image_proxy_service._POOL_RETRY_ATTEMPTS

    def test_non_pool_database_error_is_not_retried(self, monkeypatch, _instant_retries):
        # Only pool exhaustion is transient-by-nature; a QueryError must
        # propagate immediately (single call) through the existing translation.
        calls: list[int] = []

        def _query_error(_h):
            calls.append(1)
            raise DbQueryError("db down")

        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", _query_error)
        u, s = _valid_us()
        with pytest.raises(DatabaseQueryError):
            image_proxy_service.get_proxied_image(u, s)
        assert len(calls) == 1

    def test_persist_pool_exhaustion_is_retried_then_swallowed(
        self, monkeypatch, _instant_retries
    ):
        # The upsert keeps its best-effort contract: sustained exhaustion there
        # burns the retry budget and is then swallowed — the image still serves.
        upsert_calls: list[int] = []

        def _always_exhausted(*_a):
            upsert_calls.append(1)
            raise ConnectionPoolError("connection pool exhausted")

        monkeypatch.setattr(image_proxy_service.image_proxy_cache_store, "get", lambda _h: None)
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "upsert", _always_exhausted,
        )
        monkeypatch.setattr(
            image_proxy_service, "fetch_remote_image",
            lambda _url: FetchedImage(content_type="image/png", data=b"PNG"),
        )
        u, s = _valid_us()
        content_type, chunks, _hash = image_proxy_service.get_proxied_image(u, s)
        assert content_type == "image/png"
        assert b"".join(chunks) == b"PNG"
        assert len(upsert_calls) == image_proxy_service._POOL_RETRY_ATTEMPTS


# ── touch_cache_last_accessed (BackgroundTask, best-effort) ─────────


class TestTouchCacheLastAccessed:

    def test_happy_path_calls_store(self, monkeypatch):
        touched: list[str] = []
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "touch_last_accessed",
            lambda h: touched.append(h),
        )
        image_proxy_service.touch_cache_last_accessed(_URL_HASH)
        assert touched == [_URL_HASH]

    def test_error_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "touch_last_accessed",
            lambda _h: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # Runs after the stream is already on the wire — must never raise.
        assert image_proxy_service.touch_cache_last_accessed(_URL_HASH) is None

    def test_second_touch_of_same_hash_is_throttled(self, monkeypatch):
        """Only the FIRST touch per url_hash opens a DB connection; a burst of
        served images (same CDN image across a newsletter) must not hammer the
        shared pool. The throttle suppresses the repeat within its TTL window."""
        touched: list[str] = []
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "touch_last_accessed",
            lambda h: touched.append(h),
        )
        image_proxy_service.touch_cache_last_accessed(_URL_HASH)
        image_proxy_service.touch_cache_last_accessed(_URL_HASH)
        image_proxy_service.touch_cache_last_accessed(_URL_HASH)
        # Three serves, one DB touch.
        assert touched == [_URL_HASH]

    def test_distinct_hashes_each_touch_once(self, monkeypatch):
        """The throttle is per url_hash — a different image still touches."""
        touched: list[str] = []
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "touch_last_accessed",
            lambda h: touched.append(h),
        )
        image_proxy_service.touch_cache_last_accessed("hash_a")
        image_proxy_service.touch_cache_last_accessed("hash_b")
        assert touched == ["hash_a", "hash_b"]


# ── purge_expired_images (three-state admin auth) ──────────────────


class TestPurgeExpiredImages:

    def test_no_env_var_raises_purge_disabled(self, monkeypatch):
        monkeypatch.delenv(_PURGE_ENV, raising=False)
        with pytest.raises(PurgeDisabled):
            image_proxy_service.purge_expired_images("any-token")

    def test_wrong_token_raises_invalid_admin_token(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "expected-token")
        with pytest.raises(InvalidAdminToken):
            image_proxy_service.purge_expired_images("wrong-token")

    def test_missing_token_raises_invalid_admin_token(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "expected-token")
        with pytest.raises(InvalidAdminToken):
            image_proxy_service.purge_expired_images(None)

    def test_empty_token_when_env_set_raises_invalid(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "expected-token")
        with pytest.raises(InvalidAdminToken):
            image_proxy_service.purge_expired_images("")

    def test_correct_token_runs_purge_and_returns_stats(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "expected-token")
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "purge_expired",
            lambda: (7, 98765),
        )
        result = image_proxy_service.purge_expired_images("expected-token")
        assert result.purged_count == 7
        assert result.freed_bytes == 98765

    def test_db_error_translated(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "tk")
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "purge_expired",
            lambda: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            image_proxy_service.purge_expired_images("tk")

    def test_unexpected_exception_wrapped_into_api_error(self, monkeypatch):
        monkeypatch.setenv(_PURGE_ENV, "tk")
        monkeypatch.setattr(
            image_proxy_service.image_proxy_cache_store, "purge_expired",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(ApiError):
            image_proxy_service.purge_expired_images("tk")
