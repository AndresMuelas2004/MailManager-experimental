"""Tests espejo de ``outlook_client.buzones`` (papelera, leido, spam, archivo y favoritos con reintentos)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.email.email_client import SpamMoveResult
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.outlook_client import GRAPH_BASE_URL, OutlookClient

from ._helpers import _make_authenticated_client


# ── delete_messages ──────────────────────────────────────────────


class TestDeleteMessages:
    def test_guard_raises_not_authenticated(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.delete_messages(["m1"])

    def test_empty_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        assert client.delete_messages([]) == []

    def test_noop_returns_all_ids(self, client: OutlookClient):
        """delete_messages is a no-op — returns all IDs without calling the provider."""
        client._access_token = "tok"
        result = client.delete_messages(["m1", "m2", "m3"])
        assert result == ["m1", "m2", "m3"]


# ── restore_from_trash ───────────────────────────────────────────


class TestRestoreFromTrash:
    def test_guard_raises_not_authenticated(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_trash({"m1": "ALL_MAIL"})

    def test_empty_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        assert client.restore_from_trash({}) == {}

    def test_happy_path_id_changes(self, client: OutlookClient):
        client._access_token = "tok"
        responses = [{"id": "new_m1"}, {"id": "new_m2"}]

        with patch.object(client, "_graph_request", side_effect=responses) as mock_graph:
            result = client.restore_from_trash({"m1": "ALL_MAIL", "m2": "SENT"})

        assert result == {"m1": "new_m1", "m2": "new_m2"}
        assert mock_graph.call_count == 2

        # Verify the first call uses inbox for ALL_MAIL
        args1, kwargs1 = mock_graph.call_args_list[0]
        assert args1[0] == "POST"
        assert "/me/messages/m1/move" in args1[1]
        body1 = kwargs1.get("body", args1[2] if len(args1) > 2 else None)
        assert body1 == {"destinationId": "inbox"}

        # Verify the second call uses sentitems for SENT
        args2, kwargs2 = mock_graph.call_args_list[1]
        assert args2[0] == "POST"
        assert "/me/messages/m2/move" in args2[1]
        body2 = kwargs2.get("body", args2[2] if len(args2) > 2 else None)
        assert body2 == {"destinationId": "sentitems"}

    def test_partial_failure(self, client: OutlookClient):
        client._access_token = "tok"

        call_count = 0

        def mock_graph(method, url, body=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"id": "new_m1"}
            raise EmailExternalAPIError("move failed")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.restore_from_trash({"m1": "ALL_MAIL", "m2": "SENT"})

        assert result == {"m1": "new_m1"}

    def test_default_folder_is_inbox(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request", return_value={"id": "new_m1"}) as mock_graph:
            client.restore_from_trash({"m1": "UNKNOWN_BOX"})
        args, kwargs = mock_graph.call_args
        body = kwargs.get("body", args[2] if len(args) > 2 else None)
        assert body == {"destinationId": "inbox"}

    def test_none_destination_uses_inbox(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request", return_value={"id": "new_m1"}) as mock_graph:
            result = client.restore_from_trash({"m1": None})
        assert result == {"m1": "new_m1"}
        args, kwargs = mock_graph.call_args
        body = kwargs.get("body", args[2] if len(args) > 2 else None)
        assert body == {"destinationId": "inbox"}


# ── move_to_trash ────────────────────────────────────────────────


class TestMoveToTrash:
    def test_guard_raises_not_authenticated(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_trash(["m1"])

    def test_empty_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        assert client.move_to_trash([]) == {}

    def test_happy_path(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request") as mock_req:
            mock_req.return_value = {"id": "new_m1"}
            result = client.move_to_trash(["m1", "m2"])
        assert result == {"m1": "new_m1", "m2": "new_m1"}
        assert mock_req.call_count == 2

    def test_captures_new_id_from_response(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request") as mock_req:
            mock_req.side_effect = [{"id": "new_m1"}, {"id": "new_m2"}]
            result = client.move_to_trash(["m1", "m2"])
        assert result == {"m1": "new_m1", "m2": "new_m2"}

    def test_partial_failure_returns_succeeded(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request") as mock_req:
            mock_req.side_effect = [{"id": "new_m1"}, EmailExternalAPIError("fail")]
            result = client.move_to_trash(["m1", "m2"])
        assert result == {"m1": "new_m1"}

    def test_all_fail_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request") as mock_req:
            mock_req.side_effect = EmailExternalAPIError("fail")
            result = client.move_to_trash(["m1", "m2"])
        assert result == {}


# ── update_read_status ────────────────────────────────────────────


class TestUpdateReadStatus:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.update_read_status(["m1"], True)

    def test_empty_returns_empty(self):
        client = _make_authenticated_client()
        assert client.update_read_status([], True) == []

    def test_mark_as_read_sends_patch_is_read_true(self):
        client = _make_authenticated_client()
        graph_calls = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.update_read_status(["m1"], True)

        assert result == ["m1"]
        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "PATCH"
        assert "/me/messages/m1" in url
        assert body == {"isRead": True}

    def test_mark_as_unread_sends_patch_is_read_false(self):
        client = _make_authenticated_client()
        graph_calls = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.update_read_status(["m1"], False)

        assert result == ["m1"]
        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "PATCH"
        assert body == {"isRead": False}

    def test_missing_message_skipped(self):
        client = _make_authenticated_client()

        def mock_graph(method, url, body=None):
            if "m2" in url:
                raise EmailExternalAPIError("404 Not Found")
            return {}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.update_read_status(["m1", "m2", "m3"], True)

        assert result == ["m1", "m3"]


# ── move_to_spam ─────────────────────────────────────────────────


class TestMoveToSpam:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_spam(["m1"])

    def test_empty_returns_empty(self):
        client = _make_authenticated_client()
        assert client.move_to_spam([]) == []

    def test_happy_path_posts_move_to_junkemail(self):
        client = _make_authenticated_client()
        graph_calls: list[tuple[str, str, dict | None]] = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {"id": "new_m1"}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.move_to_spam(["m1"])

        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "POST"
        assert f"{GRAPH_BASE_URL}/me/messages/m1/move" == url
        assert body == {"destinationId": "junkemail"}
        assert result == [SpamMoveResult(old_id="m1", new_id="new_m1")]


# ── restore_from_spam ────────────────────────────────────────────


class TestRestoreFromSpam:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_spam(["m1"])

    def test_empty_returns_empty(self):
        client = _make_authenticated_client()
        assert client.restore_from_spam([]) == []

    def test_happy_path_posts_move_to_inbox(self):
        client = _make_authenticated_client()
        graph_calls: list[tuple[str, str, dict | None]] = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {"id": "new_m1"}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.restore_from_spam(["m1"])

        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "POST"
        assert f"{GRAPH_BASE_URL}/me/messages/m1/move" == url
        assert body == {"destinationId": "inbox"}
        assert result == [SpamMoveResult(old_id="m1", new_id="new_m1")]


# ── move_to_archive ──────────────────────────────────────────────


class TestMoveToArchive:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_archive(["m1"])

    def test_empty_returns_empty(self):
        client = _make_authenticated_client()
        assert client.move_to_archive([]) == []

    def test_happy_path_posts_move_to_archive_and_rewrites_id(self):
        client = _make_authenticated_client()
        graph_calls: list[tuple[str, str, dict | None]] = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {"id": "new_m1"}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.move_to_archive(["m1"])

        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "POST"
        assert f"{GRAPH_BASE_URL}/me/messages/m1/move" == url
        assert body == {"destinationId": "archive"}
        # Outlook rewrites the id on every move (default ids, old→new).
        assert result == [SpamMoveResult(old_id="m1", new_id="new_m1")]


# ── restore_from_archive ─────────────────────────────────────────


class TestRestoreFromArchive:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_archive(["m1"])

    def test_empty_returns_empty(self):
        client = _make_authenticated_client()
        assert client.restore_from_archive([]) == []

    def test_happy_path_posts_move_to_inbox_and_rewrites_id(self):
        client = _make_authenticated_client()
        graph_calls: list[tuple[str, str, dict | None]] = []

        def mock_graph(method, url, body=None):
            graph_calls.append((method, url, body))
            return {"id": "new_m1"}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.restore_from_archive(["m1"])

        assert len(graph_calls) == 1
        method, url, body = graph_calls[0]
        assert method == "POST"
        assert f"{GRAPH_BASE_URL}/me/messages/m1/move" == url
        assert body == {"destinationId": "inbox"}
        assert result == [SpamMoveResult(old_id="m1", new_id="new_m1")]


# ── Favourites — set_favorite / list_favorite_ids retries (Q2) ─────────


def _make_favorite_client() -> OutlookClient:
    """Authenticated client with an instant (no-op) sleep for retry loops."""
    client = OutlookClient(account_label="mb__outlook", sleep=lambda _s: None)
    client._access_token = "token"
    return client


def _raw(status: int, *, headers: dict | None = None, body: dict | None = None):
    """Build a ``_graph_request_raw`` return tuple (status, headers, bytes)."""
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    return (status, headers or {}, payload)


class TestOutlookSetFavoriteRetries:
    def test_success_single_patch(self):
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(200, body={"id": "m1"}),
        ) as raw_mock:
            client.set_favorite("m1", True)
        raw_mock.assert_called_once()
        method, url = raw_mock.call_args.args[0], raw_mock.call_args.args[1]
        assert method == "PATCH"
        assert "/me/messages/m1" in url
        assert raw_mock.call_args.kwargs["body"] == {"flag": {"flagStatus": "flagged"}}

    def test_unflag_sends_notflagged(self):
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(200, body={"id": "m1"}),
        ) as raw_mock:
            client.set_favorite("m1", False)
        assert raw_mock.call_args.kwargs["body"] == {"flag": {"flagStatus": "notFlagged"}}

    def test_retries_on_429_with_retry_after_then_succeeds(self):
        client = _make_favorite_client()
        seq = [
            _raw(429, headers={"Retry-After": "0"}),
            _raw(200, body={"id": "m1"}),
        ]
        with patch.object(client, "_graph_request_raw", side_effect=seq) as raw_mock:
            client.set_favorite("m1", True)
        assert raw_mock.call_count == 2

    def test_retries_on_503_then_succeeds(self):
        client = _make_favorite_client()
        seq = [_raw(503), _raw(200, body={"id": "m1"})]
        with patch.object(client, "_graph_request_raw", side_effect=seq) as raw_mock:
            client.set_favorite("m1", True)
        assert raw_mock.call_count == 2

    def test_permanent_400_does_not_retry(self):
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(400),
        ) as raw_mock:
            with pytest.raises(EmailExternalAPIError):
                client.set_favorite("m1", True)
        raw_mock.assert_called_once()  # no retry on a permanent error

    def test_permanent_403_does_not_retry(self):
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(403),
        ) as raw_mock:
            with pytest.raises(EmailExternalAPIError):
                client.set_favorite("m1", True)
        raw_mock.assert_called_once()

    def test_exhausts_retries_on_persistent_transient(self):
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(503),
        ) as raw_mock:
            with pytest.raises(EmailExternalAPIError):
                client.set_favorite("m1", True)
        # 3 total attempts (1 + 2 retries) from _OUTLOOK_RETRY_DELAYS_SECONDS.
        assert raw_mock.call_count == 3

    def test_prefer_immutable_header_on_every_attempt(self):
        client = _make_favorite_client()
        seq = [_raw(429, headers={"Retry-After": "0"}), _raw(200, body={"id": "m1"})]
        with patch.object(client, "_graph_request_raw", side_effect=seq) as raw_mock:
            client.set_favorite("m1", True)
        for call_args in raw_mock.call_args_list:
            headers = call_args.kwargs.get("extra_headers") or {}
            assert headers.get("Prefer") == 'IdType="ImmutableId"'

    def test_unauthenticated_raises(self):
        client = _make_favorite_client()
        client._access_token = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.set_favorite("m1", True)

    def test_empty_id_is_noop(self):
        client = _make_favorite_client()
        with patch.object(client, "_graph_request_raw") as raw_mock:
            client.set_favorite("", True)
        raw_mock.assert_not_called()


class TestOutlookListFavoriteIdsRetries:
    def test_collects_ids_single_page(self):
        client = _make_favorite_client()
        page = {"value": [{"id": "a"}, {"id": "b"}]}
        with patch.object(client, "_graph_request_raw", return_value=_raw(200, body=page)):
            assert client.list_favorite_ids() == ["a", "b"]

    def test_paginates_via_nextlink(self):
        client = _make_favorite_client()
        page1 = {"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next"}
        page2 = {"value": [{"id": "b"}]}
        with patch.object(
            client, "_graph_request_raw",
            side_effect=[_raw(200, body=page1), _raw(200, body=page2)],
        ) as raw_mock:
            assert client.list_favorite_ids() == ["a", "b"]
        # Second call follows the opaque nextLink URL verbatim.
        assert raw_mock.call_args_list[1].args[1] == "https://graph/next"

    def test_retries_transient_page_then_succeeds(self):
        client = _make_favorite_client()
        page = {"value": [{"id": "a"}]}
        with patch.object(
            client, "_graph_request_raw",
            side_effect=[_raw(503), _raw(200, body=page)],
        ) as raw_mock:
            assert client.list_favorite_ids() == ["a"]
        assert raw_mock.call_count == 2

    def test_permanent_error_aborts_listing(self):
        client = _make_favorite_client()
        with patch.object(client, "_graph_request_raw", return_value=_raw(400)):
            with pytest.raises(EmailExternalAPIError):
                client.list_favorite_ids()

    def test_prefer_immutable_header_on_each_page(self):
        client = _make_favorite_client()
        page1 = {"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next"}
        page2 = {"value": [{"id": "b"}]}
        with patch.object(
            client, "_graph_request_raw",
            side_effect=[_raw(200, body=page1), _raw(200, body=page2)],
        ) as raw_mock:
            client.list_favorite_ids()
        for call_args in raw_mock.call_args_list:
            headers = call_args.kwargs.get("extra_headers") or {}
            assert headers.get("Prefer") == 'IdType="ImmutableId"'

    def test_unauthenticated_raises(self):
        client = _make_favorite_client()
        client._access_token = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.list_favorite_ids()


class TestOutlookListFavoriteCandidatesRetries:
    """``list_favorite_candidates`` enriches list_favorite_ids with identity
    fields for the /favorites/sync reconciliation — same retry/pagination
    machinery, verified separately here."""

    def test_collects_candidates_with_identity_fields(self):
        client = _make_favorite_client()
        page = {
            "value": [
                {
                    "id": "a",
                    "from": {"emailAddress": {"address": "Sender@Example.com", "name": "Sender"}},
                    "subject": "Hello",
                    "receivedDateTime": "2024-01-01T12:00:00Z",
                },
            ],
        }
        with patch.object(client, "_graph_request_raw", return_value=_raw(200, body=page)):
            candidates = client.list_favorite_candidates()
        assert len(candidates) == 1
        c = candidates[0]
        assert c.provider_message_id == "a"
        assert c.from_email == "Sender@Example.com"
        assert c.subject == "Hello"
        assert c.received_at == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_missing_identity_fields_default_to_empty(self):
        """Absent from/subject/receivedDateTime parse to the same defaults
        _parse_graph_message already uses for sync — never None, so the
        reconciliation's identity tuple stays comparable."""
        client = _make_favorite_client()
        page = {"value": [{"id": "a"}]}
        with patch.object(client, "_graph_request_raw", return_value=_raw(200, body=page)):
            candidates = client.list_favorite_candidates()
        c = candidates[0]
        assert c.from_email == ""
        assert c.subject == ""
        assert c.received_at == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_paginates_via_nextlink(self):
        client = _make_favorite_client()
        page1 = {
            "value": [{"id": "a", "subject": "s1", "receivedDateTime": "2024-01-01T12:00:00Z"}],
            "@odata.nextLink": "https://graph/next",
        }
        page2 = {"value": [{"id": "b", "subject": "s2", "receivedDateTime": "2024-01-02T12:00:00Z"}]}
        with patch.object(
            client, "_graph_request_raw",
            side_effect=[_raw(200, body=page1), _raw(200, body=page2)],
        ) as raw_mock:
            candidates = client.list_favorite_candidates()
        assert [c.provider_message_id for c in candidates] == ["a", "b"]
        assert raw_mock.call_args_list[1].args[1] == "https://graph/next"

    def test_select_includes_identity_fields(self):
        """The enriched $select must fetch identity fields in the SAME page
        request — no extra round trip per candidate."""
        client = _make_favorite_client()
        with patch.object(
            client, "_graph_request_raw", return_value=_raw(200, body={"value": []}),
        ) as raw_mock:
            client.list_favorite_candidates()
        url = raw_mock.call_args_list[0].args[1]
        assert "$select=id,from,subject,receivedDateTime,sentDateTime" in url

    def test_retries_transient_page_then_succeeds(self):
        client = _make_favorite_client()
        page = {"value": [{"id": "a"}]}
        with patch.object(
            client, "_graph_request_raw",
            side_effect=[_raw(503), _raw(200, body=page)],
        ) as raw_mock:
            candidates = client.list_favorite_candidates()
        assert [c.provider_message_id for c in candidates] == ["a"]
        assert raw_mock.call_count == 2

    def test_permanent_error_aborts_listing(self):
        client = _make_favorite_client()
        with patch.object(client, "_graph_request_raw", return_value=_raw(400)):
            with pytest.raises(EmailExternalAPIError):
                client.list_favorite_candidates()

    def test_unauthenticated_raises(self):
        client = _make_favorite_client()
        client._access_token = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.list_favorite_candidates()
