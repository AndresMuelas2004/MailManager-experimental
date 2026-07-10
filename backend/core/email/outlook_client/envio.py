"""Envio Outlook: correo directo, envio de borradores y subida de adjuntos (simple y por sesion)."""

from __future__ import annotations

import base64
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..email_client import AttachmentUploadResult, DraftAttachmentInput, EmailMetadata
from ..errors import (
    EmailAttachmentSendFailed,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
    EmailRecipientsMissingError,
)
from ..helpers import OutlookAttachmentStrategy, pick_outlook_attachment_strategy
from .transporte import (
    GRAPH_BASE_URL,
    _OUTLOOK_RETRY_DELAYS_SECONDS,
    _PREFER_IMMUTABLE_HEADERS,
    _retry_after_seconds,
)

logger = logging.getLogger(__name__)


_SEND_DRAFT_MAX_ATTEMPTS = 3
_SEND_DRAFT_RETRY_DELAY = 1.0  # seconds


# createUploadSession / chunked PUT settings — see § 8.1 of the Outlook
# attachments doc. 4 MB is the recommended chunk size; smaller chunks
# multiply HTTP round trips, larger chunks waste bandwidth on retries.
_OUTLOOK_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024


def _extract_attachment_id_from_location(location: str) -> str | None:
    """Pull the attachment id out of a ``createUploadSession`` final Location.

    The terminal 201 response carries a header like
    ``Location: https://outlook.office.com/api/v2.0/Users('...')/Messages('...')/Attachments('AAMk...=')``.
    The id is the substring between ``Attachments('`` and ``')`` —
    encoded base64-like and case-sensitive (per immutable-id guidance).
    Returns ``None`` if the format is unexpected so the caller can raise
    a clear error rather than silently using a malformed id.
    """
    if not location:
        return None
    marker = "Attachments('"
    idx = location.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = location.find("')", start)
    if end < 0:
        return None
    candidate = location[start:end].strip()
    return candidate or None


def _classify_send_failure_reason(error_text: str) -> str:
    """Map a Graph error string to a stable ``failed_attachments.reason``.

    Service-layer translation depends on this label: ``forbidden``
    surfaces as ``provider_forbidden`` (502), ``unavailable`` /
    ``provider_error`` surface as ``provider_unavailable`` (503),
    ``too_large`` surfaces as ``attachment_too_large_for_provider``
    (413). Anything we cannot classify falls back to ``provider_error``.
    """
    text = (error_text or "").lower()
    if "403" in text or "forbidden" in text:
        return "forbidden"
    if "413" in text or "too large" in text or "ErrorAttachmentSizeShouldNotBeLessThanMinimumSize".lower() in text:
        return "too_large"
    if "429" in text:
        return "throttled"
    if "5" in text and ("503" in text or "502" in text or "504" in text or "unavailable" in text):
        return "unavailable"
    return "provider_error"


