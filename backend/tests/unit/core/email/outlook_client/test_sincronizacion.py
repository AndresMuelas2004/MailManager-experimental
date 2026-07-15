"""Tests espejo de ``outlook_client.sincronizacion`` (cursores, deltas, bootstrap e incremental)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.email.email_client import BackfillPage, EmailMetadata, LabelUpdate, SyncResult
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.outlook_client import OutlookClient, _DELTA_FOLDERS, _DELTA_SELECT_FIELDS
from core.email.outlook_client import sincronizacion
from core.email.outlook_client.sincronizacion import _BOOTSTRAP_SELECT_FIELDS

from ._helpers import _make_authenticated_client, _make_folder_cursor, _make_graph_message


@pytest.fixture(autouse=True)
def _clear_special_folder_cache():
    """Isolate the process-level special-folder cache between tests.

    ``_resolve_special_folder_ids`` now memoises its ``{folder_id: box}`` map in
    a module-level dict keyed by ``account_label`` (TTL). Every class here that
    drives the real resolution (``TestResolveSpecialFolderIds``,
    ``TestBootstrapEmailMetadata``, ``TestFetchMessagesMetadata``,
    ``TestBootstrapIsFullSync``) shares the label ``mb__outlook``, so without
    this reset the first test's cached map is served to the rest and their
    per-test Graph mocks never run — their assertions (partial / empty / failing
    folders) then read a stale full map instead. Clearing before AND after each
    test keeps the cache from leaking into or out of the file.
    """
    sincronizacion._SPECIAL_FOLDER_CACHE.clear()
    yield
    sincronizacion._SPECIAL_FOLDER_CACHE.clear()


# ── _encode_folder_cursors / _decode_folder_cursors ──────────────────


class TestEncodeFolderCursors:
    def test_roundtrip(self):
        original = {"inbox": "https://delta-inbox", "drafts": "https://delta-drafts"}
        encoded = OutlookClient._encode_folder_cursors(original)
        decoded = OutlookClient._decode_folder_cursors(encoded)
        assert decoded == original

    def test_encode_produces_valid_json(self):
        encoded = OutlookClient._encode_folder_cursors({"inbox": "link"})
        parsed = json.loads(encoded)
        assert parsed["v"] == 1
        assert parsed["folders"] == {"inbox": "link"}


class TestDecodeFolderCursors:
    def test_valid_cursor(self):
        cursor = json.dumps({"v": 1, "folders": {"inbox": "link"}})
        assert OutlookClient._decode_folder_cursors(cursor) == {"inbox": "link"}

    def test_legacy_url_cursor_returns_none(self):
        assert OutlookClient._decode_folder_cursors("https://old-delta-link") is None

    def test_malformed_json_returns_none(self):
        assert OutlookClient._decode_folder_cursors("not-json{") is None

    def test_missing_version_returns_none(self):
        cursor = json.dumps({"folders": {"inbox": "link"}})
        assert OutlookClient._decode_folder_cursors(cursor) is None

    def test_wrong_version_returns_none(self):
        cursor = json.dumps({"v": 2, "folders": {"inbox": "link"}})
        assert OutlookClient._decode_folder_cursors(cursor) is None

    def test_missing_folders_key_returns_none(self):
        cursor = json.dumps({"v": 1})
        assert OutlookClient._decode_folder_cursors(cursor) is None

    def test_folders_not_dict_returns_none(self):
        cursor = json.dumps({"v": 1, "folders": "not-a-dict"})
        assert OutlookClient._decode_folder_cursors(cursor) is None


# ── draft-exclusion + favourite select-field contracts ──────────────


class TestDraftExclusionContracts:
    def test_drafts_folder_dropped_from_delta_folders(self):
        # drafts sync into their own table; walking the drafts folder would leak
        # them into email_metadata, so it must be absent from the delta anchor.
        assert "drafts" not in _DELTA_FOLDERS

    def test_bootstrap_select_carries_isdraft_and_flag(self):
        # isDraft lets the client filter drafts; flag lets it capture favourites.
        assert "isDraft" in _BOOTSTRAP_SELECT_FIELDS
        assert "flag" in _BOOTSTRAP_SELECT_FIELDS

    def test_delta_select_carries_flag(self):
        # The delta $select must include flag so an out-of-band favourite change
        # that re-parses the full message picks it up.
        assert "flag" in _DELTA_SELECT_FIELDS


# ── _parse_graph_message ────────────────────────────────────────────


class TestParseGraphMessage:
    def test_full_message(self):
        msg = _make_graph_message()
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.provider_message_id == "msg1"
        assert result.thread_id == "conv1"
        assert result.from_email == "alice@example.com"
        assert result.from_name == "Alice"
        assert result.subject == "Hello"
        assert result.is_read is True
        assert result.box == "ALL_MAIL"
        assert result.received_at.year == 2025

    def test_missing_from_field(self):
        msg = _make_graph_message()
        del msg["from"]
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.from_email == ""
        assert result.from_name == ""

    def test_invalid_date_falls_back_to_deterministic_epoch(self):
        # Deterministic (never now()): received_at heads the id-reconciliation
        # identity, so the same payload must always parse to the same instant.
        msg = _make_graph_message(received="not-a-date")
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.received_at == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_missing_conversation_id(self):
        msg = _make_graph_message()
        del msg["conversationId"]
        result = OutlookClient._parse_graph_message(msg, "SPAM")
        assert result.thread_id == ""

    def test_is_read_false(self):
        msg = _make_graph_message(is_read=False)
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.is_read is False

    def test_box_passed_through(self):
        msg = _make_graph_message()
        assert OutlookClient._parse_graph_message(msg, "SPAM").box == "SPAM"
        assert OutlookClient._parse_graph_message(msg, "TRASH").box == "TRASH"
        assert OutlookClient._parse_graph_message(msg, "ALL_MAIL").box == "ALL_MAIL"
        assert OutlookClient._parse_graph_message(msg, "SENT").box == "SENT"
        # ARCHIVE is the new box, classified when parentFolderId == archive.
        assert OutlookClient._parse_graph_message(msg, "ARCHIVE").box == "ARCHIVE"

    def test_flagged_message_is_favorite(self):
        # flag.flagStatus == "flagged" is captured into is_favorite during sync
        # (flag is now in the $select of both bootstrap and delta).
        msg = _make_graph_message()
        msg["flag"] = {"flagStatus": "flagged"}
        assert OutlookClient._parse_graph_message(msg, "ALL_MAIL").is_favorite is True

    def test_not_flagged_message_is_not_favorite(self):
        msg = _make_graph_message()
        msg["flag"] = {"flagStatus": "notFlagged"}
        assert OutlookClient._parse_graph_message(msg, "ALL_MAIL").is_favorite is False

    def test_missing_flag_is_not_favorite(self):
        msg = _make_graph_message()
        msg.pop("flag", None)
        assert OutlookClient._parse_graph_message(msg, "ALL_MAIL").is_favorite is False


# ── fetch_email_metadata routing ────────────────────────────────────


class TestFetchEmailMetadata:
    def test_no_cursor_calls_bootstrap(self):
        client = _make_authenticated_client()
        expected = SyncResult(upserts=[], new_cursor="delta-link")
        with patch.object(client, "_bootstrap_email_metadata", return_value=expected) as mock:
            result = client.fetch_email_metadata(sync_cursor=None, max_total=100)
        mock.assert_called_once_with(100)
        assert result is expected

    def test_cursor_calls_incremental(self):
        client = _make_authenticated_client()
        expected = SyncResult(upserts=[], new_cursor="new-delta-link")
        with patch.object(client, "_incremental_email_metadata", return_value=expected) as mock:
            result = client.fetch_email_metadata(sync_cursor="old-delta-link")
        mock.assert_called_once_with("old-delta-link")
        assert result is expected

    def test_incremental_failure_falls_back_to_bootstrap(self):
        client = _make_authenticated_client()
        bootstrap_result = SyncResult(upserts=[], new_cursor="fresh-delta")
        with (
            patch.object(
                client,
                "_incremental_email_metadata",
                side_effect=EmailExternalAPIError("expired"),
            ),
            patch.object(
                client, "_bootstrap_email_metadata", return_value=bootstrap_result,
            ) as boot_mock,
        ):
            result = client.fetch_email_metadata(sync_cursor="expired-link", max_total=50)
        boot_mock.assert_called_once_with(50)
        assert result is bootstrap_result


# ── _bootstrap_email_metadata ───────────────────────────────────────


class TestResolveSpecialFolderIds:
    """Tests for _resolve_special_folder_ids — maps well-known folder names to Graph IDs."""

    def test_resolves_all_special_folders(self):
        client = _make_authenticated_client()

        def mock_graph(method, url, body=None):
            if "/mailFolders/sentitems?" in url:
                return {"id": "id-sent"}
            if "/mailFolders/deleteditems?" in url:
                return {"id": "id-trash"}
            if "/mailFolders/junkemail?" in url:
                return {"id": "id-spam"}
            if "/mailFolders/archive?" in url:
                return {"id": "id-archive"}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._resolve_special_folder_ids()

        # The well-known ``archive`` folder is now resolved alongside the other
        # three so messages in it classify as ARCHIVE (driven dynamically by
        # _FOLDER_TO_BOX).
        assert result == {
            "id-sent": "SENT",
            "id-trash": "TRASH",
            "id-spam": "SPAM",
            "id-archive": "ARCHIVE",
        }

    def test_partial_failure_returns_resolved_only(self):
        client = _make_authenticated_client()

        def mock_graph(method, url, body=None):
            if "/mailFolders/sentitems?" in url:
                raise EmailExternalAPIError("not found")
            if "/mailFolders/deleteditems?" in url:
                return {"id": "id-trash"}
            if "/mailFolders/junkemail?" in url:
                return {"id": "id-spam"}
            if "/mailFolders/archive?" in url:
                return {"id": "id-archive"}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._resolve_special_folder_ids()

        assert "SENT" not in result.values()
        assert result == {"id-trash": "TRASH", "id-spam": "SPAM", "id-archive": "ARCHIVE"}

    def test_all_fail_returns_empty(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request", side_effect=EmailExternalAPIError("down"),
        ):
            result = client._resolve_special_folder_ids()
        assert result == {}

    @staticmethod
    def _resolving_mock(calls):
        """A ``_graph_request`` side_effect that resolves the 4 special folders
        and records each URL into ``calls`` so a test can count Graph round trips."""

        def _mock(method, url, body=None):
            calls.append(url)
            if "/mailFolders/sentitems?" in url:
                return {"id": "id-sent"}
            if "/mailFolders/deleteditems?" in url:
                return {"id": "id-trash"}
            if "/mailFolders/junkemail?" in url:
                return {"id": "id-spam"}
            if "/mailFolders/archive?" in url:
                return {"id": "id-archive"}
            raise AssertionError(f"Unexpected URL: {url}")

        return _mock

    _RESOLVED = {
        "id-sent": "SENT",
        "id-trash": "TRASH",
        "id-spam": "SPAM",
        "id-archive": "ARCHIVE",
    }

    def test_second_open_reuses_cached_map_across_client_instances(self):
        # The OutlookClient is rebuilt per request, so the map must survive across
        # instances sharing an account_label — that is what makes a repeated
        # conversation open skip the 4 folder round trips (the hot-path fix).
        calls: list[str] = []
        first_client = _make_authenticated_client()
        with patch.object(first_client, "_graph_request", side_effect=self._resolving_mock(calls)):
            first = first_client._resolve_special_folder_ids()

        second_client = _make_authenticated_client()  # same account_label
        with patch.object(
            second_client, "_graph_request",
            side_effect=AssertionError("cache hit must issue zero Graph calls"),
        ):
            second = second_client._resolve_special_folder_ids()

        assert first == second == self._RESOLVED
        # Only the first resolution hit Graph (4 folders); the second was cached.
        assert len(calls) == 4

    def test_returns_a_fresh_copy_so_a_caller_cannot_mutate_the_cache(self):
        # A cache hit hands back a COPY — mutating the returned map must not
        # poison the next reader's view.
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=self._resolving_mock([])):
            first = client._resolve_special_folder_ids()
        first["id-sent"] = "TAMPERED"

        with patch.object(
            client, "_graph_request",
            side_effect=AssertionError("cache hit must issue zero Graph calls"),
        ):
            second = client._resolve_special_folder_ids()
        assert second == self._RESOLVED

    def test_distinct_account_labels_do_not_share_cache(self):
        # The cache is keyed by account_label, so a second account resolves on its
        # own — one account's map must never leak into another's.
        client_a = OutlookClient(account_label="mb__acct-a")
        client_a._access_token = "token"
        client_b = OutlookClient(account_label="mb__acct-b")
        client_b._access_token = "token"

        a_calls: list[str] = []
        b_calls: list[str] = []
        with patch.object(client_a, "_graph_request", side_effect=self._resolving_mock(a_calls)):
            client_a._resolve_special_folder_ids()
        with patch.object(client_b, "_graph_request", side_effect=self._resolving_mock(b_calls)):
            client_b._resolve_special_folder_ids()

        assert len(a_calls) == 4
        assert len(b_calls) == 4  # b resolved independently — no shared entry

    def test_expired_entry_triggers_re_resolution(self, monkeypatch):
        # The TTL is measured on time.monotonic(); once it elapses the entry is
        # stale and the next call re-resolves (the ids are stable, so this only
        # bounds the rare recreated-mailbox case).
        client = _make_authenticated_client()
        fake_now = {"t": 1000.0}
        monkeypatch.setattr(sincronizacion.time, "monotonic", lambda: fake_now["t"])

        calls: list[str] = []
        with patch.object(client, "_graph_request", side_effect=self._resolving_mock(calls)):
            client._resolve_special_folder_ids()               # caches at t=1000
            fake_now["t"] += sincronizacion._SPECIAL_FOLDER_TTL_S + 1  # entry now stale
            client._resolve_special_folder_ids()               # re-resolves

        assert len(calls) == 8  # 4 + 4: the stale entry forced a fresh resolution

    def test_total_failure_does_not_cache_and_retries_next_call(self):
        # An empty map means every folder call failed (a transient blip). Caching
        # it would misclassify every message as ALL_MAIL for a whole TTL, so the
        # empty result is deliberately NOT cached and the next call retries.
        client = _make_authenticated_client()
        call_count = {"n": 0}

        def _all_fail(method, url, body=None):
            call_count["n"] += 1
            raise EmailExternalAPIError("down")

        with patch.object(client, "_graph_request", side_effect=_all_fail):
            first = client._resolve_special_folder_ids()
            assert first == {}
            assert client._account_label not in sincronizacion._SPECIAL_FOLDER_CACHE
            second = client._resolve_special_folder_ids()
            assert second == {}

        # Both calls retried all 4 folders — the degraded map was never pinned.
        assert call_count["n"] == 8

    def test_partial_failure_does_not_cache_and_retries_next_call(self):
        # A PARTIAL map (one folder lookup hit a transient error) must not be
        # cached either: pinning it for a TTL would classify that folder's
        # messages as ALL_MAIL for an hour, and the conversation lazy-sync
        # would rewrite the canonical rows' box with that wrong value (e.g.
        # SENT thread members surfacing in the inbox listing).
        client = _make_authenticated_client()
        calls: list[str] = []

        def _sent_fails(method, url, body=None):
            calls.append(url)
            if "/mailFolders/sentitems?" in url:
                raise EmailExternalAPIError("throttled")
            if "/mailFolders/deleteditems?" in url:
                return {"id": "id-trash"}
            if "/mailFolders/junkemail?" in url:
                return {"id": "id-spam"}
            if "/mailFolders/archive?" in url:
                return {"id": "id-archive"}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(client, "_graph_request", side_effect=_sent_fails):
            first = client._resolve_special_folder_ids()
            # The partial result still serves THIS call (best-effort)…
            assert first == {"id-trash": "TRASH", "id-spam": "SPAM", "id-archive": "ARCHIVE"}
            # …but is never pinned; the next call re-resolves and self-heals.
            assert client._account_label not in sincronizacion._SPECIAL_FOLDER_CACHE

        healed_calls: list[str] = []
        with patch.object(client, "_graph_request", side_effect=self._resolving_mock(healed_calls)):
            second = client._resolve_special_folder_ids()
        assert second == self._RESOLVED
        assert len(healed_calls) == 4


class TestFetchRecentMessages:
    """Tests for _fetch_recent_messages — GET /me/messages across all folders."""

    _FOLDER_MAP = {"id-sent": "SENT", "id-trash": "TRASH", "id-spam": "SPAM"}

    def test_classifies_by_parent_folder(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="m-inbox", parent_folder_id="id-inbox"),
            _make_graph_message(msg_id="m-sent", parent_folder_id="id-sent"),
            _make_graph_message(msg_id="m-trash", parent_folder_id="id-trash"),
            _make_graph_message(msg_id="m-spam", parent_folder_id="id-spam"),
            _make_graph_message(msg_id="m-custom", parent_folder_id="id-custom"),
        ]
        with patch.object(client, "_graph_request", return_value={"value": messages}):
            result = client._fetch_recent_messages(500, self._FOLDER_MAP)

        by_id = {m.provider_message_id: m for m in result}
        assert by_id["m-inbox"].box == "ALL_MAIL"
        assert by_id["m-sent"].box == "SENT"
        assert by_id["m-trash"].box == "TRASH"
        assert by_id["m-spam"].box == "SPAM"
        assert by_id["m-custom"].box == "ALL_MAIL"

    def test_respects_max_total(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id=f"m{i}", parent_folder_id="id-inbox")
            for i in range(10)
        ]
        with patch.object(client, "_graph_request", return_value={"value": messages}):
            result = client._fetch_recent_messages(3, self._FOLDER_MAP)
        assert len(result) == 3

    def test_follows_pagination(self):
        client = _make_authenticated_client()
        page1 = {
            "value": [_make_graph_message(msg_id="m1", parent_folder_id="id-inbox")],
            "@odata.nextLink": "https://next-page",
        }
        page2 = {
            "value": [_make_graph_message(msg_id="m2", parent_folder_id="id-inbox")],
        }
        with patch.object(client, "_graph_request", side_effect=[page1, page2]):
            result = client._fetch_recent_messages(500, self._FOLDER_MAP)
        assert len(result) == 2

    def test_missing_parent_folder_defaults_to_all_mail(self):
        client = _make_authenticated_client()
        messages = [_make_graph_message(msg_id="m1")]  # no parentFolderId
        with patch.object(client, "_graph_request", return_value={"value": messages}):
            result = client._fetch_recent_messages(500, self._FOLDER_MAP)
        assert result[0].box == "ALL_MAIL"

    def test_skips_drafts(self):
        # A draft surfaced by GET /me/messages is filtered client-side (drafts
        # sync into their own table, never email_metadata).
        client = _make_authenticated_client()
        draft = _make_graph_message(msg_id="d1", parent_folder_id="id-inbox")
        draft["isDraft"] = True
        normal = _make_graph_message(msg_id="m1", parent_folder_id="id-inbox")
        with patch.object(client, "_graph_request", return_value={"value": [draft, normal]}):
            result = client._fetch_recent_messages(500, self._FOLDER_MAP)
        assert [m.provider_message_id for m in result] == ["m1"]


class TestBootstrapEmailMetadata:
    """Integration tests for _bootstrap_email_metadata — the full Path 1 flow."""

    _SENT_ID = "folder-id-sent"
    _TRASH_ID = "folder-id-trash"
    _SPAM_ID = "folder-id-spam"
    _ARCHIVE_ID = "folder-id-archive"

    def _make_bootstrap_mock(self, messages, *, fail_folders=None):
        """Return a side_effect for _graph_request that handles all bootstrap phases."""
        # ``archive`` joins the special folders resolved on bootstrap so a
        # message whose parentFolderId is the archive folder classifies as
        # ARCHIVE (the same dynamic _FOLDER_TO_BOX iteration as the others).
        folder_ids = {
            "sentitems": self._SENT_ID,
            "deleteditems": self._TRASH_ID,
            "junkemail": self._SPAM_ID,
            "archive": self._ARCHIVE_ID,
        }
        fail_folders = fail_folders or set()

        def mock_graph(method, url, body=None):
            # Phase 1: resolve special folder IDs
            for name, fid in folder_ids.items():
                if f"/mailFolders/{name}?" in url and "$select=id" in url:
                    if name in fail_folders:
                        raise EmailExternalAPIError(f"{name} not found")
                    return {"id": fid}

            # Phase 2: GET /me/messages (bootstrap data)
            if "/me/messages?" in url and "/mailFolders/" not in url:
                return {"value": messages}

            # Phase 3: per-folder delta cursor initialization
            for folder in _DELTA_FOLDERS:
                if f"/mailFolders/{folder}/messages/delta" in url:
                    if folder in fail_folders:
                        raise EmailExternalAPIError(f"delta {folder} failed")
                    return {
                        "value": [],
                        "@odata.deltaLink": f"https://delta-{folder}",
                    }

            raise AssertionError(f"Unexpected URL: {url}")

        return mock_graph

    def test_messages_classified_by_folder(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="inbox-msg", parent_folder_id="folder-inbox"),
            _make_graph_message(msg_id="sent-msg", parent_folder_id=self._SENT_ID),
            _make_graph_message(msg_id="trash-msg", parent_folder_id=self._TRASH_ID),
            _make_graph_message(msg_id="spam-msg", parent_folder_id=self._SPAM_ID),
            _make_graph_message(msg_id="archive-msg", parent_folder_id=self._ARCHIVE_ID),
            _make_graph_message(msg_id="custom-msg", parent_folder_id="folder-custom"),
        ]
        mock = self._make_bootstrap_mock(messages)
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=500)

        by_id = {u.provider_message_id: u for u in result.upserts}
        assert by_id["inbox-msg"].box == "ALL_MAIL"
        assert by_id["sent-msg"].box == "SENT"
        assert by_id["trash-msg"].box == "TRASH"
        assert by_id["spam-msg"].box == "SPAM"
        assert by_id["archive-msg"].box == "ARCHIVE"
        assert by_id["custom-msg"].box == "ALL_MAIL"

    def test_max_total_limit(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id=f"msg-{i}", parent_folder_id="folder-inbox")
            for i in range(10)
        ]
        mock = self._make_bootstrap_mock(messages)
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=3)

        assert len(result.upserts) == 3

    def test_delta_cursors_initialized(self):
        client = _make_authenticated_client()
        messages = [_make_graph_message(parent_folder_id="folder-inbox")]
        mock = self._make_bootstrap_mock(messages)
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=500)

        cursor = json.loads(result.new_cursor)
        assert cursor["v"] == 1
        assert len(cursor["folders"]) == len(_DELTA_FOLDERS)

    def test_folder_resolution_failure_defaults_to_all_mail(self):
        """If sentitems ID can't be resolved, those messages default to ALL_MAIL."""
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="sent-msg", parent_folder_id=self._SENT_ID),
        ]
        mock = self._make_bootstrap_mock(messages, fail_folders={"sentitems"})
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=500)

        by_id = {u.provider_message_id: u for u in result.upserts}
        assert by_id["sent-msg"].box == "ALL_MAIL"

    def test_delta_init_failure_still_returns_messages(self):
        """If delta init fails for a folder, messages are still returned."""
        client = _make_authenticated_client()
        messages = [_make_graph_message(parent_folder_id="folder-inbox")]
        mock = self._make_bootstrap_mock(messages, fail_folders={"inbox"})
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=500)

        assert len(result.upserts) == 1
        cursor = json.loads(result.new_cursor)
        assert "inbox" not in cursor["folders"]

    def test_api_completely_down_raises_error(self):
        """If the Graph API is completely unreachable, the error propagates."""
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request", side_effect=EmailExternalAPIError("all down"),
        ):
            with pytest.raises(EmailExternalAPIError):
                client._bootstrap_email_metadata(max_total=500)

    def test_duplicate_messages_collapse_to_last_upsert(self):
        """GET /me/messages pagination can repeat a message when the mailbox
        shifts between pages; bootstrap dedupes keeping the newest state so the
        batch persistence never sees the same key twice."""
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="dup", parent_folder_id="folder-inbox", is_read=False),
            _make_graph_message(msg_id="dup", parent_folder_id="folder-inbox", is_read=True),
        ]
        mock = self._make_bootstrap_mock(messages)
        with patch.object(client, "_graph_request", side_effect=mock):
            result = client._bootstrap_email_metadata(max_total=500)

        assert len(result.upserts) == 1
        assert result.upserts[0].provider_message_id == "dup"
        assert result.upserts[0].is_read is True


