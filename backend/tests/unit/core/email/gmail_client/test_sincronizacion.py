"""Tests espejo de ``gmail_client.sincronizacion`` (bootstrap, incremental y lotes batch)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from core.email.email_client import BackfillPage, EmailMetadata, SyncResult
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.gmail_client import GmailClient, _INCREMENTAL_EVENT_THRESHOLD


# ── fetch_email_metadata paths ───────────────────────────────────────


class TestFetchEmailMetadata:
    """Test the routing logic of fetch_email_metadata (bootstrap vs incremental)."""

    def test_no_cursor_calls_bootstrap(self, client: GmailClient):
        client.service = MagicMock()
        fake_result = SyncResult(upserts=[], new_cursor="hist123")
        with patch.object(client, "_bootstrap_email_metadata", return_value=fake_result) as mock_bs:
            result = client.fetch_email_metadata(sync_cursor=None)
        mock_bs.assert_called_once_with(500)
        assert result is fake_result

    def test_valid_cursor_calls_incremental(self, client: GmailClient):
        """With a valid cursor, Path 2 incremental is called."""
        client.service = MagicMock()
        fake_result = SyncResult(upserts=[], new_cursor="hist456")
        with patch.object(client, "_incremental_email_metadata", return_value=fake_result) as mock_inc:
            result = client.fetch_email_metadata(sync_cursor="old_cursor")
        mock_inc.assert_called_once_with("old_cursor")
        assert result is fake_result

    def test_incremental_failure_falls_back_to_bootstrap(self, client: GmailClient):
        """If incremental raises EmailExternalAPIError, bootstrap is used as fallback."""
        client.service = MagicMock()
        fake_result = SyncResult(upserts=[], new_cursor="hist456")
        with patch.object(client, "_incremental_email_metadata", side_effect=EmailExternalAPIError("fail")), \
             patch.object(client, "_bootstrap_email_metadata", return_value=fake_result) as mock_bs:
            result = client.fetch_email_metadata(sync_cursor="old_cursor")
        mock_bs.assert_called_once_with(500)
        assert result is fake_result


# ── _parse_metadata_response ─────────────────────────────────────────


class TestParseMetadataResponse:
    def test_parses_full_message(self):
        msg = {
            "id": "msg1",
            "threadId": "thread1",
            "internalDate": "1700000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "Subject", "value": "Hello"},
                ],
            },
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.provider_message_id == "msg1"
        assert result.thread_id == "thread1"
        assert result.from_email == "alice@example.com"
        assert result.from_name == "Alice"
        assert result.subject == "Hello"
        assert result.is_read is True  # UNREAD not in labels
        assert result.box == "ALL_MAIL"

    def test_unread_label(self):
        msg = {
            "id": "msg2",
            "threadId": "t2",
            "internalDate": "1700000000000",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.is_read is False

    def test_spam_box(self):
        msg = {
            "id": "msg3",
            "threadId": "t3",
            "internalDate": "1700000000000",
            "labelIds": ["SPAM"],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.box == "SPAM"

    def test_trash_box(self):
        msg = {
            "id": "msg4",
            "threadId": "t4",
            "internalDate": "1700000000000",
            "labelIds": ["TRASH"],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.box == "TRASH"

    def test_archive_box(self):
        # Received message synced without INBOX → archived in Gmail.
        msg = {
            "id": "msg4b",
            "threadId": "t4b",
            "internalDate": "1700000000000",
            "labelIds": ["IMPORTANT"],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.box == "ARCHIVE"

    def test_bare_email_address(self):
        msg = {
            "id": "msg5",
            "threadId": "t5",
            "internalDate": "1700000000000",
            "labelIds": [],
            "payload": {
                "headers": [
                    {"name": "From", "value": "bare@example.com"},
                ],
            },
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.from_email == "bare@example.com"
        assert result.from_name == ""


# ── _resolve_labels ──────────────────────────────────────────────────


class TestResolveLabels:
    def test_read_all_mail(self):
        is_read, box = GmailClient._resolve_labels(["INBOX"])
        assert is_read is True
        assert box == "ALL_MAIL"

    def test_unread(self):
        is_read, box = GmailClient._resolve_labels(["INBOX", "UNREAD"])
        assert is_read is False
        assert box == "ALL_MAIL"

    def test_spam(self):
        is_read, box = GmailClient._resolve_labels(["SPAM"])
        assert is_read is True
        assert box == "SPAM"

    def test_trash(self):
        is_read, box = GmailClient._resolve_labels(["TRASH", "UNREAD"])
        assert is_read is False
        assert box == "TRASH"

    def test_empty_labels_is_archived(self):
        # A received message with NO INBOX label (and not SPAM/TRASH/SENT)
        # is what "archived in Gmail" means: it lives in All Mail without the
        # INBOX label. Empty labels therefore classify as ARCHIVE, not ALL_MAIL.
        is_read, box = GmailClient._resolve_labels([])
        assert is_read is True
        assert box == "ARCHIVE"

    def test_inbox_label_keeps_all_mail(self):
        # Regression for the central classification change: a message that
        # STILL carries INBOX must stay in ALL_MAIL — only the absence of
        # INBOX flips it to ARCHIVE.
        _, box = GmailClient._resolve_labels(["INBOX"])
        assert box == "ALL_MAIL"

    def test_archived_when_inbox_removed(self):
        # Labels present but no INBOX/SPAM/TRASH/SENT among them → archived.
        _, box = GmailClient._resolve_labels(["IMPORTANT", "CATEGORY_UPDATES"])
        assert box == "ARCHIVE"

    def test_sent(self):
        is_read, box = GmailClient._resolve_labels(["SENT"])
        assert is_read is True
        assert box == "SENT"

    def test_sent_with_inbox(self):
        is_read, box = GmailClient._resolve_labels(["SENT", "INBOX"])
        assert is_read is True
        assert box == "SENT"

    def test_trash_beats_spam(self):
        _, box = GmailClient._resolve_labels(["TRASH", "SPAM"])
        assert box == "TRASH"

    def test_trash_beats_sent(self):
        _, box = GmailClient._resolve_labels(["TRASH", "SENT"])
        assert box == "TRASH"

    def test_spam_beats_sent(self):
        _, box = GmailClient._resolve_labels(["SPAM", "SENT"])
        assert box == "SPAM"


# ── _incremental_email_metadata ─────────────────────────────────────


def _make_history_response(history=None, history_id="999", next_page_token=None):
    """Helper to build a Gmail history.list response."""
    resp = {"historyId": history_id}
    if history is not None:
        resp["history"] = history
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


class TestIncrementalEmailMetadata:
    def test_empty_history_returns_empty_result(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=[], history_id="100")
        )
        client.service = mock_service
        result = client._incremental_email_metadata("50")
        assert isinstance(result, SyncResult)
        assert result.upserts == []
        assert result.deletes == []
        assert result.label_updates == []
        assert result.new_cursor == "100"

    def test_messages_added_goes_to_upserts(self, client: GmailClient):
        mock_service = MagicMock()
        history = [{"messagesAdded": [
            {"message": {"id": "m1"}},
            {"message": {"id": "m2"}},
        ]}]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="200")
        )
        # fetch_messages_metadata returns metadata for the requested IDs
        client.service = mock_service
        with patch.object(client, "fetch_messages_metadata", return_value=["meta1", "meta2"]) as mock_batch:
            result = client._incremental_email_metadata("100")
        assert set(mock_batch.call_args[0][0]) == {"m1", "m2"}
        assert result.upserts == ["meta1", "meta2"]
        assert result.deletes == []

    def test_messages_deleted_confirmed_goes_to_deletes(self, client: GmailClient):
        """When batch probe returns empty, the message is confirmed as delete."""
        mock_service = MagicMock()
        history = [{"messagesDeleted": [{"message": {"id": "d1"}}]}]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="200")
        )
        client.service = mock_service
        with patch.object(client, "_execute_batch_get", return_value={}), \
             patch.object(client, "fetch_messages_metadata", return_value=[]):
            result = client._incremental_email_metadata("100")
        assert "d1" in result.deletes
        assert result.upserts == []

    def test_messages_deleted_still_exists_goes_to_upserts(self, client: GmailClient):
        """When batch probe returns the message, it moves to need_get."""
        mock_service = MagicMock()
        history = [{"messagesDeleted": [{"message": {"id": "d1"}}]}]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="200")
        )
        client.service = mock_service
        with patch.object(client, "_execute_batch_get", return_value={"d1": {"id": "d1"}}), \
             patch.object(client, "fetch_messages_metadata", return_value=["meta_d1"]) as mock_batch:
            result = client._incremental_email_metadata("100")
        assert "d1" in mock_batch.call_args[0][0]
        assert result.upserts == ["meta_d1"]
        assert result.deletes == []

    def test_label_changes_not_in_get_or_delete_go_to_label_updates(self, client: GmailClient):
        """labelsAdded/Removed for messages not already in need_get/deletes → label_updates."""
        mock_service = MagicMock()
        history = [{
            "labelsAdded": [{"message": {"id": "la1"}, "labelIds": ["INBOX"]}],
            "labelsRemoved": [{"message": {"id": "lr1"}, "labelIds": ["UNREAD"]}],
        }]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="200")
        )
        client.service = mock_service
        fake_updates = [MagicMock(), MagicMock()]
        with patch.object(client, "fetch_messages_metadata", return_value=[]), \
             patch.object(client, "_batch_fetch_label_updates", return_value=fake_updates) as mock_lu:
            result = client._incremental_email_metadata("100")
        called_ids = set(mock_lu.call_args[0][0])
        assert called_ids == {"la1", "lr1"}
        assert result.label_updates == fake_updates

    def test_label_change_deduped_if_in_need_get(self, client: GmailClient):
        """A message in both messagesAdded and labelsAdded only appears in upserts."""
        mock_service = MagicMock()
        history = [{
            "messagesAdded": [{"message": {"id": "m1"}}],
            "labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["INBOX"]}],
        }]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="200")
        )
        client.service = mock_service
        with patch.object(client, "fetch_messages_metadata", return_value=["meta1"]), \
             patch.object(client, "_batch_fetch_label_updates", return_value=[]) as mock_lu:
            result = client._incremental_email_metadata("100")
        # _batch_fetch_label_updates called with empty list (m1 already in need_get)
        assert mock_lu.call_count == 0 or mock_lu.call_args[0][0] == []
        assert result.upserts == ["meta1"]

    def test_pagination_aggregates_all_pages(self, client: GmailClient):
        """Paginates through multiple history.list pages."""
        mock_service = MagicMock()
        page1 = _make_history_response(
            history=[{"messagesAdded": [{"message": {"id": "m1"}}]}],
            history_id="150",
            next_page_token="token2",
        )
        page2 = _make_history_response(
            history=[{"messagesAdded": [{"message": {"id": "m2"}}]}],
            history_id="200",
        )
        mock_service.users().history().list().execute.side_effect = [page1, page2]
        client.service = mock_service
        with patch.object(client, "fetch_messages_metadata", return_value=["meta1", "meta2"]) as mock_batch:
            result = client._incremental_email_metadata("100")
        called_ids = set(mock_batch.call_args[0][0])
        assert called_ids == {"m1", "m2"}
        assert result.new_cursor == "200"

    def test_history_list_http_error_raises(self, client: GmailClient):
        mock_service = MagicMock()
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        type(resp).status = 410
        mock_service.users().history().list().execute.side_effect = HttpError(
            resp=resp, content=b"gone"
        )
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="history.list failed"):
            client._incremental_email_metadata("100")


# ── _batch_fetch_label_updates ──────────────────────────────────────


class TestBatchFetchLabelUpdates:
    def test_parses_labels_to_label_update(self, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1", "labelIds": ["INBOX", "UNREAD"]}, None)
            cb("m2", {"id": "m2", "labelIds": ["SPAM"]}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client._batch_fetch_label_updates(["m1", "m2"])

        assert len(result) == 2
        lu1 = next(lu for lu in result if lu.provider_message_id == "m1")
        assert lu1.is_read is False
        assert lu1.box == "ALL_MAIL"
        lu2 = next(lu for lu in result if lu.provider_message_id == "m2")
        assert lu2.is_read is True
        assert lu2.box == "SPAM"

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    def test_skips_failed_messages(self, mock_sleep, client: GmailClient):
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", None, Exception("not found"))
            cb("m2", {"id": "m2", "labelIds": ["TRASH"]}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client._batch_fetch_label_updates(["m1", "m2"])

        assert len(result) == 1
        assert result[0].provider_message_id == "m2"
        assert result[0].box == "TRASH"


# ── _list_message_ids ─────────────────────────────────────────────


class TestListMessageIds:
    def test_single_page(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}],
        }
        client.service = mock_service
        ids = client._list_message_ids(500)
        assert ids == ["m1", "m2"]

    def test_pagination_with_max_total(self, client: GmailClient):
        mock_service = MagicMock()
        page1 = {
            "messages": [{"id": f"m{i}"} for i in range(3)],
            "nextPageToken": "tok2",
        }
        page2 = {
            "messages": [{"id": f"m{i}"} for i in range(3, 6)],
        }
        mock_service.users().messages().list().execute.side_effect = [page1, page2]
        client.service = mock_service
        ids = client._list_message_ids(4)
        assert len(ids) == 4

    def test_http_error_raises_external_api_error(self, client: GmailClient):
        from googleapiclient.errors import HttpError
        mock_service = MagicMock()
        resp = MagicMock()
        type(resp).status = 500
        mock_service.users().messages().list().execute.side_effect = HttpError(
            resp=resp, content=b"fail"
        )
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="fetch message list"):
            client._list_message_ids(500)

    def test_generic_exception_raises_external_api_error(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.side_effect = RuntimeError("boom")
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="RuntimeError"):
            client._list_message_ids(500)


# ── _get_current_history_id ───────────────────────────────────────


class TestGetCurrentHistoryId:
    def test_happy_path(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().getProfile().execute.return_value = {"historyId": "12345"}
        client.service = mock_service
        assert client._get_current_history_id() == "12345"

    def test_http_error_raises_external_api_error(self, client: GmailClient):
        from googleapiclient.errors import HttpError
        mock_service = MagicMock()
        resp = MagicMock()
        type(resp).status = 500
        mock_service.users().getProfile().execute.side_effect = HttpError(
            resp=resp, content=b"fail"
        )
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="historyId"):
            client._get_current_history_id()

    def test_generic_exception_raises_external_api_error(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().getProfile().execute.side_effect = RuntimeError("boom")
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="RuntimeError"):
            client._get_current_history_id()


# ── _bootstrap_email_metadata ─────────────────────────────────────


class TestBootstrapEmailMetadata:
    @staticmethod
    def _meta(msg_id: str, *, is_read: bool = False) -> EmailMetadata:
        return EmailMetadata(
            provider_message_id=msg_id,
            thread_id="t1",
            from_email="from@example.com",
            from_name="From",
            subject="subject",
            received_at=datetime(2026, 1, 1),
            is_read=is_read,
            box="ALL_MAIL",
        )

    def test_calls_list_batch_history_and_returns_sync_result(self, client: GmailClient):
        client.service = MagicMock()
        meta1, meta2 = self._meta("m1"), self._meta("m2")
        with patch.object(client, "_list_message_ids", return_value=["m1", "m2"]) as mock_list, \
             patch.object(client, "fetch_messages_metadata", return_value=[meta1, meta2]) as mock_batch, \
             patch.object(client, "_get_current_history_id", return_value="hist99") as mock_hist:
            result = client._bootstrap_email_metadata(500)

        mock_list.assert_called_once_with(500)
        mock_batch.assert_called_once_with(["m1", "m2"])
        mock_hist.assert_called_once()
        assert isinstance(result, SyncResult)
        assert result.upserts == [meta1, meta2]
        assert result.new_cursor == "hist99"

    def test_duplicate_metadata_collapses_to_last_upsert(self, client: GmailClient):
        """messages.list pagination can repeat an id when the mailbox shifts
        between pages; bootstrap dedupes keeping the newest state so the batch
        persistence never sees the same key twice."""
        client.service = MagicMock()
        stale, fresh = self._meta("m1", is_read=False), self._meta("m1", is_read=True)
        with patch.object(client, "_list_message_ids", return_value=["m1", "m1"]), \
             patch.object(client, "fetch_messages_metadata", return_value=[stale, fresh]), \
             patch.object(client, "_get_current_history_id", return_value="hist99"):
            result = client._bootstrap_email_metadata(500)

        assert result.upserts == [fresh]


# ── _execute_batch_get retry logic ──────────────────────────────────


class TestExecuteBatchGetSequentialFallback:
    """Tests for the sequential fallback when _credentials is None."""

    def test_no_failures_no_retry(self, client: GmailClient):
        """All messages succeed on the first attempt — batch.execute() called once."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", {"id": "m2"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        assert client._credentials is None
        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert set(result.keys()) == {"m1", "m2"}
        assert batch_instance.execute.call_count == 1

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    def test_partial_failure_retries_only_failed(self, mock_sleep, client: GmailClient):
        """2 of 5 fail, retry succeeds — second batch only contains the 2 failed."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        call_count = 0

        def fake_execute():
            nonlocal call_count
            call_count += 1
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            if call_count == 1:
                cb("m1", {"id": "m1"}, None)
                cb("m2", {"id": "m2"}, None)
                cb("m3", {"id": "m3"}, None)
                cb("m4", None, Exception("rate limited"))
                cb("m5", None, Exception("rate limited"))
            else:
                cb("m4", {"id": "m4"}, None)
                cb("m5", {"id": "m5"}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client._execute_batch_get(
            ["m1", "m2", "m3", "m4", "m5"], fmt="metadata", error_context="test",
        )
        assert set(result.keys()) == {"m1", "m2", "m3", "m4", "m5"}
        assert batch_instance.execute.call_count == 2
        mock_sleep.assert_called_once()

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    def test_all_retries_exhausted_messages_lost(self, mock_sleep, client: GmailClient):
        """A message fails on every attempt and is not in the final result."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            added_ids = [c.kwargs["request_id"] for c in batch_instance.add.call_args_list]
            batch_instance.add.call_args_list.clear()
            for rid in added_ids:
                if rid == "m2":
                    cb(rid, None, Exception("always fails"))
                else:
                    cb(rid, {"id": rid}, None)
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert "m1" in result
        assert "m2" not in result
        assert batch_instance.execute.call_count == 5
        assert mock_sleep.call_count == 4

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    def test_retry_with_fixed_delay(self, mock_sleep, client: GmailClient):
        """Verify sleep delays are all 1.0s (fixed delay)."""
        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", None, Exception("fail"))
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        client._execute_batch_get(["m1"], fmt="metadata", error_context="test")
        assert mock_sleep.call_args_list == [call(1.0), call(1.0), call(1.0), call(1.0)]

    def test_batch_execute_exception_raises(self, client: GmailClient):
        """HttpError from batch.execute() raises EmailExternalAPIError."""
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance
        resp = MagicMock()
        type(resp).status = 500
        batch_instance.execute.side_effect = HttpError(resp=resp, content=b"fail")

        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="test failed"):
            client._execute_batch_get(["m1"], fmt="metadata", error_context="test")

    def test_empty_message_ids_returns_empty(self, client: GmailClient):
        """Empty input returns empty dict without calling the service."""
        client.service = MagicMock()
        result = client._execute_batch_get([], fmt="metadata", error_context="test")
        assert result == {}


