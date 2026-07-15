"""Tests espejo de ``gmail_client.buzones`` (papelera, leido, spam, archivo y favoritos)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from core.email.email_client import SpamMoveResult
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.gmail_client import GmailClient


# ── delete_messages ──────────────────────────────────────────────


class TestDeleteMessages:
    def test_guard_raises_not_authenticated(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.delete_messages(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.delete_messages([]) == []

    def test_returns_all_ids_noop(self, client: GmailClient):
        client.service = MagicMock()
        result = client.delete_messages(["m1", "m2"])
        assert result == ["m1", "m2"]


# ── restore_from_trash ───────────────────────────────────────────


class TestRestoreFromTrash:
    def test_guard_raises_not_authenticated(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_trash({"m1": "ALL_MAIL"})

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.restore_from_trash({}) == {}

    def test_happy_path(self, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", {"id": "m2"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client.restore_from_trash({"m1": "ALL_MAIL", "m2": "SENT"})
        assert result == {"m1": "m1", "m2": "m2"}

    def test_modify_sends_correct_labels_per_destination_box(self, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", {"id": "m2"}, None)
            cb("m3", {"id": "m3"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        client.restore_from_trash({"m1": "ALL_MAIL", "m2": "SENT", "m3": "SPAM"})

        modify_mock = mock_service.users.return_value.messages.return_value.modify
        calls = {
            call.kwargs["id"]: call.kwargs["body"]
            for call in modify_mock.call_args_list
        }
        assert calls["m1"] == {"removeLabelIds": ["TRASH"], "addLabelIds": ["INBOX"]}
        assert calls["m2"] == {"removeLabelIds": ["TRASH"], "addLabelIds": []}
        assert calls["m3"] == {"removeLabelIds": ["TRASH"], "addLabelIds": ["SPAM"]}

    def test_partial_failure(self, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", None, Exception("untrash failed"))
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client.restore_from_trash({"m1": "ALL_MAIL", "m2": "SENT"})
        assert result == {"m1": "m1"}

    def test_http_error_raises_external_api(self, client: GmailClient):
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance
        resp = MagicMock()
        type(resp).status = 500
        batch_instance.execute.side_effect = HttpError(resp=resp, content=b"fail")

        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="restore_from_trash batch failed"):
            client.restore_from_trash({"m1": "ALL_MAIL"})

    def test_generic_error_raises_external_api(self, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance
        batch_instance.execute.side_effect = RuntimeError("connection lost")

        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="unexpected restore_from_trash error"):
            client.restore_from_trash({"m1": "ALL_MAIL"})

    def test_none_destination_uses_untrash(self, client: GmailClient):
        """When destination is None, untrash() is used instead of modify()."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client.restore_from_trash({"m1": None})
        assert result == {"m1": "m1"}

        untrash_mock = mock_service.users.return_value.messages.return_value.untrash
        untrash_mock.assert_called_once_with(userId="me", id="m1")

    def test_mixed_known_and_none_destinations(self, client: GmailClient):
        """Known destinations use modify(); None uses untrash()."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        call_count = [0]

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            call_count[0] += 1
            if call_count[0] == 1:
                # First batch: known items (modify)
                cb("m1", {"id": "m1"}, None)
            else:
                # Second batch: unknown items (untrash)
                cb("m2", {"id": "m2"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client.restore_from_trash({"m1": "ALL_MAIL", "m2": None})
        assert result == {"m1": "m1", "m2": "m2"}

        modify_mock = mock_service.users.return_value.messages.return_value.modify
        assert modify_mock.call_count == 1

        untrash_mock = mock_service.users.return_value.messages.return_value.untrash
        assert untrash_mock.call_count == 1


# ── move_to_trash ─────────────────────────────────────────────────


class TestMoveToTrash:
    def test_guard_raises_not_authenticated(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_trash(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.move_to_trash([]) == {}

    def test_happy_path(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_execute_batch_modify", return_value=["id1", "id2"]):
            result = client.move_to_trash(["id1", "id2"])
        assert result == {"id1": "id1", "id2": "id2"}

    def test_partial_failure_returns_succeeded(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_execute_batch_modify", return_value=["id1"]):
            result = client.move_to_trash(["id1", "id2"])
        assert result == {"id1": "id1"}

    def test_http_error_raises_external_api(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_execute_batch_modify", side_effect=EmailExternalAPIError("fail")):
            with pytest.raises(EmailExternalAPIError):
                client.move_to_trash(["id1"])


# ── update_read_status ────────────────────────────────────────────


class TestUpdateReadStatus:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.update_read_status(["m1"], True)

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.update_read_status([], True) == []

    def test_mark_as_read_removes_unread_label(self, client: GmailClient):
        mock_service = MagicMock()
        client.service = mock_service
        # Simulate successful batch callback
        def _batch_execute(callback=None):
            batch = mock_service.new_batch_http_request.return_value
            return None

        # Set up so batch.execute() triggers the callback with success
        batch_mock = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_mock

        def _execute():
            # The callback was registered during new_batch_http_request;
            # we simulate success by not raising. The batch add/execute
            # flow succeeds, and the callback marks each ID as successful.
            pass

        batch_mock.execute.side_effect = _execute

        # We mock _batch_modify_labels to verify the correct arguments
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.update_read_status(["m1"], True)
        mock_modify.assert_called_once_with(["m1"], remove_labels=["UNREAD"])
        assert result == ["m1"]

    def test_mark_as_unread_adds_unread_label(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.update_read_status(["m1"], False)
        mock_modify.assert_called_once_with(["m1"], add_labels=["UNREAD"])
        assert result == ["m1"]


# ── move_to_spam ─────────────────────────────────────────────────


class TestMoveToSpam:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_spam(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.move_to_spam([]) == []

    def test_adds_spam_label(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.move_to_spam(["m1"])
        mock_modify.assert_called_once_with(["m1"], add_labels=["SPAM"])
        assert result == [SpamMoveResult(old_id="m1", new_id="m1")]


# ── restore_from_spam ────────────────────────────────────────────


class TestRestoreFromSpam:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_spam(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.restore_from_spam([]) == []

    def test_removes_spam_adds_inbox(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.restore_from_spam(["m1"])
        mock_modify.assert_called_once_with(
            ["m1"], remove_labels=["SPAM"], add_labels=["INBOX"],
        )
        assert result == [SpamMoveResult(old_id="m1", new_id="m1")]


# ── move_to_archive ──────────────────────────────────────────────


class TestMoveToArchive:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.move_to_archive(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.move_to_archive([]) == []

    def test_removes_inbox_label(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.move_to_archive(["m1"])
        mock_modify.assert_called_once_with(["m1"], remove_labels=["INBOX"])
        # Gmail keeps the same id on a label change.
        assert result == [SpamMoveResult(old_id="m1", new_id="m1")]


# ── restore_from_archive ─────────────────────────────────────────


class TestRestoreFromArchive:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.restore_from_archive(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.restore_from_archive([]) == []

    def test_adds_inbox_label(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=["m1"]) as mock_modify:
            result = client.restore_from_archive(["m1"])
        mock_modify.assert_called_once_with(["m1"], add_labels=["INBOX"])
        assert result == [SpamMoveResult(old_id="m1", new_id="m1")]


# ── Favourites — set_favorite / list_favorite_ids (Q2) ────────────────


def _make_favorite_client() -> GmailClient:
    """Client with an instant (no-op) sleep for the page retry loop."""
    return GmailClient(account_label="mb__acct", sleep=lambda _s: None)


def _http_error(status: int) -> Exception:
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    type(resp).status = status
    return HttpError(resp=resp, content=b"err")


class TestGmailSetFavorite:
    def test_flag_delegates_to_batch_modify_add_starred(self):
        client = _make_favorite_client()
        client.service = MagicMock()
        with patch.object(
            client, "_batch_modify_labels", return_value=["m1"],
        ) as batch_mock:
            client.set_favorite("m1", True)
        batch_mock.assert_called_once_with(["m1"], add_labels=["STARRED"])

    def test_unflag_delegates_to_batch_modify_remove_starred(self):
        client = _make_favorite_client()
        client.service = MagicMock()
        with patch.object(
            client, "_batch_modify_labels", return_value=["m1"],
        ) as batch_mock:
            client.set_favorite("m1", False)
        batch_mock.assert_called_once_with(["m1"], remove_labels=["STARRED"])

    def test_zero_modifications_raises(self):
        client = _make_favorite_client()
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels", return_value=[]):
            with pytest.raises(EmailExternalAPIError, match="did not affect"):
                client.set_favorite("m1", True)

    def test_unauthenticated_raises(self):
        client = _make_favorite_client()
        with pytest.raises(EmailNotAuthenticatedError):
            client.set_favorite("m1", True)

    def test_empty_id_is_noop(self):
        client = _make_favorite_client()
        client.service = MagicMock()
        with patch.object(client, "_batch_modify_labels") as batch_mock:
            client.set_favorite("", True)
        batch_mock.assert_not_called()


class TestGmailListFavoriteIds:
    def _list_execute(self, mock_service):
        return mock_service.users().messages().list().execute

    def test_collects_ids_single_page(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        self._list_execute(mock_service).side_effect = [
            {"messages": [{"id": "a"}, {"id": "b"}]},
        ]
        client.service = mock_service
        assert client.list_favorite_ids() == ["a", "b"]

    def test_paginates_via_next_page_token(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        self._list_execute(mock_service).side_effect = [
            {"messages": [{"id": "a"}], "nextPageToken": "tok"},
            {"messages": [{"id": "b"}]},
        ]
        client.service = mock_service
        assert client.list_favorite_ids() == ["a", "b"]

    def test_uses_includespamtrash_and_max_results(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        self._list_execute(mock_service).side_effect = [{"messages": [{"id": "a"}]}]
        client.service = mock_service
        client.list_favorite_ids()
        # The kwargs of the real list call (the one that builds the request).
        list_method = mock_service.users().messages().list
        kwargs = list_method.call_args.kwargs
        assert kwargs["labelIds"] == ["STARRED"]
        assert kwargs["maxResults"] == 500
        assert kwargs["includeSpamTrash"] is True

    def test_retries_transient_page_then_succeeds(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        self._list_execute(mock_service).side_effect = [
            _http_error(503),
            {"messages": [{"id": "a"}]},
        ]
        client.service = mock_service
        assert client.list_favorite_ids() == ["a"]

    def test_permanent_error_aborts_without_retry(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        execute = self._list_execute(mock_service)
        execute.side_effect = _http_error(400)
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError):
            client.list_favorite_ids()
        # A permanent 400 must not be retried — exactly one execute call.
        assert execute.call_count == 1

    def test_exhausts_retries_on_persistent_transient(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        execute = self._list_execute(mock_service)
        execute.side_effect = _http_error(503)
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError):
            client.list_favorite_ids()
        # _BATCH_MAX_RETRIES + 1 == 5 total attempts.
        assert execute.call_count == 5

    def test_unauthenticated_raises(self):
        client = _make_favorite_client()
        with pytest.raises(EmailNotAuthenticatedError):
            client.list_favorite_ids()


class TestGmailListFavoriteCandidates:
    """Gmail does not override list_favorite_candidates — it inherits the
    ABC's default (delegates to list_favorite_ids(), no identity fields),
    correct because Gmail's favourite ids are already stable across
    endpoints and need no reconciliation."""

    def _list_execute(self, mock_service):
        return mock_service.users().messages().list().execute

    def test_delegates_to_list_favorite_ids_with_no_identity_fields(self):
        client = _make_favorite_client()
        mock_service = MagicMock()
        self._list_execute(mock_service).side_effect = [
            {"messages": [{"id": "a"}, {"id": "b"}]},
        ]
        client.service = mock_service
        candidates = client.list_favorite_candidates()
        assert [c.provider_message_id for c in candidates] == ["a", "b"]
        assert all(c.received_at is None for c in candidates)
        assert all(c.from_email is None for c in candidates)
        assert all(c.subject is None for c in candidates)
