"""
Integration tests for the admin image-proxy purge endpoint:

``POST /admin/image-proxy/purge``

Mirrors ``test_admin_purge.py`` (the attachment-blob purge). Three documented
states:
- env var unset → 503 ``purge_disabled``
- env var set + bad/missing header → 401 ``invalid_admin_token``
- env var set + correct header → 200 with ``{purged_count, freed_bytes}``

The success path seeds one expired AND one fresh ``image_proxy_cache`` row
directly via the test transaction, then asserts the purge drops EXACTLY the
expired one while the fresh one SURVIVES — so an inverted/removed
``last_accessed_at`` WHERE clause (a purge-everything regression) is caught,
not just a trivially-passing no-op purge.
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg2


_PURGE_URL = "/admin/image-proxy/purge"
_ENV_VAR = "IMAGE_PROXY_PURGE_TOKEN"


# ── env unset → 503 purge_disabled ─────────────────────────────────


def test_purge_returns_503_when_env_var_unset(test_client_base, monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)
    resp = test_client_base.post(_PURGE_URL, headers={"X-Admin-Token": "anything"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "purge_disabled"


# ── env set + bad/missing token → 401 invalid_admin_token ──────────


def test_purge_returns_401_when_token_is_wrong(test_client_base, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    resp = test_client_base.post(_PURGE_URL, headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_admin_token"


def test_purge_returns_401_when_header_is_missing(test_client_base, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    resp = test_client_base.post(_PURGE_URL)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_admin_token"


# ── happy path with a seeded expired cache row ─────────────────────


def _seed_expired_image(connection) -> bytes:
    """Insert an ``image_proxy_cache`` row whose ``last_accessed_at`` is older
    than the 30-day purge threshold. Returns the bytes for a ``freed_bytes``
    assertion."""
    blob = b"expired-image-bytes-payload"
    url = f"https://cdn.example.com/{uuid.uuid4()}.png"
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO image_proxy_cache
                (url_hash, url, content_type, image_bytes, fetched_at, last_accessed_at)
            VALUES (%(h)s, %(u)s, 'image/png', %(b)s,
                    now() - INTERVAL '45 days', now() - INTERVAL '45 days')
            """,
            {"h": url_hash, "u": url, "b": psycopg2.Binary(blob)},
        )
    return blob


def _seed_fresh_image(connection) -> str:
    """Insert an ``image_proxy_cache`` row whose ``last_accessed_at`` is well
    within the 30-day purge threshold. Returns its ``url_hash`` so the test can
    assert the row SURVIVES the purge (an over-broad WHERE would drop it too)."""
    blob = b"fresh-image-bytes-payload"
    url = f"https://cdn.example.com/{uuid.uuid4()}.png"
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO image_proxy_cache
                (url_hash, url, content_type, image_bytes, fetched_at, last_accessed_at)
            VALUES (%(h)s, %(u)s, 'image/png', %(b)s, now(), now())
            """,
            {"h": url_hash, "u": url, "b": psycopg2.Binary(blob)},
        )
    return url_hash


def _row_exists(connection, url_hash: str) -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM image_proxy_cache WHERE url_hash = %(h)s",
            {"h": url_hash},
        )
        return cur.fetchone() is not None


def test_purge_happy_path_drops_expired_image(test_client_base, monkeypatch, isolated_db):
    monkeypatch.setenv(_ENV_VAR, "expected-token")
    blob = _seed_expired_image(isolated_db)
    fresh_hash = _seed_fresh_image(isolated_db)

    resp = test_client_base.post(_PURGE_URL, headers={"X-Admin-Token": "expected-token"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # image_proxy_cache is unseeded and transaction-isolated, so a correct
    # 30-day WHERE drops EXACTLY the one expired row seeded above.
    assert body["purged_count"] == 1
    assert body["freed_bytes"] == len(blob)
    # Load-bearing: the fresh row must SURVIVE. An inverted/removed
    # ``last_accessed_at < now() - INTERVAL '30 days'`` clause (purge-everything)
    # would drop it too, failing this assertion.
    assert _row_exists(isolated_db, fresh_hash)