class TestExecuteBatchGetParallel:
    """Tests for the parallel path (when _credentials is set)."""

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_success_all_chunks(self, mock_auth_http, mock_build, client: GmailClient):
        """All messages succeed on first attempt via parallel path."""
        client.service = MagicMock()
        client._credentials = MagicMock()

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", {"id": "m2"}, None)
        batch_instance.execute.side_effect = fake_execute

        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert set(result.keys()) == {"m1", "m2"}

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_partial_failure_retries(self, mock_auth_http, mock_build, mock_sleep, client: GmailClient):
        """Failed IDs are retried in subsequent attempts."""
        client.service = MagicMock()
        client._credentials = MagicMock()

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        call_count = 0

        def fake_execute():
            nonlocal call_count
            call_count += 1
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            if call_count == 1:
                cb("m1", {"id": "m1"}, None)
                cb("m2", None, Exception("rate limited"))
            else:
                cb("m2", {"id": "m2"}, None)
        batch_instance.execute.side_effect = fake_execute

        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert set(result.keys()) == {"m1", "m2"}
        mock_sleep.assert_called_once_with(1.0)

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_all_retries_exhausted(self, mock_auth_http, mock_build, mock_sleep, client: GmailClient):
        """A message that always fails is lost after all retries."""
        client.service = MagicMock()
        client._credentials = MagicMock()

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            added_ids = [c.kwargs["request_id"] for c in batch_instance.add.call_args_list]
            batch_instance.add.call_args_list.clear()
            for rid in added_ids:
                if rid == "m2":
                    cb(rid, None, Exception("always fails"))
                else:
                    cb(rid, {"id": rid}, None)
        batch_instance.execute.side_effect = fake_execute

        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert "m1" in result
        assert "m2" not in result
        assert mock_sleep.call_count == 4

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_chunk_level_exception_marks_all_failed(self, mock_auth_http, mock_build, client: GmailClient):
        """An exception in batch.execute() marks all chunk IDs as failed (no raise)."""
        client.service = MagicMock()
        client._credentials = MagicMock()

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance
        batch_instance.execute.side_effect = RuntimeError("connection reset")

        result = client._execute_batch_get(
            ["m1"], fmt="metadata", error_context="test",
        )
        assert "m1" not in result


