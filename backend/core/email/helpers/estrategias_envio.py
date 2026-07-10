"""Estrategias de envio/subida por tamano (D-18): Gmail resumable y Outlook upload session."""

from __future__ import annotations

import enum


# ---------------------------------------------------------------------------
# Upload strategy enums (D-18)
# ---------------------------------------------------------------------------


class GmailSendStrategy(enum.Enum):
    """Cut-off for Gmail draft sends — based on TOTAL MIME bytes."""
    SIMPLE = "simple"        # <= 5 MB total MIME: drafts.send with raw in JSON
    RESUMABLE = "resumable"  # > 5 MB: /upload/.../drafts/send?uploadType=resumable


class OutlookAttachmentStrategy(enum.Enum):
    """Cut-off for Outlook attachment uploads — based on per-attachment bytes."""
    SIMPLE = "simple"                  # < 3 MB: POST /attachments with contentBytes
    UPLOAD_SESSION = "upload_session"  # >= 3 MB: createUploadSession + chunked PUT


_GMAIL_SEND_RESUMABLE_THRESHOLD_BYTES: int = 5 * 1024 * 1024
_OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES: int = 3 * 1024 * 1024


def pick_gmail_send_strategy(total_mime_bytes: int) -> GmailSendStrategy:
    """Decide Gmail's send strategy from the total MIME size.

    Total here means body + base64-encoded attachments + headers — the
    full RFC 5322 message that goes on the wire. Beyond 5 MB the JSON
    metadata path no longer accepts ``Message.raw`` and the resumable
    upload URI is required.
    """
    if total_mime_bytes > _GMAIL_SEND_RESUMABLE_THRESHOLD_BYTES:
        return GmailSendStrategy.RESUMABLE
    return GmailSendStrategy.SIMPLE


def pick_outlook_attachment_strategy(per_attachment_bytes: int) -> OutlookAttachmentStrategy:
    """Decide Outlook's per-attachment upload strategy.

    Outlook decides per attachment, not per message — call this once per
    attachment. ``POST /attachments`` rejects payloads >= 3 MB; the
    upload session path rejects payloads < 3 MB
    (``ErrorAttachmentSizeShouldNotBeLessThanMinimumSize``).
    """
    if per_attachment_bytes >= _OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES:
        return OutlookAttachmentStrategy.UPLOAD_SESSION
    return OutlookAttachmentStrategy.SIMPLE
