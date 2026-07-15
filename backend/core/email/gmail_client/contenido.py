"""Contenido Gmail: cuerpo+adjuntos unificado, conversaciones, contexto de respuesta y binarios."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, NoReturn

from googleapiclient.errors import HttpError

from ..email_client import (
    AttachmentBinary,
    AttachmentMetadata,
    ConversationMessage,
    EmailContent,
    ReplyContext,
)
from ..errors import (
    CoreError,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
    EmailReplyContextFetchError,
)
from ..helpers import (
    decode_mime_body,
    extract_filename_from_headers,
    find_referenced_cids,
    http_error_detail,
    inline_cid_images,
    normalize_cid,
    resolve_attachment_mime_type,
    retry_with_backoff,
)
from ._comunes import _RETRYABLE_STATUS_CODES, _split_address_header

logger = logging.getLogger(__name__)


# Decoupled from send retries on purpose — download failure modes (5xx,
# 429 with Retry-After, transient connection drops) deserve their own
# tuning even if today they happen to use the same value.
_FETCH_ATTACHMENT_MAX_ATTEMPTS = 3


def _find_part_by_id(
    payload: dict[str, Any], part_id: str,
) -> dict[str, Any] | None:
    """Locate a MIME part by ``partId`` in a Gmail message payload tree.

    Gmail's ``partId`` is documented as immutable per part
    (``adjuntos-gmail.md`` § 6.2). The walk is depth-first and returns
    the first match; multipart wrappers are expanded transparently.
    """
    if (payload.get("partId") or "") == part_id:
        return payload
    for sub in payload.get("parts", []) or []:
        found = _find_part_by_id(sub, part_id)
        if found is not None:
            return found
    return None


def _retry_after_seconds_from_http_error(exc: Exception) -> float | None:
    """Extract ``Retry-After`` (seconds) from a Google ``HttpError``."""
    if not isinstance(exc, HttpError):
        return None
    resp = getattr(exc, "resp", None)
    if resp is None:
        return None
    raw = resp.get("retry-after") if hasattr(resp, "get") else None
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_attachment_download_retryable(exc: Exception) -> bool:
    """Predicate for the attachment download retry loop (D-16).

    Network noise is retryable. ``HttpError`` is retryable only on the
    explicit set of transient status codes
    (``429``/``500``/``502``/``503``/``504``). Permanent errors
    (``400``/``401``/``403``/``404``/``410``) propagate immediately so
    the caller can map them to the right ``EmailAttachment*`` class.
    """
    if isinstance(exc, OSError):
        return True
    if isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        return status in _RETRYABLE_STATUS_CODES
    return False


def _raise_attachment_download_error(exc: HttpError) -> NoReturn:
    """Translate an ``HttpError`` from an attachment fetch (D-17).

    Annotated ``NoReturn`` because every branch raises: this lets the type
    checker treat the code after each ``_raise_attachment_download_error(exc)``
    call site (e.g. ``response.get(...)`` in ``_fetch``) as unreachable, so
    ``response`` is never flagged as possibly-unbound.

    - ``404``/``410`` → :py:class:`EmailAttachmentNotFound` so the
      service marks the metadata row ``unavailable_at``.
    - ``403`` → :py:class:`EmailAttachmentDownloadFailed` with reason
      ``forbidden`` (mapped to HTTP 502 by the API translator).
    - Any other 4xx/5xx → :py:class:`EmailAttachmentDownloadFailed`
      with reason ``unavailable`` (mapped to HTTP 503).
    """
    status, reason = http_error_detail(exc)
    if str(status) in {"404", "410"}:
        raise EmailAttachmentNotFound(
            f"Gmail attachment fetch returned {status}: {reason}.",
            {"reason": "missing"},
        ) from exc
    if str(status) == "403":
        raise EmailAttachmentDownloadFailed(
            f"Gmail attachment fetch forbidden (HTTP {status}: {reason}).",
            {"reason": "forbidden"},
        ) from exc
    raise EmailAttachmentDownloadFailed(
        f"Gmail attachment fetch failed (HTTP {status}: {reason}).",
        {"reason": "unavailable"},
    ) from exc


class GmailContenidoMixin:
    """Metodos de contenido/adjuntos de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    def fetch_content_with_attachments(
        self, provider_message_id: str,
    ) -> tuple[EmailContent, list[AttachmentMetadata], dict[str, str]]:
        """Body + attachments + inline ``cid_map`` in ONE ``messages.get(format=FULL)``.

        Fuses what ``fetch_email_content`` + ``list_message_attachments``
        did with two ``messages.get`` calls (40 quota units) into a single
        read (20 units). Inlines referenced ``cid:…`` images as ``data:``
        URLs (D-13 strict).

        ``_classify_attachments`` runs UNCONDITIONALLY — NOT guarded by
        ``if html_body`` as ``fetch_email_content`` did. A text-only body
        (``html_body is None``) can still carry downloadable attachments,
        and guarding the classification on the HTML body would silently
        drop them. The ``if html_body and cid_map`` guard wraps ONLY the
        inline substitution.
        """
        payload = self._fetch_message_payload(provider_message_id)
        html_body, text_body = self._extract_body_from_payload(payload)
        cid_map, attachments = self._classify_attachments(
            payload, provider_message_id, html_body,
        )
        if html_body and cid_map:
            html_body = inline_cid_images(html_body, cid_map)
        return (
            EmailContent(html_body=html_body, text_body=text_body),
            attachments,
            cid_map,
        )

    def list_message_attachments(
        self,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        """List downloadable attachments + inline cid_map for a Gmail message.

        Single ``messages.get(format=FULL)`` call (cuota: 20 units). The
        same MIME walk that resolves inline ``cid:`` references also
        identifies parts that should be presented to the user as
        downloadable attachments under the strict D-13 rule.
        """
        payload = self._fetch_message_payload(provider_message_id)
        html_body, _ = self._extract_body_from_payload(payload)
        cid_map, attachments = self._classify_attachments(
            payload, provider_message_id, html_body,
        )
        return attachments, cid_map

    def fetch_reply_context(self, provider_message_id: str) -> ReplyContext:
        """Single ``messages.get(format=FULL)`` plus header + body parsing.

        Reuses :py:meth:`_fetch_message_payload` (error wrapping + auth
        guard already in place) so the new endpoint shares the same
        retry / 404 semantics as ``fetch_email_content``. ``format=full``
        is used regardless of ``action`` (Reply / Forward) — the quota
        cost is identical (20 units in both ``metadata`` and ``full``
        modes) and the body is needed for the quote on every action that
        the frontend can choose, see core_guide.md § Helper Reuse Policy.

        Header parsing reuses :py:meth:`_header_value` and applies
        :py:func:`email.utils.getaddresses` to decompose ``To`` / ``Cc``
        / ``Reply-To`` headers that may carry multiple comma-separated
        addresses (with display names, RFC 5322 § 3.4 group syntax,
        etc.). ``Date`` is parsed via :py:func:`parsedate_to_datetime`
        with a soft fallback to ``internalDate`` (already used by
        :py:meth:`_parse_metadata_response`).
        """
        try:
            resource = self._fetch_message_resource(provider_message_id)
        except EmailReplyContextFetchError:
            # Never double-wrap: a reply-context error surfacing from a future
            # helper inside the try must pass through unchanged (core/CLAUDE.md §5).
            raise
        except CoreError as exc:
            # Re-raise as a reply-context-specific error so the service
            # layer maps to ``EmailReplyContextError`` (HTTP 502) instead
            # of the generic external API error.
            raise EmailReplyContextFetchError(
                f"Failed to fetch reply context for message {provider_message_id}: {exc.message}",
                detail={"reason": "provider_fetch_failed"},
            ) from exc

        payload = resource.get("payload", {}) or {}
        thread_id = (resource.get("threadId") or "").strip()

        from_header = self._header_value(payload, "From") or ""
        from_name_raw, from_email_raw = parseaddr(from_header)

        reply_to_addrs = _split_address_header(
            self._header_value(payload, "Reply-To") or "",
        )
        to_addrs = _split_address_header(
            self._header_value(payload, "To") or "",
        )
        cc_addrs = _split_address_header(
            self._header_value(payload, "Cc") or "",
        )

        subject = self._header_value(payload, "Subject") or ""
        message_id_raw = (self._header_value(payload, "Message-ID") or "").strip().strip("<>")
        references = self._header_value(payload, "References") or ""

        date_header = self._header_value(payload, "Date")
        received_at: datetime | None = None
        if date_header:
            try:
                received_at = parsedate_to_datetime(date_header)
            except (TypeError, ValueError):
                received_at = None
        if received_at is None:
            # internalDate is in ms since epoch — same fallback as
            # ``_parse_metadata_response``.
            internal_date = payload.get("internalDate") or ""
            if internal_date:
                try:
                    received_at = datetime.fromtimestamp(
                        int(internal_date) / 1000, tz=timezone.utc,
                    )
                except (ValueError, OverflowError, OSError, TypeError):
                    received_at = None
        if received_at is None:
            received_at = datetime.now(timezone.utc)

        # ``_classify_attachments`` walks the MIME tree; we don't need
        # the attachments here, only the body, so call the dedicated
        # extractor directly (cheaper than re-classifying).
        html_body, text_body = self._extract_body_from_payload(payload)

        _, box = self._resolve_labels(resource.get("labelIds") or [])

        return ReplyContext(
            provider_message_id=provider_message_id,
            thread_id=thread_id,
            from_email=(from_email_raw or "").strip(),
            from_name=(from_name_raw or "").strip(),
            reply_to=reply_to_addrs,
            to_recipients=to_addrs,
            cc_recipients=cc_addrs,
            subject=subject,
            body_html=html_body,
            body_text=text_body,
            received_at=received_at,
            message_id=message_id_raw,
            references=references,
            box=box,
        )

    def fetch_conversation(self, thread_id: str) -> list[ConversationMessage]:
        """Fetch every message of a Gmail thread (metadata + state, NO body).

        A single ``users.threads.get(format=metadata)`` call returns the
        whole thread with every message embedded — including messages in
        Sent / Spam / Trash, which are label changes rather than separate
        threads. Each message is parsed with the same
        :py:meth:`_parse_metadata_response` used by sync (so ``box`` /
        ``is_read`` / ``to_*`` stay byte-for-byte consistent with the
        incremental sync path) and enriched with the ``STARRED`` label
        for ``is_favorite``. Messages are sorted ascending by
        ``internalDate`` (the provider does not guarantee ordering).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_conversation requires authentication.")
        try:
            thread = (
                self.service.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to fetch thread {thread_id} (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected threads.get error ({type(exc).__name__}): {exc}"
            ) from exc

        messages: list[ConversationMessage] = []
        for msg in (thread or {}).get("messages", []):
            try:
                # ``threads.get`` embeds in-progress draft replies in the
                # thread. Drafts belong to the ``drafts`` table, never to the
                # viewer nor to ``email_metadata`` (the lazy-sync persists
                # whatever is returned here) — mirrors the ``"DRAFT" in
                # labelIds`` safety net of ``fetch_messages_metadata``. Extra
                # trap this closes: a Gmail draft gets a NEW message id on
                # every save, so letting it through would accumulate one
                # ghost row per edit+open. Inside the try so a malformed
                # ``labelIds`` still falls into the per-message skip below.
                if "DRAFT" in set(msg.get("labelIds") or []):
                    continue
                meta = self._parse_metadata_response(msg)
                is_favorite = "STARRED" in set(msg.get("labelIds") or [])
                messages.append(
                    ConversationMessage(
                        provider_message_id=meta.provider_message_id,
                        thread_id=meta.thread_id,
                        from_email=meta.from_email,
                        from_name=meta.from_name,
                        subject=meta.subject,
                        received_at=meta.received_at,
                        is_read=meta.is_read,
                        is_favorite=is_favorite,
                        box=meta.box,
                        to_email=meta.to_email,
                        to_name=meta.to_name,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Gmail fetch_conversation: skipping unparseable message %s: %s",
                    msg.get("id", "?"), exc,
                )

        messages.sort(key=lambda m: (m.received_at, m.provider_message_id))
        return messages

    def _fetch_message_resource(self, provider_message_id: str) -> dict[str, Any]:
        """``messages.get(format=FULL)`` returning the full Message resource.

        Callers that only need the MIME tree should use
        :py:meth:`_fetch_message_payload` instead. Root-level fields
        such as ``threadId`` live outside ``payload`` and require this
        helper.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail messages.get requires authentication.")
        try:
            response = (
                self.service.users()
                .messages()
                .get(userId="me", id=provider_message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to fetch message {provider_message_id} "
                f"(HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected messages.get error ({type(exc).__name__}): {exc}"
            ) from exc
        return response or {}

    def _fetch_message_payload(self, provider_message_id: str) -> dict[str, Any]:
        """``messages.get(format=FULL)`` returning just the MIME ``payload``.

        Thin wrapper over :py:meth:`_fetch_message_resource` for the
        common case where root-level fields (``threadId``, ``labelIds``)
        are not needed.
        """
        return self._fetch_message_resource(provider_message_id).get("payload", {}) or {}

    @staticmethod
    def _header_value(part: dict[str, Any], name: str) -> str | None:
        """Return the first header value matching ``name`` (case-insensitive)."""
        for header in part.get("headers", []) or []:
            if (header.get("name") or "").lower() == name.lower():
                value = (header.get("value") or "").strip()
                return value or None
        return None

    @classmethod
    def _content_id(cls, part: dict[str, Any]) -> str | None:
        raw = cls._header_value(part, "Content-ID")
        if raw is None:
            return None
        return raw.strip("<>").strip() or None

    @classmethod
    def _content_disposition(cls, part: dict[str, Any]) -> str | None:
        """Return the ``Content-Disposition`` value (``inline`` / ``attachment``)
        in lowercase, or ``None`` when the header is absent."""
        raw = cls._header_value(part, "Content-Disposition")
        if raw is None:
            return None
        first_token = raw.split(";", 1)[0].strip().lower()
        return first_token or None

    def _classify_attachments(
        self,
        payload: dict[str, Any],
        provider_message_id: str,
        html_body: str | None,
    ) -> tuple[dict[str, str], list[AttachmentMetadata]]:
        """Walk the MIME tree applying the D-13 strict inline-vs-attachment rule.

        Returns ``(cid_map, attachments)``:
        - ``cid_map`` covers parts whose ``Content-ID`` is referenced by
          ``html_body`` via ``cid:`` (HTML attribute or CSS ``url(cid:…)``).
          For these parts the binary is fetched inline (or decoded from
          ``body.data`` when present) and emitted as a ``data:`` URL.
          Per-image failures soft-fallback to skipping the CID — broken
          inline image is better than losing the whole email.
        - ``attachments`` covers everything else the user should see as
          a downloadable attachment: ``Content-Disposition: attachment``,
          parts with a ``filename`` regardless of disposition, and
          inline-marked parts whose CID is NOT referenced by the body
          (D-13 strict — these must surface as downloadables instead of
          being silently lost).
        """
        referenced_cids = find_referenced_cids(html_body)
        cid_map: dict[str, str] = {}
        attachments: list[AttachmentMetadata] = []
        position_counter = 0

        def _is_text_body_part(part: dict[str, Any]) -> bool:
            """text/plain or text/html parts that are the message body."""
            mime_type = (part.get("mimeType") or "").lower()
            if mime_type not in ("text/plain", "text/html"):
                return False
            disposition = self._content_disposition(part)
            filename = part.get("filename") or ""
            # If a text/* part declares attachment disposition or carries a
            # filename, treat it as an attachment (rare but happens on
            # forwarded transcripts).
            return disposition != "attachment" and not filename

        def _walk(part: dict[str, Any]) -> None:
            nonlocal position_counter
            mime_type = (part.get("mimeType") or "")
            if mime_type.lower().startswith("multipart/"):
                for sub in part.get("parts", []) or []:
                    _walk(sub)
                return
            if _is_text_body_part(part):
                return

            cid = self._content_id(part)
            disposition = self._content_disposition(part)
            filename = (part.get("filename") or "").strip()
            if not filename:
                # B-NAME-GMAIL: some senders leave ``MessagePart.filename``
                # empty and put the name only in ``Content-Disposition:
                # filename=`` or ``Content-Type: name=``. Recover it before
                # falling back to the synthetic name below.
                recovered = extract_filename_from_headers(part.get("headers"))
                if recovered:
                    filename = recovered.strip()
            body = part.get("body") or {}
            size = int(body.get("size") or 0)
            part_id = part.get("partId")

            is_inline_marked = (
                disposition == "inline"
                or (mime_type.lower().startswith("image/") and cid is not None)
            )
            # ``find_referenced_cids`` returns normalised entries, so the
            # provider-side Content-ID must be normalised for the membership
            # check (case / percent-encoding tolerant matching).
            referenced = bool(cid and normalize_cid(cid) in referenced_cids)

            if is_inline_marked and referenced:
                # D-13: inline + referenced → resolve to data: URL.
                self._populate_cid_map(
                    part, mime_type, cid, body, provider_message_id, cid_map,
                )
                return

            # Skip parts that carry no usable identity at all (no filename,
            # no disposition, no content_id) — those are not attachments
            # the user can see.
            if not filename and disposition is None and not cid:
                return

            resolved_filename = filename or (cid or "attachment")
            attachments.append(
                AttachmentMetadata(
                    provider_message_id=provider_message_id,
                    part_id=str(part_id) if part_id else None,
                    provider_attachment_id=None,
                    filename=resolved_filename,
                    # B-MIME: a generic declared type (``application/octet-stream``)
                    # is overridden by the type inferred from the filename
                    # extension; a specific declared type is kept verbatim.
                    mime_type=resolve_attachment_mime_type(resolved_filename, mime_type),
                    size=size,
                    content_id=cid,
                    is_inline=is_inline_marked,
                    position=position_counter,
                )
            )
            position_counter += 1

        if "parts" in payload:
            for part in payload["parts"]:
                _walk(part)
        else:
            _walk(payload)
        return cid_map, attachments

    def _populate_cid_map(
        self,
        part: dict[str, Any],
        mime_type: str,
        cid: str,
        body: dict[str, Any],
        provider_message_id: str,
        cid_map: dict[str, str],
    ) -> None:
        """Resolve a referenced inline image to a data: URL (soft fallback).

        Tries ``body.data`` first (Gmail returns small parts inline) and
        falls back to ``attachments().get()`` when the binary lives on a
        separate ``attachmentId``. Per-image failures log a warning and
        skip the CID — the HTML keeps the ``cid:`` reference and the
        client renders a broken-image icon, which is still better than
        losing the whole email.
        """
        data_b64url = body.get("data")
        try:
            if data_b64url:
                raw_bytes = base64.urlsafe_b64decode(data_b64url + "==")
            else:
                attachment_id = body.get("attachmentId")
                if not attachment_id:
                    return
                attachment = (
                    self.service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=provider_message_id, id=attachment_id)
                    .execute()
                )
                attachment_data = attachment.get("data")
                if not attachment_data:
                    return
                raw_bytes = base64.urlsafe_b64decode(attachment_data + "==")
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            logger.warning(
                "Gmail inline image fetch failed for cid=%s (HTTP %s: %s)",
                cid, status, reason,
            )
            return
        except (binascii.Error, UnicodeDecodeError) as exc:
            logger.warning("Gmail inline image decode failed for cid=%s: %s", cid, exc)
            return
        except Exception as exc:
            logger.warning(
                "Gmail inline image unexpected error for cid=%s (%s): %s",
                cid, type(exc).__name__, exc,
            )
            return
        # Keyed by the normalised CID — ``inline_cid_images`` normalises the
        # HTML-side reference before its fallback lookup, so header/HTML
        # case or percent-encoding mismatches still resolve.
        cid_map[normalize_cid(cid)] = (
            f"data:{mime_type};base64,"
            f"{base64.b64encode(raw_bytes).decode('ascii')}"
        )

    def fetch_attachment_binary(
        self,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        """Download a Gmail attachment binary on demand.

        The cache key is ``(account_id, provider_message_id, part_id)``
        (D-06b-clave). To resolve ``part_id`` -> Gmail's transient
        ``attachmentId`` we re-fetch the message tree and locate the
        matching part. Gmail's ``attachmentId`` is documented as transient
        (see ``adjuntos-gmail.md`` § 6.3 and core_guide.md), so we never
        persist it; we re-discover it on every fetch.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError(
                "Gmail fetch_attachment_binary requires authentication."
            )
        if not attachment.part_id:
            raise EmailAttachmentNotFound(
                "Gmail attachment is missing part_id; cannot resolve attachmentId."
            )

        payload = self._fetch_message_payload(provider_message_id)
        located = _find_part_by_id(payload, attachment.part_id)
        if located is None:
            raise EmailAttachmentNotFound(
                f"Gmail attachment part_id={attachment.part_id} not found in message "
                f"{provider_message_id}."
            )

        body = located.get("body") or {}
        data_b64url = body.get("data")
        attachment_api_id = body.get("attachmentId")

        def _fetch() -> bytes:
            if data_b64url:
                return base64.urlsafe_b64decode(data_b64url + "==")
            if not attachment_api_id:
                raise EmailAttachmentNotFound(
                    f"Gmail part {attachment.part_id} has neither inline data nor attachmentId."
                )
            try:
                response = (
                    self.service.users()
                    .messages()
                    .attachments()
                    .get(
                        userId="me",
                        messageId=provider_message_id,
                        id=attachment_api_id,
                    )
                    .execute()
                )
            except HttpError as exc:
                _raise_attachment_download_error(exc)
            payload_data = response.get("data") or ""
            try:
                return base64.urlsafe_b64decode(payload_data + "==")
            except (binascii.Error, UnicodeDecodeError) as decode_exc:
                raise EmailAttachmentDownloadFailed(
                    f"Gmail attachment {attachment.part_id} returned undecodable data.",
                    {"reason": "unavailable"},
                ) from decode_exc

        try:
            data = retry_with_backoff(
                _fetch,
                attempts=_FETCH_ATTACHMENT_MAX_ATTEMPTS,
                is_retryable=_is_attachment_download_retryable,
                retry_after_extractor=_retry_after_seconds_from_http_error,
            )
        except HttpError as exc:
            # Reach this branch when retry_with_backoff propagates the
            # final HttpError (non-retryable from the start). Translate.
            _raise_attachment_download_error(exc)
        except CoreError:
            # ``_fetch`` already converted decode failures into
            # EmailAttachmentDownloadFailed — re-raise without re-wrapping.
            raise
        except Exception as exc:
            # Connection drops, OSError, URLError after retry exhaustion,
            # or any other untyped failure must not escape as raw — D-17
            # contract says the user gets ``provider_unavailable``.
            raise EmailAttachmentDownloadFailed(
                f"Gmail attachment {attachment.part_id} download failed unexpectedly "
                f"({type(exc).__name__}).",
                {"reason": "unavailable"},
            ) from exc
        return AttachmentBinary(
            mime_type=attachment.mime_type or "application/octet-stream",
            filename=attachment.filename,
            data=data,
            size=len(data),
        )

    @staticmethod
    def _extract_body_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        # Gmail returns the raw MIME tree (nested parts[] with base64url-encoded bodies)
        # instead of decoded content like Outlook does. This function recursively traverses
        # that tree to find and decode the text/html and text/plain parts. Charset handling
        # lives in ``decode_mime_body`` (shared helper) — UTF-8-first with validated fallback
        # to the declared charset, which prevents mojibake on senders that mislabel UTF-8
        # bodies as iso-8859-1 / windows-1252.
        html_body: str | None = None
        text_body: str | None = None

        def _charset_of(part: dict[str, Any]) -> str | None:
            for header in part.get("headers", []) or []:
                if (header.get("name") or "").lower() == "content-type":
                    value = header.get("value") or ""
                    match = re.search(r'charset\s*=\s*"?([^";\s]+)"?', value, re.IGNORECASE)
                    if match:
                        return match.group(1).strip()
            return None

        def _traverse(part: dict[str, Any]) -> None:
            nonlocal html_body, text_body
            mime_type = part.get("mimeType", "")
            if mime_type.startswith("multipart/"):
                for sub_part in part.get("parts", []):
                    _traverse(sub_part)
                return
            body_data = part.get("body", {}).get("data")
            if not body_data:
                return
            charset = _charset_of(part)
            if mime_type == "text/html" and html_body is None:
                decoded = decode_mime_body(body_data, charset)
                if decoded is not None:
                    html_body = decoded
            elif mime_type == "text/plain" and text_body is None:
                decoded = decode_mime_body(body_data, charset)
                if decoded is not None:
                    text_body = decoded

        if "parts" in payload:
            for part in payload["parts"]:
                _traverse(part)
        else:
            _traverse(payload)

        return html_body, text_body
