"""Ensamblado del mensaje RFC 5322 saliente (multipart/alternative + adjuntos)."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from ..errors import EmailAttachmentSendFailed
from .html_texto import html_to_plain_text_alternative


# ---------------------------------------------------------------------------
# MIME assembly for Gmail send (D-31 + Gmail multipart/mixed)
# ---------------------------------------------------------------------------


def _split_mime_type(mime_type: str | None) -> tuple[str, str]:
    """Split a MIME type into ``(maintype, subtype)`` with octet-stream fallback."""
    if not mime_type or "/" not in mime_type:
        return "application", "octet-stream"
    main, _, sub = mime_type.partition("/")
    return main.strip() or "application", sub.strip() or "octet-stream"


def build_mime_with_attachments(
    *,
    to_recipients: list[str],
    cc_recipients: list[str],
    bcc_recipients: list[str],
    subject: str,
    body: str,
    attachments: list[dict[str, Any]],
    from_email: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a complete RFC 5322 message with an HTML body + attachments.

    Uses ``email.message.EmailMessage`` (Python's modern email API). The
    ``body`` is treated as **HTML** and the message is assembled as a
    ``multipart/alternative`` with a ``text/plain`` alternative (derived
    from the HTML via :py:func:`html_to_plain_text_alternative`) FIRST
    and the ``text/html`` part SECOND — the order mail clients require to
    show the richest representation they understand. When ``attachments``
    are present, ``EmailMessage`` promotes the root to ``multipart/mixed``
    with the ``multipart/alternative`` as its first child. Each attachment
    in ``attachments`` is a dict with keys ``filename``, ``mime_type``,
    ``data`` (bytes); optional ``content_id`` and ``is_inline`` are
    honoured for inline parts (composer doesn't ship them today, but the
    helper supports them so the call site is uniform).

    ``extra_headers`` carries arbitrary RFC 5322 header injections used
    by the Reply / Forward flow (``In-Reply-To``, ``References``).
    The keys are unique by construction (it's a ``dict``) so a single
    call cannot accidentally duplicate a header — Python's
    :py:class:`email.message.EmailMessage` would otherwise append on
    repeated subscript assignment. ``None`` and empty-string values
    are silently skipped so callers can pass partial maps without a
    pre-filter.

    Returns the raw bytes of the MIME message (not base64url-encoded).
    Callers wrap the bytes in ``base64.urlsafe_b64encode`` for the
    JSON ``Message.raw`` form, or send them as ``message/rfc822`` for
    ``uploadType=resumable``.

    The recipients lists may be empty individually, but at least one
    address overall is the caller's responsibility to enforce. Empty
    lists collapse to absent headers (Python's email API accepts that).
    """
    msg = EmailMessage()
    if from_email:
        msg["From"] = from_email
    if to_recipients:
        msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    if bcc_recipients:
        msg["Bcc"] = ", ".join(bcc_recipients)
    if subject:
        msg["Subject"] = subject
    if extra_headers:
        for name, value in extra_headers.items():
            if value is None:
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            msg[name] = value_str
    # Body is HTML. ``set_content`` lays down the text/plain alternative
    # (derived from the HTML) and ``add_alternative`` appends the HTML part,
    # promoting the root to ``multipart/alternative`` with text/plain first.
    plain_alternative = html_to_plain_text_alternative(body)
    msg.set_content(plain_alternative or "", subtype="plain", charset="utf-8")
    msg.add_alternative(body or "", subtype="html", charset="utf-8")

    for attachment in attachments:
        data = attachment["data"]
        if not isinstance(data, (bytes, bytearray)):
            raise EmailAttachmentSendFailed(
                f"Attachment data must be bytes/bytearray, got {type(data).__name__}",
                detail={"reason": "invalid_attachment_data"},
            )
        maintype, subtype = _split_mime_type(attachment.get("mime_type"))
        filename = attachment.get("filename") or "attachment"
        cid = attachment.get("content_id")
        is_inline = bool(attachment.get("is_inline", False))
        kwargs: dict[str, Any] = {
            "maintype": maintype,
            "subtype": subtype,
            "filename": filename,
        }
        if is_inline:
            kwargs["disposition"] = "inline"
            kwargs["cid"] = cid if cid else f"<{make_msgid(domain='mailmanager.local')[1:-1]}>"
        msg.add_attachment(bytes(data), **kwargs)

    return msg.as_bytes()
