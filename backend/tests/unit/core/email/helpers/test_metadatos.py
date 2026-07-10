"""Tests espejo de ``core.email.helpers.metadatos`` (dedupe de lotes de sincronizacion)."""

from __future__ import annotations

from datetime import datetime, timezone

from core.email.email_client import EmailMetadata
from core.email.helpers import dedupe_metadata_by_message_id


# ── dedupe_metadata_by_message_id ──────────────────────────────────


class TestDedupeMetadataByMessageId:
    def _meta(self, msg_id: str, *, is_read: bool = False) -> EmailMetadata:
        return EmailMetadata(
            provider_message_id=msg_id,
            thread_id="t1",
            from_email="from@example.com",
            from_name="From",
            subject="subject",
            received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_read=is_read,
            box="ALL_MAIL",
        )

    def test_duplicate_id_keeps_last_occurrence(self):
        first = self._meta("m1", is_read=False)
        last = self._meta("m1", is_read=True)
        assert dedupe_metadata_by_message_id([first, last]) == [last]

    def test_unique_ids_preserved_in_order(self):
        items = [self._meta("m1"), self._meta("m2"), self._meta("m3")]
        assert dedupe_metadata_by_message_id(items) == items

    def test_empty_list_returns_empty(self):
        assert dedupe_metadata_by_message_id([]) == []
