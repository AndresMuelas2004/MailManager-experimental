"""Unit tests for ``provider_labels`` stamping in the sync parsers (carpetas-y-reglas).

``EmailMetadata.provider_labels`` is the TRANSIENT membership carrier the sync
reconciliation crosses against ``folder_account_links`` — Gmail ships the raw
``labelIds`` (opaque user-label ids match, system ids simply do not), Outlook
ships the ``categories`` display-names (a folder's Outlook provider_ref IS its
name). Both message parsers are static, so they can be driven directly with a
provider payload.
"""

from __future__ import annotations

from core.email.gmail_client import GmailClient
from core.email.outlook_client import OutlookClient

from .outlook_client._helpers import _make_graph_message


class TestGmailProviderLabels:
    def test_raw_label_ids_are_carried(self):
        msg = {
            "id": "m1",
            "threadId": "t1",
            "internalDate": "1700000000000",
            "labelIds": ["INBOX", "Label_5", "STARRED"],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        # NOT filtered here — the service crosses these against the links map.
        assert result.provider_labels == ["INBOX", "Label_5", "STARRED"]

    def test_no_labels_yields_empty_list(self):
        msg = {
            "id": "m2",
            "threadId": "t2",
            "internalDate": "1700000000000",
            "labelIds": [],
            "payload": {"headers": []},
        }
        result = GmailClient._parse_metadata_response(msg)
        assert result.provider_labels == []


class TestOutlookProviderLabels:
    def test_categories_are_carried(self):
        msg = _make_graph_message()
        msg["categories"] = ["Universidad", "Facturas"]
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.provider_labels == ["Universidad", "Facturas"]

    def test_missing_categories_yields_empty_list(self):
        msg = _make_graph_message()
        assert "categories" not in msg
        result = OutlookClient._parse_graph_message(msg, "ALL_MAIL")
        assert result.provider_labels == []
