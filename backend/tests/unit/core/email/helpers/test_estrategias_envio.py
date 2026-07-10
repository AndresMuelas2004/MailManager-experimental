"""Tests espejo de ``core.email.helpers.estrategias_envio`` (umbrales D-18)."""

from __future__ import annotations

from core.email.helpers import (
    GmailSendStrategy,
    OutlookAttachmentStrategy,
    pick_gmail_send_strategy,
    pick_outlook_attachment_strategy,
)


# ── pick_gmail_send_strategy ───────────────────────────────────────


class TestPickGmailSendStrategy:

    def test_under_5mb_simple(self):
        assert pick_gmail_send_strategy(0) is GmailSendStrategy.SIMPLE
        assert pick_gmail_send_strategy(1) is GmailSendStrategy.SIMPLE
        assert pick_gmail_send_strategy(5 * 1024 * 1024) is GmailSendStrategy.SIMPLE

    def test_over_5mb_resumable(self):
        assert pick_gmail_send_strategy(5 * 1024 * 1024 + 1) is GmailSendStrategy.RESUMABLE
        assert pick_gmail_send_strategy(50 * 1024 * 1024) is GmailSendStrategy.RESUMABLE


# ── pick_outlook_attachment_strategy ───────────────────────────────


class TestPickOutlookAttachmentStrategy:

    def test_under_3mb_simple(self):
        assert pick_outlook_attachment_strategy(0) is OutlookAttachmentStrategy.SIMPLE
        assert pick_outlook_attachment_strategy(3 * 1024 * 1024 - 1) is OutlookAttachmentStrategy.SIMPLE

    def test_at_or_over_3mb_session(self):
        # Boundary: the simple endpoint rejects payloads >= 3 MB so the
        # cut-off must be inclusive on the upload-session side.
        assert pick_outlook_attachment_strategy(3 * 1024 * 1024) is OutlookAttachmentStrategy.UPLOAD_SESSION
        assert pick_outlook_attachment_strategy(10 * 1024 * 1024) is OutlookAttachmentStrategy.UPLOAD_SESSION
