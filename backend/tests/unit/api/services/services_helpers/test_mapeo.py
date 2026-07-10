"""Tests espejo de ``services_helpers.mapeo``: proyeccion de fila a EmailMetadataOut."""

from __future__ import annotations

from api.services.services_helpers import row_to_email_metadata_out


# ------------------------------------------------------------------
# row_to_email_metadata_out — thread_message_count projection
# ------------------------------------------------------------------


def _metadata_row(**overrides):
    base = {
        "provider_message_id": "m1",
        "account_id": "acc-1",
        "mailbox_id": "mb-1",
        "thread_id": "t1",
        "from_email": "a@b.com",
        "from_name": "A",
        "subject": "s",
        "received_at": "2026-01-01T00:00:00+00:00",
        "is_read": False,
        "box": "ALL_MAIL",
    }
    base.update(overrides)
    return base


class TestRowToEmailMetadataOut:

    def test_populates_thread_message_count_from_row(self):
        out = row_to_email_metadata_out(_metadata_row(thread_message_count=4))
        assert out.thread_message_count == 4

    def test_thread_message_count_defaults_to_one_when_absent(self):
        # Non-grouped listings (Favourites) and each message inside a
        # ConversationOut omit the key → must fall back to 1, never 0.
        out = row_to_email_metadata_out(_metadata_row())
        assert out.thread_message_count == 1

    def test_thread_message_count_falsy_value_falls_back_to_one(self):
        # A NULL / 0 thread_message_count would be nonsensical for a row that
        # represents at least itself; the helper coalesces it to 1.
        out = row_to_email_metadata_out(_metadata_row(thread_message_count=None))
        assert out.thread_message_count == 1