# ── _incremental_email_metadata ─────────────────────────────────────


class TestIncrementalEmailMetadata:
    def test_new_messages_become_upserts(self):
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [_make_graph_message(msg_id="new1")],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert any(u.provider_message_id == "new1" for u in result.upserts)
        assert result.deletes == []
        new_cursors = json.loads(result.new_cursor)
        assert new_cursors["folders"]["inbox"] == "https://new-delta-inbox"

    def test_removed_messages_become_deletes(self):
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [{"id": "del1", "@removed": {"reason": "deleted"}}],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert result.deletes == ["del1"]

    def test_mixed_upserts_and_deletes(self):
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [
                        _make_graph_message(msg_id="new1"),
                        {"id": "del1", "@removed": {"reason": "deleted"}},
                    ],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert len(result.upserts) == 1
        assert result.deletes == ["del1"]

    def test_pagination(self):
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()
        next_url = "https://next-page"

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url and url != next_url:
                return {
                    "value": [_make_graph_message(msg_id="m1")],
                    "@odata.nextLink": next_url,
                }
            if url == next_url:
                return {
                    "value": [_make_graph_message(msg_id="m2")],
                    "@odata.deltaLink": "https://final-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        inbox_msgs = [u for u in result.upserts if u.provider_message_id in ("m1", "m2")]
        assert len(inbox_msgs) == 2

    def test_empty_delta(self):
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert result.upserts == []
        assert result.deletes == []

    def test_legacy_cursor_raises_for_fallback(self):
        """A legacy (non-JSON) cursor triggers EmailExternalAPIError for fallback to bootstrap."""
        client = _make_authenticated_client()
        with pytest.raises(EmailExternalAPIError, match="legacy"):
            client._incremental_email_metadata("https://old-style-delta-link")

    def test_partial_folder_failure_keeps_previous_cursors(self):
        """If one folder fails, its previous cursor is preserved in the new cursor."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                raise EmailExternalAPIError("inbox expired")
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        new_cursors = json.loads(result.new_cursor)
        # inbox keeps its old cursor
        assert new_cursors["folders"]["inbox"] == "https://delta-inbox"
        # other folders got updated
        assert new_cursors["folders"]["sentitems"] == "https://new-delta-sentitems"

    def test_all_folders_fail_raises_error(self):
        """If ALL folders fail, raise to trigger bootstrap fallback."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        with patch.object(
            client, "_graph_request", side_effect=EmailExternalAPIError("all down"),
        ):
            with pytest.raises(EmailExternalAPIError, match="all folder"):
                client._incremental_email_metadata(cursor)

    @pytest.mark.parametrize(
        "flag_status, expected",
        [("flagged", True), ("notFlagged", False)],
    )
    def test_partial_delta_carries_favorite_when_flag_present(self, flag_status, expected):
        # A partial delta object (no ``from`` field — only isRead/labels changed)
        # becomes a LabelUpdate. When ``flag`` IS present in the partial payload,
        # its state rides through as a concrete bool.
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [{"id": "m1", "isRead": True, "flag": {"flagStatus": flag_status}}],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        lu = next(lu for lu in result.label_updates if lu.provider_message_id == "m1")
        assert lu.is_favorite is expected

    def test_partial_delta_leaves_favorite_none_without_flag(self):
        # Without ``flag`` in the partial payload, is_favorite is None so the
        # COALESCE in UPDATE_LABELS_BATCH keeps the stored favourite untouched.
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [{"id": "m1", "isRead": True}],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        lu = next(lu for lu in result.label_updates if lu.provider_message_id == "m1")
        assert lu.is_favorite is None

    def test_skips_stale_drafts_cursor_inherited_from_pre_change_sync(self):
        # An account synced before drafts were excluded still carries a ``drafts``
        # cursor in its stored sync_cursor. The incremental must NOT walk it and
        # must drop it from the new cursor so drafts stop leaking without waiting
        # for a re-bootstrap.
        client = _make_authenticated_client()
        cursor = OutlookClient._encode_folder_cursors({
            "inbox": "https://delta-inbox",
            "drafts": "https://delta-drafts",
        })

        def mock_graph(method, url, body=None):
            if "delta-drafts" in url:
                raise AssertionError("the stale drafts cursor must not be walked")
            return {"value": [], "@odata.deltaLink": "https://new-delta-inbox"}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        new_cursors = json.loads(result.new_cursor)
        assert "drafts" not in new_cursors["folders"]
        assert new_cursors["folders"]["inbox"] == "https://new-delta-inbox"

    def test_box_mapping_from_folder_name(self):
        """deleteditems→TRASH, junkemail→SPAM, sentitems→SENT, archive→ARCHIVE, others→ALL_MAIL."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            for folder in _DELTA_FOLDERS:
                if f"delta-{folder}" in url:
                    return {
                        "value": [_make_graph_message(msg_id=f"msg-{folder}")],
                        "@odata.deltaLink": f"https://new-delta-{folder}",
                    }
            return {"value": [], "@odata.deltaLink": url}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        by_id = {u.provider_message_id: u for u in result.upserts}
        assert by_id["msg-deleteditems"].box == "TRASH"
        assert by_id["msg-junkemail"].box == "SPAM"
        assert by_id["msg-inbox"].box == "ALL_MAIL"
        assert by_id["msg-sentitems"].box == "SENT"
        # ``drafts`` is no longer a delta folder (drafts sync into their own
        # table), so it is absent from _DELTA_FOLDERS and never walked here.
        # The archive folder delta now classifies into ARCHIVE (out-of-band
        # archives surface on the next sync).
        assert by_id["msg-archive"].box == "ARCHIVE"

    def test_partial_delta_emits_label_update(self):
        """Delta returning a partial message (no 'from') populates label_updates, not upserts."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [{"id": "partial1", "isRead": True}],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert result.upserts == []
        assert len(result.label_updates) == 1
        assert result.label_updates[0].provider_message_id == "partial1"
        assert result.label_updates[0].is_read is True
        assert result.label_updates[0].box == "ALL_MAIL"

    def test_mixed_full_and_partial_messages(self):
        """Delta with both full and partial messages routes them correctly."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [
                        _make_graph_message(msg_id="full1"),
                        {"id": "partial1", "isRead": False},
                    ],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert len(result.upserts) == 1
        assert result.upserts[0].provider_message_id == "full1"
        assert len(result.label_updates) == 1
        assert result.label_updates[0].provider_message_id == "partial1"
        assert result.label_updates[0].is_read is False

    def test_duplicate_message_in_delta_window_collapses_to_last_upsert(self):
        """Graph delta can emit the same message twice in one window (a message
        that changed twice since the stored cursor). The client must collapse
        the repeats keeping the newest state — the batch persistence rejects a
        batch touching the same key twice, and the crash would leave the cursor
        stuck replaying the same window on every sync."""
        client = _make_authenticated_client()
        cursor = _make_folder_cursor()

        def mock_graph(method, url, body=None):
            if "delta-inbox" in url:
                return {
                    "value": [
                        _make_graph_message(msg_id="dup1", is_read=False),
                        _make_graph_message(msg_id="dup1", is_read=True),
                    ],
                    "@odata.deltaLink": "https://new-delta-inbox",
                }
            return {"value": [], "@odata.deltaLink": url.replace("delta-", "new-delta-")}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._incremental_email_metadata(cursor)

        assert len(result.upserts) == 1
        assert result.upserts[0].provider_message_id == "dup1"
        assert result.upserts[0].is_read is True


# ── _fetch_folder_delta ──────────────────────────────────────────


class TestFetchFolderDelta:
    def test_single_page_returns_delta_link(self):
        client = _make_authenticated_client()
        msg = {
            "id": "msg1",
            "subject": "Hello",
            "from": {"emailAddress": {"address": "a@b.com", "name": "A"}},
            "receivedDateTime": "2025-01-01T10:00:00Z",
            "isRead": False,
            "conversationId": "conv1",
        }
        response = {"value": [msg], "@odata.deltaLink": "https://delta-link"}
        with patch.object(client, "_graph_request", return_value=response):
            upserts = []
            deletes = []
            delta = client._fetch_folder_delta("inbox", "https://start-url", upserts, deletes)
        assert delta == "https://delta-link"
        assert len(upserts) == 1
        assert upserts[0].provider_message_id == "msg1"
        assert deletes == []

    def test_pagination(self):
        client = _make_authenticated_client()
        msg1 = {
            "id": "msg1", "subject": "S1",
            "from": {"emailAddress": {"address": "a@b.com", "name": "A"}},
            "receivedDateTime": "2025-01-01T10:00:00Z", "isRead": False, "conversationId": "c1",
        }
        msg2 = {
            "id": "msg2", "subject": "S2",
            "from": {"emailAddress": {"address": "b@c.com", "name": "B"}},
            "receivedDateTime": "2025-01-02T10:00:00Z", "isRead": True, "conversationId": "c2",
        }
        page1 = {"value": [msg1], "@odata.nextLink": "https://next"}
        page2 = {"value": [msg2], "@odata.deltaLink": "https://delta-final"}
        with patch.object(client, "_graph_request", side_effect=[page1, page2]):
            upserts = []
            deletes = []
            delta = client._fetch_folder_delta("inbox", "https://start", upserts, deletes)
        assert delta == "https://delta-final"
        assert len(upserts) == 2

    def test_max_collect_stops_collecting(self):
        client = _make_authenticated_client()
        msgs = [
            {
                "id": f"msg{i}", "subject": f"S{i}",
                "from": {"emailAddress": {"address": f"{i}@b.com", "name": f"U{i}"}},
                "receivedDateTime": "2025-01-01T10:00:00Z", "isRead": False, "conversationId": f"c{i}",
            }
            for i in range(3)
        ]
        page1 = {"value": msgs[:2], "@odata.nextLink": "https://next"}
        page2 = {"value": [msgs[2]], "@odata.deltaLink": "https://delta"}
        with patch.object(client, "_graph_request", side_effect=[page1, page2]):
            upserts = []
            deletes = []
            delta = client._fetch_folder_delta("inbox", "https://start", upserts, deletes, max_collect=1)
        assert delta == "https://delta"
        assert len(upserts) == 1

    def test_removed_message_added_to_deletes(self):
        client = _make_authenticated_client()
        msg = {"id": "removed1", "@removed": {"reason": "deleted"}}
        response = {"value": [msg], "@odata.deltaLink": "https://delta"}
        with patch.object(client, "_graph_request", return_value=response):
            upserts = []
            deletes = []
            client._fetch_folder_delta("inbox", "https://start", upserts, deletes)
        assert upserts == []
        assert deletes == ["removed1"]

    def test_partial_message_becomes_label_update(self):
        """Message without 'from' key is appended to label_updates, not upserts."""
        client = _make_authenticated_client()
        partial_msg = {"id": "partial1", "isRead": True}
        response = {"value": [partial_msg], "@odata.deltaLink": "https://delta"}
        with patch.object(client, "_graph_request", return_value=response):
            upserts: list[EmailMetadata] = []
            deletes: list[str] = []
            label_updates: list[LabelUpdate] = []
            client._fetch_folder_delta("inbox", "https://start", upserts, deletes, label_updates=label_updates)
        assert upserts == []
        assert len(label_updates) == 1
        assert label_updates[0].provider_message_id == "partial1"
        assert label_updates[0].is_read is True
        assert label_updates[0].box == "ALL_MAIL"

    def test_full_message_remains_upsert_when_label_updates_provided(self):
        """Message with 'from' still goes to upserts even when label_updates list is provided."""
        client = _make_authenticated_client()
        full_msg = _make_graph_message(msg_id="full1")
        response = {"value": [full_msg], "@odata.deltaLink": "https://delta"}
        with patch.object(client, "_graph_request", return_value=response):
            upserts: list[EmailMetadata] = []
            deletes: list[str] = []
            label_updates: list[LabelUpdate] = []
            client._fetch_folder_delta("inbox", "https://start", upserts, deletes, label_updates=label_updates)
        assert len(upserts) == 1
        assert upserts[0].provider_message_id == "full1"
        assert label_updates == []

    def test_label_updates_none_falls_through_to_upsert(self):
        """When label_updates is None (Path 1 default), partial messages are parsed as upserts."""
        client = _make_authenticated_client()
        partial_msg = {"id": "partial1", "isRead": False}
        response = {"value": [partial_msg], "@odata.deltaLink": "https://delta"}
        with patch.object(client, "_graph_request", return_value=response):
            upserts: list[EmailMetadata] = []
            deletes: list[str] = []
            client._fetch_folder_delta("inbox", "https://start", upserts, deletes)
        # Without label_updates list, partial messages go through normal upsert parsing.
        # This is fine because Path 1 (bootstrap) always receives full messages.
        assert len(upserts) == 1
        assert upserts[0].provider_message_id == "partial1"
        assert upserts[0].from_email == ""


# ── _prime_folder_delta_cursors (shared by bootstrap + backfill anchor) ──


class TestPrimeFolderDeltaCursors:
    def _delta_mock(self, *, fail_folders=None):
        fail_folders = fail_folders or set()

        def mock_graph(method, url, body=None):
            for folder in _DELTA_FOLDERS:
                if f"/mailFolders/{folder}/messages/delta" in url:
                    if folder in fail_folders:
                        raise EmailExternalAPIError(f"delta {folder} failed")
                    return {"value": [], "@odata.deltaLink": f"https://delta-{folder}"}
            raise AssertionError(f"Unexpected URL: {url}")

        return mock_graph

    def test_primes_every_delta_folder(self):
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=self._delta_mock()):
            cursors = client._prime_folder_delta_cursors()
        assert set(cursors) == set(_DELTA_FOLDERS)
        assert cursors["inbox"] == "https://delta-inbox"

    def test_failing_folder_is_omitted(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request", side_effect=self._delta_mock(fail_folders={"inbox"}),
        ):
            cursors = client._prime_folder_delta_cursors()
        assert "inbox" not in cursors
        assert "sentitems" in cursors


# ── _folder_id_to_box_cached (resolve once, reuse across waves) ──────


class TestFolderIdToBoxCached:
    def test_resolves_once_and_caches(self):
        client = _make_authenticated_client()
        resolved = {"id-sent": "SENT"}
        with patch.object(
            client, "_resolve_special_folder_ids", return_value=resolved,
        ) as mock_resolve:
            first = client._folder_id_to_box_cached()
            second = client._folder_id_to_box_cached()
        assert first == resolved
        assert second is first
        # Resolved exactly once — the instance cache survives across waves.
        mock_resolve.assert_called_once()
        assert client._backfill_folder_map == resolved


# ── capture_backfill_anchor (Outlook: primed per-folder delta links) ──


class TestCaptureBackfillAnchor:
    def test_returns_encoded_primed_cursors(self):
        client = _make_authenticated_client()

        def mock_graph(method, url, body=None):
            for folder in _DELTA_FOLDERS:
                if f"/mailFolders/{folder}/messages/delta" in url:
                    return {"value": [], "@odata.deltaLink": f"https://delta-{folder}"}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            anchor = client.capture_backfill_anchor()

        decoded = OutlookClient._decode_folder_cursors(anchor)
        assert set(decoded) == set(_DELTA_FOLDERS)
        assert decoded["inbox"] == "https://delta-inbox"

    def test_requires_authentication(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.capture_backfill_anchor()


# ── fetch_backfill_page (Outlook: inline-metadata page via retrying transport) ──


class TestFetchBackfillPage:
    _FOLDER_MAP = {"id-sent": "SENT", "id-archive": "ARCHIVE"}

    def test_first_page_builds_ordered_query_via_retrying_transport(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="m1", parent_folder_id="id-inbox"),
            _make_graph_message(msg_id="m2", parent_folder_id="id-sent"),
        ]
        response = {"value": messages, "@odata.nextLink": "https://next-page"}

        with patch.object(
            client, "_folder_id_to_box_cached", return_value=self._FOLDER_MAP,
        ), patch.object(
            client, "_graph_request_json_with_retries", return_value=response,
        ) as mock_retry, patch.object(
            client, "_graph_request",
            side_effect=AssertionError("backfill must not use the non-retrying transport"),
        ):
            page = client.fetch_backfill_page(None, 5000)

        # The GET is routed through the Retry-After-aware transport, NOT the
        # plain _graph_request (which the assertion side_effect would trip).
        assert mock_retry.call_count == 1
        method, url = mock_retry.call_args[0][0], mock_retry.call_args[0][1]
        assert method == "GET"
        assert "$orderby=receivedDateTime+desc" in url
        # $top is clamped to the Graph 1000-per-page maximum.
        assert "$top=1000" in url

        assert isinstance(page, BackfillPage)
        by_id = {u.provider_message_id: u for u in page.upserts}
        assert by_id["m1"].box == "ALL_MAIL"  # unknown parent folder → ALL_MAIL
        assert by_id["m2"].box == "SENT"
        assert page.next_cursor == "https://next-page"

    def test_subsequent_page_uses_cursor_as_url(self):
        client = _make_authenticated_client()
        response = {"value": [], "@odata.deltaLink": "ignored"}
        with patch.object(client, "_folder_id_to_box_cached", return_value=self._FOLDER_MAP), \
             patch.object(
                 client, "_graph_request_json_with_retries", return_value=response,
             ) as mock_retry:
            page = client.fetch_backfill_page("https://next-page-cursor", 1000)
        # The opaque @odata.nextLink cursor is followed literally.
        assert mock_retry.call_args[0][1] == "https://next-page-cursor"
        # No nextLink in the response → mailbox exhausted.
        assert page.next_cursor is None

    def test_dedupes_page_keeping_newest(self):
        client = _make_authenticated_client()
        messages = [
            _make_graph_message(msg_id="dup", parent_folder_id="id-inbox", is_read=False),
            _make_graph_message(msg_id="dup", parent_folder_id="id-inbox", is_read=True),
        ]
        response = {"value": messages}
        with patch.object(client, "_folder_id_to_box_cached", return_value=self._FOLDER_MAP), \
             patch.object(client, "_graph_request_json_with_retries", return_value=response):
            page = client.fetch_backfill_page(None, 1000)
        assert len(page.upserts) == 1
        assert page.upserts[0].is_read is True

    def test_requires_authentication(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_backfill_page(None, 1000)

    def test_skips_drafts(self):
        # Drafts belong to their own table — the backfill filters them client-side.
        client = _make_authenticated_client()
        draft = _make_graph_message(msg_id="d1", parent_folder_id="id-inbox")
        draft["isDraft"] = True
        normal = _make_graph_message(msg_id="m1", parent_folder_id="id-inbox")
        response = {"value": [draft, normal]}
        with patch.object(client, "_folder_id_to_box_cached", return_value=self._FOLDER_MAP), \
             patch.object(client, "_graph_request_json_with_retries", return_value=response):
            page = client.fetch_backfill_page(None, 1000)
        assert [u.provider_message_id for u in page.upserts] == ["m1"]


# ── verify_message_existence ───────────────────────────────────────


class TestVerifyMessageExistence:
    def test_not_authenticated_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailNotAuthenticatedError):
            client.verify_message_existence(["m1"])

    def test_empty_list_returns_empty(self):
        client = _make_authenticated_client()
        assert client.verify_message_existence([]) == []

    def test_all_exist(self):
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", return_value={"id": "m1"}):
            result = client.verify_message_existence(["m1"])
        assert result == ["m1"]

    def test_some_missing(self):
        client = _make_authenticated_client()
        responses = [
            {"id": "m1"},
            EmailExternalAPIError("404"),
            {"id": "m3"},
        ]
        side_effects = []
        for r in responses:
            if isinstance(r, Exception):
                side_effects.append(r)
            else:
                side_effects.append(r)

        def mock_graph(method, url):
            resp = side_effects.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.verify_message_existence(["m1", "m2", "m3"])
        assert result == ["m1", "m3"]

    def test_all_missing(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request", side_effect=EmailExternalAPIError("404"),
        ):
            result = client.verify_message_existence(["m1", "m2"])
        assert result == []


# ── is_full_sync flag ──────────────────────────────────────────────


class TestBootstrapIsFullSync:
    def test_bootstrap_sets_is_full_sync_true(self):
        client = _make_authenticated_client()
        messages = [_make_graph_message(parent_folder_id="folder-inbox")]

        _SENT_ID = "folder-id-sent"
        _TRASH_ID = "folder-id-trash"
        _SPAM_ID = "folder-id-spam"
        _ARCHIVE_ID = "folder-id-archive"
        folder_ids = {
            "sentitems": _SENT_ID,
            "deleteditems": _TRASH_ID,
            "junkemail": _SPAM_ID,
            "archive": _ARCHIVE_ID,
        }

        def mock_graph(method, url, body=None):
            for name, fid in folder_ids.items():
                if f"/mailFolders/{name}?" in url and "$select=id" in url:
                    return {"id": fid}
            if "/me/messages?" in url and "/mailFolders/" not in url:
                return {"value": messages}
            for folder in _DELTA_FOLDERS:
                if f"/mailFolders/{folder}/messages/delta" in url:
                    return {"value": [], "@odata.deltaLink": f"https://delta-{folder}"}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._bootstrap_email_metadata(max_total=500)
        assert result.is_full_sync is True


# ── fetch_messages_metadata ─────────────────────────────────────


class TestFetchMessagesMetadata:
    def test_guard_raises_not_authenticated(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_messages_metadata(["m1"])

    def test_empty_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        assert client.fetch_messages_metadata([]) == []

    def test_happy_path_resolves_box(self, client: OutlookClient):
        client._access_token = "tok"
        # Folder ids resolved per well-known name (URL-driven, not by call
        # order) so adding ``archive`` to _FOLDER_TO_BOX cannot misalign the
        # mapping. The message lives in sentitems → SENT.
        folder_ids = {
            "deleteditems": "folder-trash",
            "junkemail": "folder-spam",
            "sentitems": "folder-sent",
            "archive": "folder-archive",
        }
        message_response = {
            "id": "m1",
            "conversationId": "c1",
            "from": {"emailAddress": {"address": "x@test.com", "name": "X"}},
            "subject": "Test",
            "receivedDateTime": "2024-01-01T12:00:00Z",
            "isRead": True,
            "parentFolderId": "folder-sent",
        }

        def mock_graph(method, url, body=None):
            if "mailFolders" in url:
                for name, fid in folder_ids.items():
                    if f"/mailFolders/{name}?" in url:
                        return {"id": fid}
                raise AssertionError(f"Unexpected folder URL: {url}")
            return message_response

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.fetch_messages_metadata(["m1"])

        assert len(result) == 1
        assert result[0].box == "SENT"
        assert result[0].provider_message_id == "m1"

    def test_resolves_archive_box(self, client: OutlookClient):
        client._access_token = "tok"
        folder_ids = {
            "deleteditems": "folder-trash",
            "junkemail": "folder-spam",
            "sentitems": "folder-sent",
            "archive": "folder-archive",
        }
        message_response = {
            "id": "m2",
            "conversationId": "c2",
            "from": {"emailAddress": {"address": "x@test.com", "name": "X"}},
            "subject": "Archived",
            "receivedDateTime": "2024-01-01T12:00:00Z",
            "isRead": True,
            "parentFolderId": "folder-archive",
        }

        def mock_graph(method, url, body=None):
            if "mailFolders" in url:
                for name, fid in folder_ids.items():
                    if f"/mailFolders/{name}?" in url:
                        return {"id": fid}
                raise AssertionError(f"Unexpected folder URL: {url}")
            return message_response

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.fetch_messages_metadata(["m2"])

        assert len(result) == 1
        assert result[0].box == "ARCHIVE"

    def test_failed_message_silently_skipped(self, client: OutlookClient):
        client._access_token = "tok"

        call_count = [0]
        def mock_graph(method, url, body=None):
            nonlocal call_count
            call_count[0] += 1
            if "mailFolders" in url:
                return {"id": f"folder-{call_count[0]}"}
            raise EmailExternalAPIError("Not found")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.fetch_messages_metadata(["m1"])

        assert result == []


class TestParseGraphMessageSentDateFallback:
    """received_at derivation lives INSIDE _parse_graph_message for EVERY
    endpoint (delta / bootstrap / conversation): receivedDateTime →
    sentDateTime → deterministic epoch. One shared chain is load-bearing for
    the conversation id reconciliation, whose identity starts with
    received_at."""

    def test_missing_received_date_falls_back_to_sent_date(self):
        msg = _make_graph_message()
        del msg["receivedDateTime"]
        msg["sentDateTime"] = "2025-06-02T08:00:00Z"
        result = OutlookClient._parse_graph_message(msg, "SENT")
        assert result.received_at == datetime(2025, 6, 2, 8, 0, tzinfo=timezone.utc)

    def test_missing_both_dates_falls_back_to_deterministic_epoch(self):
        # No receivedDateTime and no sentDateTime → the deterministic epoch,
        # identical on every parse (never now()).
        msg = _make_graph_message()
        del msg["receivedDateTime"]
        result = OutlookClient._parse_graph_message(msg, "SENT")
        assert result.received_at == datetime(1970, 1, 1, tzinfo=timezone.utc)