class TestExecuteSingleChunk:
    """Tests for _execute_single_chunk."""

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_success(self, mock_auth_http, mock_build, client: GmailClient):
        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
        batch_instance.execute.side_effect = fake_execute

        successes, failed, permanent_failed, elapsed = client._execute_single_chunk(
            ["m1"], 0, fmt="metadata",
            credentials=MagicMock(),
        )
        assert successes == {"m1": {"id": "m1"}}
        assert failed == []
        assert permanent_failed == []
        assert elapsed >= 0

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_partial_failure(self, mock_auth_http, mock_build, client: GmailClient):
        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", None, Exception("fail"))
        batch_instance.execute.side_effect = fake_execute

        successes, failed, permanent_failed, elapsed = client._execute_single_chunk(
            ["m1", "m2"], 0, fmt="metadata",
            credentials=MagicMock(),
        )
        assert successes == {"m1": {"id": "m1"}}
        assert failed == ["m2"]
        assert permanent_failed == []

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_total_exception(self, mock_auth_http, mock_build, client: GmailClient):
        """batch.execute() raises — all IDs marked failed, no exception propagated."""
        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance
        batch_instance.execute.side_effect = RuntimeError("connection reset")

        successes, failed, permanent_failed, elapsed = client._execute_single_chunk(
            ["m1", "m2"], 0, fmt="metadata",
            credentials=MagicMock(),
        )
        assert successes == {}
        assert set(failed) == {"m1", "m2"}
        assert permanent_failed == []


    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_non_retryable_callback_goes_to_permanent_failed(self, mock_auth_http, mock_build, client: GmailClient):
        """HttpError 404 in callback → permanent_failed, not retryable failed."""
        from googleapiclient.errors import HttpError

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        resp_404 = MagicMock()
        type(resp_404).status = 404

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", None, HttpError(resp=resp_404, content=b"not found"))
        batch_instance.execute.side_effect = fake_execute

        successes, failed, permanent_failed, elapsed = client._execute_single_chunk(
            ["m1", "m2"], 0, fmt="metadata",
            credentials=MagicMock(),
        )
        assert successes == {"m1": {"id": "m1"}}
        assert failed == []
        assert permanent_failed == ["m2"]

    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_chunk_level_non_retryable_http_error(self, mock_auth_http, mock_build, client: GmailClient):
        """Non-retryable HttpError from batch.execute() → all IDs permanently failed."""
        from googleapiclient.errors import HttpError

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        resp_400 = MagicMock()
        type(resp_400).status = 400
        batch_instance.execute.side_effect = HttpError(resp=resp_400, content=b"bad request")

        successes, failed, permanent_failed, elapsed = client._execute_single_chunk(
            ["m1", "m2"], 0, fmt="metadata",
            credentials=MagicMock(),
        )
        assert successes == {}
        assert failed == []
        assert set(permanent_failed) == {"m1", "m2"}


