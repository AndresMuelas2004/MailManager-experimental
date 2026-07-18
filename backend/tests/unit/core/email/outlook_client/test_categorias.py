"""Tests espejo de ``outlook_client.categorias`` (categories = MISSELA folders).

Applying/removing a category is a read-modify-write because a Graph PATCH
REPLACES the whole ``categories`` array: the FRESH list is read, the name is
added/removed, and the full result PATCHed. Reading fresh (never from a local
snapshot) is what prevents dropping the user's own foreign categories.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.email.errors import (
    CategoryOperationError,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
)
from core.email.outlook_client import OutlookClient

from ._helpers import _make_authenticated_client


class TestAddCategoryToMessage:
    def test_guard_requires_authentication(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.add_category_to_message("m1", "Work")

    def test_empty_name_is_a_noop(self):
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request_json_with_retries") as graph:
            assert client.add_category_to_message("m1", "") == "m1"
        graph.assert_not_called()

    def test_reads_fresh_then_patches_the_full_list(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=[{"categories": ["Existing"]}, {"id": "m1"}],
        ) as graph:
            assert client.add_category_to_message("m1", "Work") == "m1"
        # GET first (fresh list), PATCH second with the merged array.
        assert graph.call_args_list[0].args[0] == "GET"
        patch_call = graph.call_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.kwargs["body"]["categories"] == ["Existing", "Work"]

    def test_idempotent_when_already_present_skips_patch(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=[{"categories": ["Work"]}],
        ) as graph:
            assert client.add_category_to_message("m1", "Work") == "m1"
        # Only the GET ran — no PATCH when the category is already present.
        assert graph.call_count == 1

    def test_returns_rewritten_id_from_patch(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=[{"categories": []}, {"id": "rewritten"}],
        ):
            assert client.add_category_to_message("m1", "Work") == "rewritten"

    def test_provider_error_becomes_category_operation_error(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=EmailExternalAPIError("graph down"),
        ):
            with pytest.raises(CategoryOperationError):
                client.add_category_to_message("m1", "Work")


class TestRemoveCategoryFromMessage:
    def test_removes_and_patches_remaining(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=[{"categories": ["Work", "Other"]}, {"id": "m1"}],
        ) as graph:
            client.remove_category_from_message("m1", "Work")
        assert graph.call_args_list[1].kwargs["body"]["categories"] == ["Other"]

    def test_absent_category_skips_patch(self):
        client = _make_authenticated_client()
        with patch.object(
            client, "_graph_request_json_with_retries",
            side_effect=[{"categories": ["Other"]}],
        ) as graph:
            assert client.remove_category_from_message("m1", "Work") == "m1"
        assert graph.call_count == 1
