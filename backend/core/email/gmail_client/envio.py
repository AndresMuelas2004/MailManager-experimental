"""Envio Gmail: correo directo, envio de borradores (simple y resumable) y sus reintentos."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any

import google_auth_httplib2
import httplib2
from googleapiclient.errors import HttpError

from ..email_client import AttachmentUploadResult, DraftAttachmentInput, EmailMetadata
from ..errors import (
    EmailAttachmentBlockedByProvider,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
    EmailRecipientsMissingError,
)
from ..helpers import (
    GmailSendStrategy,
    html_to_plain_text_alternative,
    http_error_detail,
    pick_gmail_send_strategy,
)
from ._comunes import _is_retryable

logger = logging.getLogger(__name__)


_SEND_DRAFT_MAX_ATTEMPTS = 3
_SEND_DRAFT_RETRY_DELAY = 1.0  # seconds


# Resumable upload — Gmail requires chunks to be a multiple of 256 KB
# (except the very last one). 4 MB matches the recommended default;
# smaller would multiply the number of HTTP round trips needlessly.
_GMAIL_RESUMABLE_CHUNK_SIZE = 4 * 1024 * 1024
_GMAIL_RESUMABLE_UPLOAD_BASE = (
    "https://gmail.googleapis.com/upload/gmail/v1/users/me/drafts/send?uploadType=resumable"
)


# Daily quota errors are NOT transient — Gmail throws them with
# ``User-rate limit exceeded (Mail sending)`` and a short backoff is
# unhelpful. Detect and raise immediately as a non-retryable failure.
_GMAIL_DAILY_LIMIT_REASON_MARKER = "user-rate limit exceeded"


def _is_send_retryable(exception: Any) -> bool:
    """Send-path retry predicate.

    Like :py:func:`_is_retryable` but additionally filters out the Gmail
    daily quota 429 (``user-rate limit exceeded (mail sending)``), which
    is documented as a hard wall — retrying it just burns budget. All
    other 429 / 5xx / network failures remain retryable.
    """
    if not _is_retryable(exception):
        return False
    if isinstance(exception, HttpError):
        status, reason = http_error_detail(exception)
        if str(status) == "429" and _GMAIL_DAILY_LIMIT_REASON_MARKER in (reason or "").lower():
            return False
    return True


def _failed_attachments_detail(
    attachments: list[Any],
    reason: str,
) -> dict[str, Any]:
    """Build the ``detail`` payload for :py:class:`EmailAttachmentSendFailed`.

    Used by Gmail's atomic send: when the send fails, every attachment
    is reported as failed (Gmail does not tell us per-attachment
    granularity, the failure is over the whole MIME).
    """
    return {
        "failed_attachments": [
            {
                "draft_attachment_id": getattr(att, "draft_attachment_id", ""),
                "filename": getattr(att, "filename", ""),
                "reason": reason,
            }
            for att in attachments
        ]
    }


def _raise_send_with_attachments_error(
    exc: HttpError, attachments: list[Any],
) -> None:
    """Translate an ``HttpError`` from ``drafts.send`` with attachments.

    - ``400`` with reason text matching ``The attachment is invalid`` →
      :py:class:`EmailAttachmentBlockedByProvider` (D-04a edge case).
    - ``413`` → :py:class:`EmailAttachmentTooLargeForProvider` (the
      tenant configured a smaller cap than D-01 / D-02).
    - ``429`` with ``user-rate limit exceeded (mail sending)`` reason →
      :py:class:`EmailAttachmentSendFailed` with reason ``daily_limit``
      (the retry loop will not help; the user's daily quota is gone).
    - Anything else → :py:class:`EmailAttachmentSendFailed` with the
      raw HTTP detail.
    """
    status, reason = http_error_detail(exc)
    reason_lc = (reason or "").lower()
    if str(status) == "400" and "attachment is invalid" in reason_lc:
        raise EmailAttachmentBlockedByProvider(
            f"Gmail rejected the message because an attachment is blocked "
            f"(HTTP {status}: {reason})."
        ) from exc
    if str(status) == "413":
        raise EmailAttachmentTooLargeForProvider(
            f"Gmail rejected the message as too large (HTTP {status}: {reason})."
        ) from exc
    if str(status) == "429" and _GMAIL_DAILY_LIMIT_REASON_MARKER in reason_lc:
        raise EmailAttachmentSendFailed(
            f"Gmail daily sending limit reached (HTTP {status}: {reason}).",
            _failed_attachments_detail(attachments, "daily_limit"),
        ) from exc
    raise EmailAttachmentSendFailed(
        f"Gmail send failed (HTTP {status}: {reason}).",
        _failed_attachments_detail(attachments, "provider_error"),
    ) from exc


class GmailEnvioMixin:
    """Metodos de envio de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    def send_email(
        self,
        subject: str,
        body: str,
        recipients: list[str],
    ) -> EmailMetadata:
        """
        Send an HTML email using the Gmail API.

        The ``body`` is HTML. The message is assembled as a
        ``multipart/alternative`` with a derived ``text/plain`` part FIRST
        and the ``text/html`` part SECOND (the order clients require to
        prefer the richest representation). ``EmailMessage`` handles UTF-8
        body encoding and RFC 2047 header encoding automatically.
        Returns metadata of the sent message.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail send_email requires authentication.")

        if not recipients:
            raise EmailRecipientsMissingError("Gmail send_email requires at least one recipient.")

        from email.message import EmailMessage

        message = EmailMessage()
        message["to"] = ", ".join(recipients)
        message["subject"] = subject
        # HTML body: text/plain alternative (derived) first, text/html second.
        message.set_content(html_to_plain_text_alternative(body) or "", subtype="plain", charset="utf-8")
        message.add_alternative(body or "", subtype="html", charset="utf-8")

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        payload = {"raw": raw_message}

        try:
            response = self.service.users().messages().send(userId="me", body=payload).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to send email (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected send email error ({type(exc).__name__}): {exc}"
            ) from exc

        message_id = response.get("id", "")
        if message_id:
            # Best-effort post-send metadata enrichment — failures here must
            # never abort the send, which already succeeded at the provider.
            # The generic `except Exception` is intentional: any failure from
            # the follow-up metadata fetch (network, parsing, partial data)
            # falls through to the minimal-metadata fallback below.
            try:
                fetched = self.fetch_messages_metadata([message_id])
                if fetched:
                    result = fetched[0]
                    if not result.subject:
                        result.subject = subject
                    if not result.from_email:
                        result.from_email = self._fetch_sender_email()
                    if not result.from_name:
                        result.from_name = result.from_email
                    if not result.to_email and recipients:
                        result.to_email = recipients[0]
                    return result
            except Exception as exc:
                logger.warning(
                    "Gmail failed to fetch metadata for sent message %s (%s): %s",
                    message_id, type(exc).__name__, exc,
                )

        # Fallback: build minimal metadata from the send response
        sender_email = self._fetch_sender_email()
        primary_recipient = recipients[0] if recipients else ""
        return EmailMetadata(
            provider_message_id=message_id,
            thread_id=response.get("threadId") or "",
            from_email=sender_email,
            from_name=sender_email,
            subject=subject,
            received_at=datetime.now(timezone.utc),
            is_read=True,
            box="SENT",
            to_email=primary_recipient,
            to_name="",
        )

    def send_draft(self, provider_draft_id: str) -> EmailMetadata:
        """
        Send an existing Gmail draft via users().drafts().send().

        Gmail returns a Message resource with a new message_id (different
        from the draft ID). The draft is automatically deleted by Gmail.
        Retries transient failures up to 3 total attempts.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail send_draft requires authentication.")

        response: dict[str, Any] | None = None
        for attempt in range(1, _SEND_DRAFT_MAX_ATTEMPTS + 1):
            try:
                response = (
                    self.service.users()
                    .drafts()
                    .send(userId="me", body={"id": provider_draft_id})
                    .execute()
                )
                break
            except HttpError as exc:
                if not _is_send_retryable(exc) or attempt == _SEND_DRAFT_MAX_ATTEMPTS:
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail failed to send draft (HTTP {status}: {reason})."
                    ) from exc
                logger.warning(
                    "Gmail send_draft attempt %d/%d failed, retrying: %s",
                    attempt, _SEND_DRAFT_MAX_ATTEMPTS, exc,
                )
                time.sleep(_SEND_DRAFT_RETRY_DELAY)
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected send_draft error ({type(exc).__name__}): {exc}"
                ) from exc

        if response is None:
            raise EmailExternalAPIError("Gmail send_draft: no response after retry loop.")

        message_id = response.get("id", "")
        if message_id:
            try:
                fetched = self.fetch_messages_metadata([message_id])
                if fetched:
                    result = fetched[0]
                    if not result.from_email:
                        result.from_email = self._fetch_sender_email()
                    if not result.from_name:
                        result.from_name = result.from_email
                    return result
            except Exception as exc:
                logger.warning(
                    "Gmail failed to fetch metadata for sent draft %s (%s): %s",
                    message_id, type(exc).__name__, exc,
                )

        sender_email = self._fetch_sender_email()
        return EmailMetadata(
            provider_message_id=message_id,
            thread_id=response.get("threadId") or "",
            from_email=sender_email,
            from_name=sender_email,
            subject="",
            received_at=datetime.now(timezone.utc),
            is_read=True,
            box="SENT",
        )

    def send_draft_with_attachments(
        self,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        attachments: list[DraftAttachmentInput],
        *,
        in_reply_to: str | None = None,
        references: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[EmailMetadata, list[AttachmentUploadResult]]:
        """Atomic Gmail draft send with attachments (D-07, D-18).

        Builds a fresh ``multipart/mixed`` MIME with body + attachments
        and replaces the provider draft contents in a single
        ``drafts.send`` call (or its resumable upload variant when the
        total payload exceeds 5 MB). Gmail does not support partial
        attachment state on drafts, so the result list never reports
        provider_attachment_ids — Gmail's send is atomic.

        When ``in_reply_to`` / ``references`` are provided (reply / forward
        drafts), they are injected as MIME headers via
        :py:meth:`_build_draft_raw_message`'s ``extra_headers`` plumbing
        so any destination client re-threads. ``thread_id`` rides the
        ``drafts.send`` JSON ``message`` shape so Gmail itself stitches
        the outgoing message into the right thread.

        On success returns the sent ``EmailMetadata`` and an empty
        upload result list. On a non-retryable failure raises
        :py:class:`EmailAttachmentSendFailed` (or
        :py:class:`EmailAttachmentBlockedByProvider` /
        :py:class:`EmailAttachmentTooLargeForProvider` for specific
        provider-side rejections).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError(
                "Gmail send_draft_with_attachments requires authentication."
            )

        extra_headers: dict[str, str] | None = None
        if in_reply_to or references:
            extra_headers = {}
            if in_reply_to:
                extra_headers["In-Reply-To"] = in_reply_to
            if references:
                extra_headers["References"] = references

        raw_b64url, raw_bytes = self._build_draft_raw_message(
            to_recipients, cc_recipients, bcc_recipients, subject, body, attachments,
            extra_headers=extra_headers,
        )
        strategy = pick_gmail_send_strategy(len(raw_bytes))

        if strategy is GmailSendStrategy.SIMPLE:
            response = self._send_draft_simple(
                provider_draft_id, raw_b64url, attachments, thread_id=thread_id,
            )
        else:
            response = self._send_draft_resumable(
                provider_draft_id, raw_b64url, raw_bytes, attachments,
                thread_id=thread_id,
            )

        message_id = response.get("id", "") if isinstance(response, dict) else ""
        return (
            self._build_sent_metadata(message_id, response, subject, to_recipients),
            [],
        )

    @staticmethod
    def _build_send_message_payload(
        raw_b64url: str, thread_id: str | None,
    ) -> dict[str, Any]:
        """Build the ``message`` sub-payload for ``drafts.send``.

        Used by both the simple and resumable paths so the shape stays
        identical (``raw`` always present, ``threadId`` only when the
        draft belongs to a thread).
        """
        message: dict[str, Any] = {"raw": raw_b64url}
        if thread_id:
            message["threadId"] = thread_id
        return message

    def _send_draft_simple(
        self,
        provider_draft_id: str,
        raw_b64url: str,
        attachments: list[DraftAttachmentInput],
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """``drafts.send`` with the metadata body shape (<= 5 MB total MIME)."""
        for attempt in range(1, _SEND_DRAFT_MAX_ATTEMPTS + 1):
            try:
                return (
                    self.service.users()
                    .drafts()
                    .send(
                        userId="me",
                        body={
                            "id": provider_draft_id,
                            "message": self._build_send_message_payload(
                                raw_b64url, thread_id,
                            ),
                        },
                    )
                    .execute()
                )
            except HttpError as exc:
                if attempt == _SEND_DRAFT_MAX_ATTEMPTS or not _is_send_retryable(exc):
                    _raise_send_with_attachments_error(exc, attachments)
                logger.warning(
                    "Gmail drafts.send simple attempt %d/%d failed, retrying: %s",
                    attempt, _SEND_DRAFT_MAX_ATTEMPTS, exc,
                )
                time.sleep(_SEND_DRAFT_RETRY_DELAY * attempt)
            except Exception as exc:
                raise EmailAttachmentSendFailed(
                    f"Gmail unexpected drafts.send error ({type(exc).__name__}): {exc}",
                    _failed_attachments_detail(attachments, "provider_error"),
                ) from exc
        raise EmailAttachmentSendFailed(
            "Gmail drafts.send simple exhausted retries without a response.",
            _failed_attachments_detail(attachments, "unavailable"),
        )

    def _send_draft_resumable(
        self,
        provider_draft_id: str,
        raw_b64url: str,
        raw_bytes: bytes,
        attachments: list[DraftAttachmentInput],
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Resumable upload path for >5 MB total MIME payloads.

        Initiates the session, streams the content in 4 MB chunks (each
        a multiple of 256 KB except the last), and parses the final 201
        response into the same shape returned by ``drafts.send``. On
        unrecoverable failure raises :py:class:`EmailAttachmentSendFailed`.
        """
        if self._credentials is None:
            raise EmailNotAuthenticatedError(
                "Gmail resumable send requires refreshed credentials."
            )

        # Step 1: initiate the resumable session. The body is a Draft
        # JSON referencing the existing draft id; the binary is uploaded
        # separately via PUT chunks.
        init_body = {
            "id": provider_draft_id,
            "message": self._build_send_message_payload(raw_b64url, thread_id),
        }
        try:
            init_response = self._http_request(
                "POST",
                _GMAIL_RESUMABLE_UPLOAD_BASE,
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "message/rfc822",
                    "X-Upload-Content-Length": str(len(raw_bytes)),
                },
                body=init_body,
            )
        except Exception as exc:
            raise EmailAttachmentSendFailed(
                f"Gmail resumable session init failed ({type(exc).__name__}): {exc}",
                _failed_attachments_detail(attachments, "provider_error"),
            ) from exc

        upload_uri = (init_response.get("headers", {}) or {}).get("location") or (
            init_response.get("headers", {}) or {}
        ).get("Location")
        if not upload_uri:
            raise EmailAttachmentSendFailed(
                "Gmail resumable session init returned no Location header.",
                _failed_attachments_detail(attachments, "provider_error"),
            )

        # Step 2: PUT chunks. Single PUT when payload fits in one chunk.
        total = len(raw_bytes)
        offset = 0
        while offset < total:
            chunk_end = min(offset + _GMAIL_RESUMABLE_CHUNK_SIZE, total) - 1
            chunk = raw_bytes[offset : chunk_end + 1]
            headers = {
                "Content-Type": "message/rfc822",
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{chunk_end}/{total}",
            }
            try:
                response = self._http_request(
                    "PUT", upload_uri, headers=headers, body_bytes=chunk,
                )
            except Exception as exc:
                raise EmailAttachmentSendFailed(
                    f"Gmail resumable PUT chunk failed ({type(exc).__name__}): {exc}",
                    _failed_attachments_detail(attachments, "provider_error"),
                ) from exc
            status = response.get("status", 0)
            if status in (200, 201):
                return response.get("json", {}) or {}
            if status == 308:
                # Continue with the next chunk past the server's confirmed range.
                range_header = (response.get("headers", {}) or {}).get("range") or ""
                _, _, end_str = range_header.rpartition("-")
                try:
                    offset = int(end_str) + 1
                except ValueError:
                    offset = chunk_end + 1
                continue
            raise EmailAttachmentSendFailed(
                f"Gmail resumable PUT returned unexpected status {status}.",
                _failed_attachments_detail(attachments, "provider_error"),
            )

        raise EmailAttachmentSendFailed(
            "Gmail resumable upload completed without a 201 final response.",
            _failed_attachments_detail(attachments, "unavailable"),
        )

    def _http_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        body_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """Authenticated HTTP request bypassing the discovery service.

        Used for the resumable upload flow where the discovery client
        does not directly expose the ``/upload/...`` URI. Re-uses the
        same ``Credentials`` already refreshed by ``authenticate_silent``,
        so no extra token logic lives here.
        """
        import json as _json

        if self._credentials is None:
            raise EmailNotAuthenticatedError(
                "Gmail _http_request requires refreshed credentials."
            )
        http = google_auth_httplib2.AuthorizedHttp(
            self._credentials, http=httplib2.Http(timeout=60)  # 60s socket timeout: resumable upload chunks are larger than normal calls
        )
        outbound_headers = dict(headers or {})
        if body is not None and body_bytes is None:
            payload_str = _json.dumps(body)
            payload = payload_str.encode("utf-8")
        else:
            payload = body_bytes or b""
        response, content = http.request(
            url,
            method=method,
            body=payload,
            headers=outbound_headers,
        )
        status = int(response.status) if response is not None else 0
        # httplib2 lowercases header names; preserve the original-case
        # ``Location`` lookup in callers via ``.get(...) or .get(...)``.
        json_payload: dict[str, Any] = {}
        if content:
            try:
                json_payload = _json.loads(content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                json_payload = {}
        return {"status": status, "headers": dict(response or {}), "json": json_payload}

    def _build_sent_metadata(
        self,
        message_id: str,
        response: dict[str, Any],
        subject: str,
        to_recipients: list[str] | None = None,
    ) -> EmailMetadata:
        """Best-effort metadata enrichment for a freshly-sent message.

        Re-uses the same fallback-to-minimal-metadata pattern as
        ``send_email`` and ``send_draft`` so all three send paths emit
        a consistent ``EmailMetadata`` shape regardless of how the
        upstream call returned. When ``to_recipients`` is provided and
        the post-send fetch fails, the first recipient seeds
        ``to_email`` so the inbox "Para" column still renders correctly
        for the freshly-sent message.
        """
        if message_id:
            try:
                fetched = self.fetch_messages_metadata([message_id])
                if fetched:
                    result = fetched[0]
                    if not result.subject:
                        result.subject = subject
                    if not result.from_email:
                        result.from_email = self._fetch_sender_email()
                    if not result.from_name:
                        result.from_name = result.from_email
                    if not result.to_email and to_recipients:
                        result.to_email = to_recipients[0]
                    return result
            except Exception as exc:
                logger.warning(
                    "Gmail failed to fetch metadata for sent message %s (%s): %s",
                    message_id, type(exc).__name__, exc,
                )
        sender_email = self._fetch_sender_email()
        primary_recipient = to_recipients[0] if to_recipients else ""
        return EmailMetadata(
            provider_message_id=message_id,
            thread_id=response.get("threadId") or "" if isinstance(response, dict) else "",
            from_email=sender_email,
            from_name=sender_email,
            subject=subject,
            received_at=datetime.now(timezone.utc),
            is_read=True,
            box="SENT",
            to_email=primary_recipient,
            to_name="",
        )