# ── Non-retryable errors skip retry in batch paths ──────────────


class TestNonRetryableSkipsRetry:
    """Non-retryable errors (e.g. 404) must not trigger retry loops."""

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    def test_sequential_path_no_retry_for_404(self, mock_sleep, client: GmailClient):
        """HttpError 404 in sequential callback → no retry, message simply missing."""
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        batch_instance = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_instance

        resp_404 = MagicMock()
        type(resp_404).status = 404

        def fake_execute():
            cb = mock_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", None, HttpError(resp=resp_404, content=b"not found"))
        batch_instance.execute.side_effect = fake_execute

        client.service = mock_service
        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert "m1" in result
        assert "m2" not in result
        assert batch_instance.execute.call_count == 1
        mock_sleep.assert_not_called()

    @patch("core.email.gmail_client.sincronizacion.time.sleep")
    @patch("core.email.gmail_client.sincronizacion.build")
    @patch("core.email.gmail_client.sincronizacion.google_auth_httplib2.AuthorizedHttp")
    def test_parallel_path_no_retry_for_404(self, mock_auth_http, mock_build, mock_sleep, client: GmailClient):
        """HttpError 404 in parallel callback → no retry, message simply missing."""
        from googleapiclient.errors import HttpError

        client.service = MagicMock()
        client._credentials = MagicMock()

        thread_service = MagicMock()
        mock_build.return_value = thread_service
        batch_instance = MagicMock()
        thread_service.new_batch_http_request.return_value = batch_instance

        resp_404 = MagicMock()
        type(resp_404).status = 404

        def fake_execute():
            cb = thread_service.new_batch_http_request.call_args[1]["callback"]
            cb("m1", {"id": "m1"}, None)
            cb("m2", None, HttpError(resp=resp_404, content=b"not found"))
        batch_instance.execute.side_effect = fake_execute

        result = client._execute_batch_get(
            ["m1", "m2"], fmt="metadata", error_context="test",
        )
        assert "m1" in result
        assert "m2" not in result
        assert batch_instance.execute.call_count == 1
        mock_sleep.assert_not_called()