class OutlookEnvioMixin:
    """Metodos de envio de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def send_email(
        self,
        subject: str,
        body: str,
        recipients: list[str],
    ) -> EmailMetadata:
        """
        Send an HTML email using Microsoft Graph API (draft-then-send).
        The ``body`` is HTML; Graph stores it as ``contentType: "HTML"``.
        Returns metadata of the sent message.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError()

        if not recipients:
            raise EmailRecipientsMissingError()

        try:
            draft_payload = {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body or ""},
                "toRecipients": [
                    {"emailAddress": {"address": r}} for r in recipients
                ],
            }

            draft_response = self._graph_request(
                "POST", f"{GRAPH_BASE_URL}/me/messages", body=draft_payload,
            )
            try:
                metadata = self._parse_graph_message(draft_response, "SENT")
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Outlook failed to parse sent message metadata ({type(exc).__name__}): {exc}"
                ) from exc

            draft_id = draft_response.get("id", "")
            try:
                self._graph_request("POST", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(draft_id, safe='')}/send")
            except EmailExternalAPIError:
                try:
                    self._graph_request("DELETE", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(draft_id, safe='')}")
                except Exception as exc:
                    logger.warning(
                        "Outlook failed to delete orphan draft %s after send failure: %s",
                        draft_id, exc,
                    )
                raise

            if not metadata.from_email or not metadata.from_name:
                profile_email, profile_name = self._fetch_sender_profile()
                if not metadata.from_email:
                    metadata.from_email = profile_email
                if not metadata.from_name:
                    metadata.from_name = profile_name or profile_email

            return metadata
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected send_email error ({type(exc).__name__}): {exc}"
            ) from exc

    def send_draft(self, provider_draft_id: str) -> EmailMetadata:
        """
        Send an existing Outlook draft via POST /me/messages/{id}/send.

        Uses the ``Prefer: IdType="ImmutableId"`` header because the stored
        draft ID is an Immutable ID (created with that header). Without it
        Graph may interpret the path parameter as a mutable ID and fail.

        The ``/send`` endpoint returns 202 (no body). Since the ID is
        immutable, ``provider_draft_id`` stays the same after send.
        Retries transient failures up to 3 total attempts.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook send_draft requires authentication.")

        url = f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(provider_draft_id, safe='')}/send"
        for attempt in range(1, _SEND_DRAFT_MAX_ATTEMPTS + 1):
            try:
                self._graph_request(
                    "POST", url,
                    extra_headers=_PREFER_IMMUTABLE_HEADERS,
                )
                break
            except EmailExternalAPIError:
                if attempt == _SEND_DRAFT_MAX_ATTEMPTS:
                    raise
                logger.warning(
                    "Outlook send_draft attempt %d/%d failed, retrying.",
                    attempt, _SEND_DRAFT_MAX_ATTEMPTS,
                )
                time.sleep(_SEND_DRAFT_RETRY_DELAY)
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Outlook unexpected send_draft error ({type(exc).__name__}): {exc}"
                ) from exc

        # With ImmutableId, provider_draft_id == provider_message_id after send.
        return self._build_outlook_sent_metadata(provider_draft_id)

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
        """Push attachments to the provider draft and send (D-07, D-18, D-27).

        For every attachment without ``provider_attachment_id`` (not yet
        uploaded), pick a strategy via
        :py:func:`pick_outlook_attachment_strategy` and upload it. Each
        successful upload is added to the result list so the caller can
        persist ``provider_attachment_id`` immediately — that lets a
        retry skip already-uploaded parts (D-27).

        After every attachment is in place, ``POST /messages/{id}/send``
        finalises the send. Failure mid-flight raises
        :py:class:`EmailAttachmentSendFailed` with the failed
        attachments listed; the partial upload results returned BEFORE
        the failure remain valid for resume.

        ``in_reply_to`` / ``references`` / ``thread_id`` are accepted for
        signature symmetry with Gmail but **ignored** — Outlook stitches
        the thread server-side via ``createReply`` / ``createReplyAll``
        / ``createForward`` (used at draft creation), so injecting the
        same data on the wire here would be redundant and Graph
        provides no header-injection path for the ``send`` endpoint.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook send_draft_with_attachments requires authentication."
            )
        del in_reply_to, references, thread_id  # signature symmetry with Gmail

        upload_results: list[AttachmentUploadResult] = []
        failed: list[dict[str, Any]] = []
        last_upload_exc: EmailExternalAPIError | None = None
        for att in attachments:
            if att.provider_attachment_id:
                # Already at the provider from a prior partial-success run.
                upload_results.append(
                    AttachmentUploadResult(
                        draft_attachment_id=att.draft_attachment_id,
                        provider_attachment_id=att.provider_attachment_id,
                    )
                )
                continue
            strategy = pick_outlook_attachment_strategy(att.size or len(att.data))
            try:
                if strategy is OutlookAttachmentStrategy.SIMPLE:
                    new_id = self._upload_attachment_simple(provider_draft_id, att)
                else:
                    new_id = self._upload_attachment_via_session(provider_draft_id, att)
            except EmailExternalAPIError as exc:
                last_upload_exc = exc
                failed.append({
                    "draft_attachment_id": att.draft_attachment_id,
                    "filename": att.filename,
                    "reason": _classify_send_failure_reason(str(exc)),
                })
                continue
            upload_results.append(
                AttachmentUploadResult(
                    draft_attachment_id=att.draft_attachment_id,
                    provider_attachment_id=new_id,
                )
            )

        if failed:
            # Chain from the last per-attachment failure so the original
            # traceback survives (core/CLAUDE.md §3.4). ``last_upload_exc`` is
            # guaranteed set whenever ``failed`` is non-empty (both are written
            # in the same ``except`` branch).
            raise EmailAttachmentSendFailed(
                "Outlook failed to upload one or more attachments before send.",
                {"failed_attachments": failed, "succeeded": [
                    {"draft_attachment_id": r.draft_attachment_id,
                     "provider_attachment_id": r.provider_attachment_id}
                    for r in upload_results
                ]},
            ) from last_upload_exc

        # All attachments are in place — fire the send.
        send_url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_draft_id, safe='')}/send"
        )
        last_exc: EmailExternalAPIError | None = None
        for attempt in range(1, _SEND_DRAFT_MAX_ATTEMPTS + 1):
            try:
                self._graph_request(
                    "POST", send_url,
                    extra_headers=_PREFER_IMMUTABLE_HEADERS,
                )
                last_exc = None
                break
            except EmailExternalAPIError as exc:
                last_exc = exc
                if attempt == _SEND_DRAFT_MAX_ATTEMPTS:
                    break
                logger.warning(
                    "Outlook send_draft_with_attachments attempt %d/%d failed, retrying.",
                    attempt, _SEND_DRAFT_MAX_ATTEMPTS,
                )
                time.sleep(_SEND_DRAFT_RETRY_DELAY * attempt)
        if last_exc is not None:
            raise EmailAttachmentSendFailed(
                f"Outlook send after attachments failed: {last_exc}",
                {"failed_attachments": [], "succeeded": [
                    {"draft_attachment_id": r.draft_attachment_id,
                     "provider_attachment_id": r.provider_attachment_id}
                    for r in upload_results
                ]},
            ) from last_exc

        return (
            self._build_outlook_sent_metadata(provider_draft_id, to_recipients),
            upload_results,
        )

    def _upload_attachment_simple(
        self, provider_draft_id: str, attachment: DraftAttachmentInput,
    ) -> str:
        """Upload a small attachment via ``POST /messages/{id}/attachments``.

        Outlook's ``contentBytes`` is base64 standard (RFC 4648 with
        ``+``/``/``), NOT base64url like Gmail. Mixing them produces
        silently-corrupt binaries — see ``adjuntos-outlook.md`` § 14.
        """
        body_payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment.filename,
            "contentType": attachment.mime_type or "application/octet-stream",
            "contentBytes": base64.b64encode(attachment.data).decode("ascii"),
            "isInline": attachment.is_inline,
        }
        if attachment.content_id:
            body_payload["contentId"] = attachment.content_id
        url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_draft_id, safe='')}/attachments"
        )
        response = self._graph_request(
            "POST", url, body=body_payload, extra_headers=_PREFER_IMMUTABLE_HEADERS,
        )
        new_id = str(response.get("id") or "")
        if not new_id:
            raise EmailExternalAPIError(
                "Outlook POST /attachments returned no id."
            )
        return new_id

    def _upload_attachment_via_session(
        self, provider_draft_id: str, attachment: DraftAttachmentInput,
    ) -> str:
        """Upload a >=3 MB attachment via ``createUploadSession`` + chunked PUTs.

        - The session URL points to ``outlook.office.com`` and is
          pre-authenticated; chunk PUTs MUST omit ``Authorization``.
        - Chunks are 4 MB max (recommendation; not a hard limit).
        - The terminal ``201`` response carries the ``Location`` header
          whose last URL segment is ``Attachments('<id>')``; that ``id``
          is what callers use against Graph endpoints.
        """
        size = attachment.size or len(attachment.data)
        init_body = {
            "AttachmentItem": {
                "attachmentType": "file",
                "name": attachment.filename,
                "size": size,
                "contentType": attachment.mime_type or "application/octet-stream",
                "isInline": attachment.is_inline,
            }
        }
        if attachment.content_id:
            init_body["AttachmentItem"]["contentId"] = attachment.content_id
        session_url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_draft_id, safe='')}/attachments/createUploadSession"
        )
        session = self._graph_request(
            "POST", session_url, body=init_body,
            extra_headers=_PREFER_IMMUTABLE_HEADERS,
        )
        upload_url = str(session.get("uploadUrl") or "")
        if not upload_url:
            raise EmailExternalAPIError(
                "Outlook createUploadSession returned no uploadUrl."
            )

        offset = 0
        location: str | None = None
        while offset < size:
            end = min(offset + _OUTLOOK_UPLOAD_CHUNK_SIZE, size) - 1
            chunk = attachment.data[offset : end + 1]
            for attempt, delay in enumerate(_OUTLOOK_RETRY_DELAYS_SECONDS, start=1):
                status, headers, _ = self._upload_chunk_put(
                    upload_url, chunk, offset, end, size,
                )
                if status in (200, 201):
                    if status == 201:
                        location = (headers.get("Location") or headers.get("location"))
                    offset = end + 1
                    break
                if status not in (429, 500, 502, 503, 504) or attempt == len(_OUTLOOK_RETRY_DELAYS_SECONDS):
                    raise EmailExternalAPIError(
                        f"Outlook upload session PUT failed (HTTP {status})."
                    )
                retry_after = _retry_after_seconds(headers)
                time.sleep(retry_after if retry_after is not None else delay)

        if not location:
            raise EmailExternalAPIError(
                "Outlook upload session ended without a final Location header."
            )
        new_id = _extract_attachment_id_from_location(location)
        if not new_id:
            raise EmailExternalAPIError(
                f"Outlook upload session Location did not contain an attachment id: {location}"
            )
        return new_id

    def _upload_chunk_put(
        self,
        upload_url: str,
        chunk: bytes,
        start: int,
        end: int,
        total: int,
    ) -> tuple[int, dict[str, str], bytes]:
        """Single PUT chunk for createUploadSession.

        ``upload_url`` carries an embedded auth token in its query
        string; we MUST NOT add ``Authorization`` here. Returns
        ``(status_code, response_headers, body_bytes)``.
        """
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        req = urllib.request.Request(
            upload_url, data=chunk, headers=headers, method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return (
                    int(response.status),
                    dict(response.headers),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return (int(exc.code), dict(exc.headers or {}), exc.read())
        except urllib.error.URLError as exc:
            raise EmailExternalAPIError(
                f"Outlook upload chunk PUT URL error: {exc.reason}"
            ) from exc

    def _build_outlook_sent_metadata(
        self,
        provider_draft_id: str,
        to_recipients: list[str] | None = None,
    ) -> EmailMetadata:
        """Best-effort metadata for a freshly-sent message (Outlook).

        With ImmutableId, the message id is preserved across the send
        transition, so we can reuse ``fetch_messages_metadata`` against
        the same id. Mirrors the existing pattern in ``send_draft``.
        When ``to_recipients`` is provided, the first recipient seeds
        ``to_email`` if the post-send fetch fails — keeping the "Para"
        column populated for freshly-sent messages.
        """
        try:
            fetched = self.fetch_messages_metadata([provider_draft_id])
            if fetched:
                result = fetched[0]
                if not result.from_email or not result.from_name:
                    profile_email, profile_name = self._fetch_sender_profile()
                    if not result.from_email:
                        result.from_email = profile_email
                    if not result.from_name:
                        result.from_name = profile_name or profile_email
                if not result.to_email and to_recipients:
                    result.to_email = to_recipients[0]
                return result
        except Exception as exc:
            logger.warning(
                "Outlook failed to fetch metadata for sent draft %s (%s): %s",
                provider_draft_id, type(exc).__name__, exc,
            )
        profile_email, profile_name = self._fetch_sender_profile()
        primary_recipient = to_recipients[0] if to_recipients else ""
        return EmailMetadata(
            provider_message_id=provider_draft_id,
            thread_id="",
            from_email=profile_email,
            from_name=profile_name or profile_email,
            subject="",
            received_at=datetime.now(timezone.utc),
            is_read=True,
            box="SENT",
            to_email=primary_recipient,
            to_name="",
        )
