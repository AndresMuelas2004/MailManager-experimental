"""
Integration tests for the per-client rate limiting (#11e).

These exercise the real app + real router dependencies + real error handler
against the ``TestClient``. The provider boundary is faked by ``test_client``.

Critical isolation trap (verified, see implementation plan §9.2): under
``TestClient`` every request reports ``request.client.host == "testclient"``
with no ``X-Forwarded-For``, so ``client_ip()`` returns ``"testclient"`` for
ALL requests in a test — they share one IP key. Any setup ``POST`` therefore
increments the ``global`` bucket too. So tests that are NOT exercising the
``global`` bucket keep it generous (e.g. ``(50, 60)``) so the setup never
trips a spurious 429 before the specific bucket under test; the test that DOES
exercise ``global`` uses a small value and starts from a clean ``reset()``.
"""

from __future__ import annotations

import pytest

from api import rate_limit


@pytest.fixture(autouse=True)
def _isolate_counters():
    """Drop counters before/after each test (the env toggle is undone by
    ``monkeypatch`` in the helper). Safe for the default-OFF test too — it only
    clears state, it does not enable the limiter."""
    rate_limit.reset()
    yield
    rate_limit.reset()


def _enable(monkeypatch, limits):
    """Turn the limiter on with a small, deterministic profile for this test."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setattr(rate_limit, "RATE_LIMITS", limits)
    rate_limit.reset()


class TestRateLimitEnabled:

    def test_login_throttled_by_ip(self, test_client, monkeypatch):
        # auth_login (2,60); global generous so the 3rd call trips auth_login.
        _enable(monkeypatch, {"auth_login": ((2, 60),), "global": ((50, 60),)})
        client = test_client
        # dev-login has no require_session; the limiter runs before the route,
        # so the first two responses' own status (503/403) is irrelevant here.
        client.post("/auth/dev-login")
        client.post("/auth/dev-login")
        resp = client.post("/auth/dev-login")
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limit_exceeded"
        # Retry-After rides BOTH the header and the JSON body. The header is not
        # CORS-exposed, so the cross-origin SPA reads the countdown from the body
        # — assert header/body parity so a regression on either half is caught.
        header_retry = int(resp.headers["Retry-After"])
        assert header_retry > 0
        assert resp.json()["error"]["detail"]["retry_after"] == header_retry

    def test_send_throttled_by_user(self, test_client, setup_mailbox_and_account, monkeypatch):
        # email_send (2,60); global generous so the two setup POSTs + the two
        # allowed sends do not trip global before email_send does.
        #
        # Axis caveat (honesty): under TestClient both the IP ("testclient") and
        # the overridden user are constant, so this proves email_send throttles
        # but NOT that it keys by user rather than IP. The keying axis is proven
        # at the unit layer (test_rate_limit_dependencies: distinct users have
        # independent counters); do not try to vary the user here — the
        # require_session override is session-scoped and shared.
        _enable(monkeypatch, {"email_send": ((2, 60), (200, 3600)), "global": ((50, 60),)})
        client = test_client
        mailbox_id, account_id = setup_mailbox_and_account(client)
        url = f"/mailboxes/{mailbox_id}/emails/send"
        body = {
            "account_id": account_id,
            "subject": "Hi",
            "body": "<p>x</p>",
            "recipients": ["dest@example.com"],
        }
        first = client.post(url, json=body)
        second = client.post(url, json=body)
        assert first.status_code != 429
        assert second.status_code != 429
        third = client.post(url, json=body)
        assert third.status_code == 429
        assert third.json()["error"]["detail"]["scope"] == "email_send"
        # Draft send shares the SAME email_send bucket (drafts_routers wires
        # rate_limit_by_user("email_send") on /drafts/{id}/send). With the bucket
        # already exhausted, sending a draft must also 429 under scope
        # email_send. Creating the draft is not email_send-throttled, so it still
        # succeeds; a regression giving draft-send its own bucket (or dropping
        # the dep) would let the send through with a 2xx.
        create = client.post(
            f"/mailboxes/{mailbox_id}/accounts/{account_id}/drafts",
            json={"subject": "d", "body": "<p>x</p>", "to_recipients": ["dest@example.com"]},
        )
        assert create.status_code == 200, create.text
        provider_draft_id = create.json()["provider_draft_id"]
        draft_send = client.post(
            f"/mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}/send"
        )
        assert draft_send.status_code == 429
        assert draft_send.json()["error"]["detail"]["scope"] == "email_send"

    def test_provider_sync_bucket_is_shared_across_endpoints(
        self, test_client, setup_mailbox_and_account, monkeypatch
    ):
        # provider_sync (2,60) is ONE quota shared by all THREE sync endpoints:
        # sync-metadata, favorites/sync AND drafts/sync. Two syncs exhaust it,
        # favourites trips on the third, and drafts/sync — sharing the same
        # bucket — also 429s while it stays exhausted. A regression that gave
        # drafts/sync its own bucket (or dropped the dep) would 2xx the last call.
        _enable(monkeypatch, {"provider_sync": ((2, 60),), "global": ((50, 60),)})
        client = test_client
        mailbox_id, _account_id = setup_mailbox_and_account(client)
        client.post(f"/mailboxes/{mailbox_id}/emails/sync-metadata")
        client.post(f"/mailboxes/{mailbox_id}/emails/sync-metadata")
        resp = client.post(f"/mailboxes/{mailbox_id}/favorites/sync")
        assert resp.status_code == 429
        assert resp.json()["error"]["detail"]["scope"] == "provider_sync"
        drafts_sync = client.post(f"/mailboxes/{mailbox_id}/drafts/sync")
        assert drafts_sync.status_code == 429
        assert drafts_sync.json()["error"]["detail"]["scope"] == "provider_sync"

    def test_global_bucket_throttles_endpoints_without_a_specific_bucket(
        self, test_client, monkeypatch
    ):
        # Isolated case: small global, clean reset, and an endpoint with no
        # specific bucket (GET /mailboxes) so only the global counter moves.
        _enable(monkeypatch, {"global": ((5, 60),)})
        client = test_client
        for _ in range(5):
            assert client.get("/mailboxes").status_code != 429
        resp = client.get("/mailboxes")
        assert resp.status_code == 429
        assert resp.json()["error"]["detail"]["scope"] == "global"

    def test_health_is_exempt_from_the_global_bucket(self, test_client, monkeypatch):
        # global small (3,60) and /health hammered well past it: if it never
        # 429s, the exemption is proven (health_router carries no global dep).
        _enable(monkeypatch, {"global": ((3, 60),)})
        client = test_client
        for _ in range(6):
            assert client.get("/health").status_code != 429

    def test_oauth_callback_is_exempt_from_the_global_bucket(self, test_client, monkeypatch):
        # The provider redirects the user's browser to the callback, so a 429
        # there would break a legitimate account connection. global small (3,60)
        # and the callback hammered past it: if it never 429s, the exemption
        # holds (oauth_callback_router carries no global dep). An empty state
        # makes the callback render its failure page (200) — we assert only that
        # it is never throttled, mirroring the /health exemption test.
        _enable(monkeypatch, {"global": ((3, 60),)})
        client = test_client
        for _ in range(6):
            assert client.get("/auth/google/callback").status_code != 429


class TestRateLimitDisabled:

    def test_disabled_by_default_never_throttles(self, test_client):
        # No activation fixture: RATE_LIMIT_ENABLED is absent, so hammering an
        # endpoint past any production cap stays un-throttled (existing tests
        # must not break).
        client = test_client
        for _ in range(6):
            assert client.post("/auth/dev-login").status_code != 429