class TestFetchSenderEmail:
    def test_caches_result(self, client: GmailClient):
        """_fetch_sender_email calls getProfile only once, caches for subsequent calls."""
        mock_service = MagicMock()
        client.service = mock_service
        mock_service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "me@gmail.com",
        }
        first = client._fetch_sender_email()
        second = client._fetch_sender_email()
        assert first == "me@gmail.com"
        assert second == "me@gmail.com"
        mock_service.users.return_value.getProfile.return_value.execute.assert_called_once()

    def test_returns_empty_on_error(self, client: GmailClient):
        """_fetch_sender_email returns empty string when getProfile raises."""
        mock_service = MagicMock()
        client.service = mock_service
        mock_service.users.return_value.getProfile.return_value.execute.side_effect = RuntimeError("boom")
        result = client._fetch_sender_email()
        assert result == ""


# ── verify_message_existence ───────────────────────────────────────


class TestVerifyMessageExistence:
    def test_not_authenticated_raises(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.verify_message_existence(["m1"])

    def test_empty_list_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.verify_message_existence([]) == []

    def test_all_exist(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(
            client, "_execute_batch_get",
            return_value={"m1": {"id": "m1"}, "m2": {"id": "m2"}},
        ):
            result = client.verify_message_existence(["m1", "m2"])
        assert result == ["m1", "m2"]

    def test_some_missing(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(
            client, "_execute_batch_get",
            return_value={"m1": {"id": "m1"}},
        ):
            result = client.verify_message_existence(["m1", "m2", "m3"])
        assert result == ["m1"]

    def test_all_missing(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_execute_batch_get", return_value={}):
            result = client.verify_message_existence(["m1", "m2"])
        assert result == []


# ── is_full_sync flag ──────────────────────────────────────────────


class TestBootstrapIsFullSync:
    def test_bootstrap_sets_is_full_sync_true(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_list_message_ids", return_value=[]), \
             patch.object(client, "fetch_messages_metadata", return_value=[]), \
             patch.object(client, "_get_current_history_id", return_value="hist1"):
            result = client._bootstrap_email_metadata(500)
        assert result.is_full_sync is True


class TestIncrementalIsFullSync:
    def test_incremental_sets_is_full_sync_false(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=[], history_id="200")
        )
        client.service = mock_service
        result = client._incremental_email_metadata("100")
        assert result.is_full_sync is False


# ── incremental threshold ─────────────────────────────────────────


class TestIncrementalThreshold:
    def test_threshold_triggers_fallback(self, client: GmailClient):
        mock_service = MagicMock()
        ids_count = _INCREMENTAL_EVENT_THRESHOLD + 1
        history = [{"messagesAdded": [
            {"message": {"id": f"m{i}"}} for i in range(ids_count)
        ]}]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="999")
        )
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="exceeding threshold"):
            client._incremental_email_metadata("50")

    def test_at_threshold_does_not_trigger(self, client: GmailClient):
        mock_service = MagicMock()
        history = [{"messagesAdded": [
            {"message": {"id": f"m{i}"}} for i in range(_INCREMENTAL_EVENT_THRESHOLD)
        ]}]
        mock_service.users().history().list().execute.return_value = (
            _make_history_response(history=history, history_id="999")
        )
        client.service = mock_service
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            result = client._incremental_email_metadata("50")
        assert isinstance(result, SyncResult)


# ── fetch_messages_metadata (public) ─────────────────────────────


# ── _list_message_ids_page (single-page primitive of the backfill) ──


class TestListMessageIdsPage:
    def test_returns_ids_and_next_token(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "nextPageToken": "tok2",
        }
        client.service = mock_service
        ids, next_token = client._list_message_ids_page(None, 500)
        assert ids == ["m1", "m2"]
        assert next_token == "tok2"

    def test_no_next_token_when_mailbox_exhausted(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1"}],
        }
        client.service = mock_service
        ids, next_token = client._list_message_ids_page("tok1", 500)
        assert ids == ["m1"]
        assert next_token is None

    def test_page_size_clamped_to_gmail_max_500(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {"messages": []}
        client.service = mock_service
        client._list_message_ids_page(None, 1000)
        # maxResults is clamped to the Gmail 500-per-page hard maximum.
        _, kwargs = mock_service.users().messages().list.call_args
        assert kwargs["maxResults"] == 500
        assert kwargs["includeSpamTrash"] is True

    def test_forwards_page_token(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {"messages": []}
        client.service = mock_service
        client._list_message_ids_page("cursor-xyz", 500)
        _, kwargs = mock_service.users().messages().list.call_args
        assert kwargs["pageToken"] == "cursor-xyz"


# ── capture_backfill_anchor (Gmail: historyId before listing) ──────


class TestCaptureBackfillAnchor:
    def test_returns_current_history_id(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_get_current_history_id", return_value="hist777") as mock_hist:
            assert client.capture_backfill_anchor() == "hist777"
        mock_hist.assert_called_once()

    def test_requires_authentication(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.capture_backfill_anchor()


# ── fetch_backfill_page (one wave: list page + batched metadata) ───


class TestFetchBackfillPage:
    @staticmethod
    def _meta(msg_id: str, *, is_read: bool = False) -> EmailMetadata:
        return EmailMetadata(
            provider_message_id=msg_id,
            thread_id="t1",
            from_email="from@example.com",
            from_name="From",
            subject="subject",
            received_at=datetime(2026, 1, 1),
            is_read=is_read,
            box="ALL_MAIL",
        )

    def test_first_page_lists_then_batches_metadata(self, client: GmailClient):
        client.service = MagicMock()
        meta1, meta2 = self._meta("m1"), self._meta("m2")
        with patch.object(
            client, "_list_message_ids_page", return_value=(["m1", "m2"], "tok2"),
        ) as mock_list, patch.object(
            client, "fetch_messages_metadata", return_value=[meta1, meta2],
        ) as mock_batch:
            page = client.fetch_backfill_page(None, 500)

        # cursor=None → first page; page_size clamped to the Gmail 500 max.
        mock_list.assert_called_once_with(None, 500)
        mock_batch.assert_called_once_with(["m1", "m2"])
        assert isinstance(page, BackfillPage)
        assert page.upserts == [meta1, meta2]
        assert page.next_cursor == "tok2"

    def test_forwards_cursor_as_page_token(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(
            client, "_list_message_ids_page", return_value=([], None),
        ) as mock_list, patch.object(client, "fetch_messages_metadata", return_value=[]):
            client.fetch_backfill_page("tok1", 500)
        mock_list.assert_called_once_with("tok1", 500)

    def test_next_cursor_none_when_mailbox_exhausted(self, client: GmailClient):
        client.service = MagicMock()
        with patch.object(client, "_list_message_ids_page", return_value=(["m1"], None)), \
             patch.object(client, "fetch_messages_metadata", return_value=[self._meta("m1")]):
            page = client.fetch_backfill_page("tok9", 500)
        assert page.next_cursor is None

    def test_dedupes_page_keeping_newest(self, client: GmailClient):
        client.service = MagicMock()
        stale, fresh = self._meta("m1", is_read=False), self._meta("m1", is_read=True)
        with patch.object(client, "_list_message_ids_page", return_value=(["m1", "m1"], None)), \
             patch.object(client, "fetch_messages_metadata", return_value=[stale, fresh]):
            page = client.fetch_backfill_page(None, 500)
        assert page.upserts == [fresh]

    def test_requires_authentication(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_backfill_page(None, 500)


class TestFetchMessagesMetadata:
    def test_guard_raises_not_authenticated(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_messages_metadata(["m1"])

    def test_empty_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        assert client.fetch_messages_metadata([]) == []

    def test_happy_path_returns_metadata(self, client: GmailClient):
        mock_service = MagicMock()
        client.service = mock_service

        with patch.object(client, "_execute_batch_get", return_value={
            "m1": {
                "id": "m1", "threadId": "t1", "labelIds": ["SENT"],
                "internalDate": "1704067200000",
                "payload": {"headers": [
                    {"name": "From", "value": "me@example.com"},
                    {"name": "Subject", "value": "Hello"},
                ]},
            },
        }):
            result = client.fetch_messages_metadata(["m1"])

        assert len(result) == 1
        assert result[0].provider_message_id == "m1"
        assert result[0].box == "SENT"
