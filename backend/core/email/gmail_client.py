from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Callable, NoReturn

logger = logging.getLogger(__name__)

import google_auth_httplib2
import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .email_client import (
    AttachmentBinary,
    AttachmentMetadata,
    AttachmentUploadResult,
    ConversationMessage,
    DraftAttachmentInput,
    DraftMetadata,
    EmailClient,
    EmailContent,
    EmailMetadata,
    LabelUpdate,
    ReplyContext,
    SpamMoveResult,
    SyncResult,
)
from .errors import (
    CoreError,
    EmailAttachmentBlockedByProvider,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
    EmailExternalAPIError,
    EmailInvalidCredentialsDataError,
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailNotAuthenticatedError,
    EmailProviderConfigError,
    EmailRecipientsMissingError,
    EmailRefreshFailedError,
    EmailReplyContextFetchError,
)
from .helpers import (
    GmailSendStrategy,
    build_mime_with_attachments,
    decode_mime_body,
    dedupe_metadata_by_message_id,
    extract_filename_from_headers,
    find_referenced_cids,
    html_to_plain_text_alternative,
    http_error_detail,
    inline_cid_images,
    normalize_cid,
    parse_expiry,
    pick_gmail_send_strategy,
    plain_text_to_html,
    resolve_attachment_mime_type,
    retry_with_backoff,
    unwrap_app_credentials,
    unwrap_user_tokens,
    wrap_account_tokens,
)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_BATCH_SIZE = 100
_BATCH_MAX_RETRIES = 4
_BATCH_RETRY_DELAY = 1.0  # seconds
_INCREMENTAL_EVENT_THRESHOLD = 100
_DRAFTS_MAX_TOTAL = 100
_SEND_DRAFT_MAX_ATTEMPTS = 3
_SEND_DRAFT_RETRY_DELAY = 1.0  # seconds
# Decoupled from send retries on purpose — download failure modes (5xx,
# 429 with Retry-After, transient connection drops) deserve their own
# tuning even if today they happen to use the same value.
_FETCH_ATTACHMENT_MAX_ATTEMPTS = 3

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


def _parse_max_workers() -> int:
    raw = os.environ.get("GMAIL_BATCH_MAX_WORKERS", "5")
    try:
        return max(int(raw), 1)
    except (ValueError, TypeError):
        logger.warning("Invalid GMAIL_BATCH_MAX_WORKERS=%r, defaulting to 5", raw)
        return 5

_PARALLEL_MAX_WORKERS = _parse_max_workers()

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exception: Any) -> bool:
    """Classify whether a batch callback exception is worth retrying.

    Returns True for rate-limit (429), server errors (5xx), and non-HTTP
    exceptions (network timeouts, connection resets).  Returns False for
    client errors like 404, 400, 403, 410 — those are permanent failures.
    """
    if isinstance(exception, HttpError):
        status = getattr(getattr(exception, "resp", None), "status", None)
        return status in _RETRYABLE_STATUS_CODES
    return True


def _split_address_header(header_value: str) -> list[str]:
    """Decompose an RFC 5322 ``To`` / ``Cc`` / ``Reply-To`` header into
    a list of plain email addresses (no display name, no angle brackets).

    Uses :py:func:`email.utils.getaddresses` to tolerate display names,
    quoted local parts, multiple addresses separated by commas, and
    rare RFC 5322 § 3.4 group syntax. Empty / whitespace strings
    return ``[]``. Addresses without an ``@`` are dropped silently —
    they are usually leftover ``"Undisclosed recipients:;"`` group
    headers or malformed senders that would break downstream.
    """
    raw = (header_value or "").strip()
    if not raw:
        return []
    pairs = getaddresses([raw])
    out: list[str] = []
    for _name, addr in pairs:
        cleaned = (addr or "").strip()
        if not cleaned or "@" not in cleaned:
            continue
        out.append(cleaned)
    return out


def _first_recipient_from_to_header(header_value: str) -> tuple[str, str]:
    """Extract ``(name, email)`` of the first valid recipient in a ``To`` header.

    Same RFC 5322 parser as :py:func:`_split_address_header` but
    preserves the display name for the leading entry — used to populate
    ``EmailMetadata.to_email`` / ``to_name`` during sync. Returns
    ``("", "")`` when the header is empty or carries no address with
    an ``@`` (rare; service-side notifications mass-mailed via Bcc).
    """
    raw = (header_value or "").strip()
    if not raw:
        return "", ""
    for name, addr in getaddresses([raw]):
        cleaned_addr = (addr or "").strip()
        if not cleaned_addr or "@" not in cleaned_addr:
            continue
        return (name or "").strip(), cleaned_addr
    return "", ""


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


def _log_skipped_messages(
    operation: str, skipped_ids: list[str], message_ids: list[str],
) -> None:
    if skipped_ids:
        logger.warning(
            "Gmail %s: %d/%d messages could not be fetched: %s",
            operation, len(skipped_ids), len(message_ids), skipped_ids[:10],
        )


# ---------------------------------------------------------------------------
# Module-level helpers for the attachments flow.
# Kept at module level so multiple methods (and potentially other Gmail-
# specific callers) reuse them without bouncing through ``self``.
# ---------------------------------------------------------------------------


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


class GmailClient(EmailClient):
    """
    Concrete implementation of EmailClient for Gmail accounts.
    This class will be responsible for talking to the official Gmail API.
    """

    def __init__(
        self,
        account_label: str = "gmail",
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._account_label = account_label
        self.service = None
        self._credentials: Credentials | None = None
        self._sender_email: str | None = None
        # Injection point for the favourite-listing page retry loop so
        # tests do not wait on real backoff delays. Defaults to
        # ``time.sleep`` in production.
        self._sleep = sleep

    def begin_interactive_auth(
        self,
        app_credentials: dict[str, Any] | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """
        Build the Google authorization URL for a user-driven OAuth flow.

        The backend runs headless (container), so no browser is opened and
        no local callback server is started: the caller forwards the URL to
        the end user's browser and Google redirects to *redirect_uri*,
        which must be an HTTP endpoint of this API.
        """
        credentials_payload = unwrap_app_credentials(app_credentials)
        if not credentials_payload:
            raise EmailMissingAppCredentialsError("Gmail interactive auth requires app credentials.")
        resolved_redirect = str(redirect_uri or "").strip()
        if not resolved_redirect:
            raise EmailProviderConfigError("Gmail interactive auth requires a redirect_uri.")

        client_config = self._build_client_config(credentials_payload)
        try:
            flow = InstalledAppFlow.from_client_config(
                client_config, GMAIL_SCOPES, redirect_uri=resolved_redirect,
            )
        except Exception as exc:
            raise EmailInvalidCredentialsDataError(
                f"Gmail failed to build OAuth flow from app credentials: {exc}"
            ) from exc
        try:
            # prompt="consent" guarantees a refresh_token even when the user
            # already consented before (re-connecting a previously connected
            # account); without it Google may omit the refresh_token and the
            # account would silently die when the access token expires.
            authorization_url, state = flow.authorization_url(
                access_type="offline", prompt="consent",
            )
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected OAuth authorization URL error ({type(exc).__name__}): {exc}"
            ) from exc

        # The live Flow object carries the PKCE code_verifier generated for
        # this URL — the exchange must reuse it, so it travels (in-process
        # only) inside flow_state.
        return {
            "authorization_url": authorization_url,
            "state": state,
            "flow_state": {"flow": flow},
        }

    def complete_interactive_auth(
        self,
        app_credentials: dict[str, Any] | None = None,
        flow_state: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """
        Exchange the authorization code captured by the redirect callback
        for Gmail tokens. *app_credentials* is unused for Gmail (the flow
        object already carries the client config) but kept for signature
        parity across providers.
        """
        auth_code = str(code or "").strip()
        if not auth_code:
            raise EmailExternalAPIError("Gmail OAuth completion is missing the authorization code.")
        flow = (flow_state or {}).get("flow")
        if flow is None:
            raise EmailExternalAPIError("Gmail OAuth completion is missing the in-progress flow state.")

        try:
            flow.fetch_token(code=auth_code)
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail failed to exchange the authorization code ({type(exc).__name__}): {exc}"
            ) from exc

        creds = flow.credentials
        self._credentials = creds
        try:
            self.service = build("gmail", "v1", credentials=creds)
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail failed to initialize API service after connect ({type(exc).__name__}): {exc}"
            ) from exc
        email_address = self._fetch_sender_email()

        token_record = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "scopes": creds.scopes,
            "email_address": email_address or None,
        }
        return wrap_account_tokens(token_record)

    def authenticate_silent(
        self,
        app_credentials: dict[str, Any] | None = None,
        user_tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Authenticate the Gmail client without starting an interactive OAuth flow.
        """
        credentials_payload = unwrap_app_credentials(app_credentials)
        if not credentials_payload:
            raise EmailMissingAppCredentialsError("Gmail silent auth requires app credentials.")

        token_payload = unwrap_user_tokens(user_tokens)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise EmailMissingTokenError("Gmail silent auth requires access_token.")

        refresh_token = token_payload.get("refresh_token")
        expiry = parse_expiry(token_payload.get("expiry"))

        token_uri = credentials_payload.get("token_uri")
        client_id = credentials_payload.get("client_id")
        client_secret = credentials_payload.get("client_secret")
        missing_fields = [
            field for field in ("token_uri", "client_id", "client_secret")
            if not credentials_payload.get(field)
        ]
        if missing_fields:
            raise EmailMissingAppCredentialsError(
                f"Missing required app credentials for Gmail silent auth: {', '.join(missing_fields)}."
            )

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=token_payload.get("scopes") or GMAIL_SCOPES,
            expiry=expiry,
        )
        refreshed = False
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except TransportError as exc:
                raise EmailRefreshFailedError(f"Gmail token refresh transport error: {exc}") from exc
            except RefreshError as exc:
                raise EmailRefreshFailedError(
                    f"Gmail token refresh rejected by provider: {exc}"
                ) from exc
            except Exception as exc:
                raise EmailRefreshFailedError(
                    f"Gmail unexpected token refresh error ({type(exc).__name__}): {exc}"
                ) from exc
        elif creds.expired and not creds.refresh_token:
            raise EmailMissingRefreshTokenError("Gmail token expired and refresh_token is missing.")

        try:
            self.service = build("gmail", "v1", credentials=creds)
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail failed to initialize API service ({type(exc).__name__}): {exc}"
            ) from exc
        self._credentials = creds
        if refreshed:
            token_record = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
                "scopes": creds.scopes,
            }
            return wrap_account_tokens(token_record)
        return None

    def _build_client_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "installed" in payload or "web" in payload:
            return payload
        return {"installed": payload}

    # ------------------------------------------------------------------
    # Metadata sync
    # ------------------------------------------------------------------

    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        """
        Fetch email metadata from Gmail.

        Returns a SyncResult with upserts, deletes, label_updates and new_cursor.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_email_metadata requires authentication.")

        if sync_cursor is not None:
            # ------ Path 2: Incremental sync via users.history.list ------
            try:
                return self._incremental_email_metadata(sync_cursor)
            except EmailExternalAPIError:
                pass  # Fallback to bootstrap (e.g. cursor expired / 404 / 410)

        # ------ Path 1: Bootstrap (full fetch) ------
        return self._bootstrap_email_metadata(max_total)

    def _bootstrap_email_metadata(
        self,
        max_total: int,
    ) -> SyncResult:
        """Path 1: Full bootstrap fetch of message metadata."""
        history_id = self._get_current_history_id()
        message_ids = self._list_message_ids(max_total)
        metadata_list = self.fetch_messages_metadata(message_ids)
        return SyncResult(
            upserts=dedupe_metadata_by_message_id(metadata_list),
            new_cursor=history_id,
            is_full_sync=True,
        )

    def _list_message_ids(self, max_total: int) -> list[str]:
        """List message IDs using pagination, including spam and trash."""
        ids: list[str] = []
        page_token = None
        page_size = min(max_total, 500)

        while True:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "maxResults": page_size,
                "includeSpamTrash": True,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token

            try:
                response = self.service.users().messages().list(**list_kwargs).execute()
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail failed to fetch message list (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected fetch message list error ({type(exc).__name__}): {exc}"
                ) from exc

            for msg in response.get("messages", []):
                msg_id = str(msg.get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)

            if len(ids) >= max_total:
                ids = ids[:max_total]
                break

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return ids

    def _execute_single_chunk(
        self,
        chunk_ids: list[str],
        chunk_index: int,
        *,
        fmt: str,
        extra_kwargs: dict[str, Any] | None = None,
        credentials: Credentials,
        resource: str = "messages",
    ) -> tuple[dict[str, dict[str, Any]], list[str], list[str], float]:
        """Execute a single batch.execute() for one chunk of IDs.

        Each call builds its own thread-local HTTP transport and service
        because httplib2 is not thread-safe. Never raises — chunk-level
        errors mark all IDs as failed.

        The ``resource`` parameter selects between ``users().messages()``
        (default) and ``users().drafts()`` so the same parallel batch
        skeleton is reused for the drafts sync.

        Returns (successes, retryable_failed_ids, permanent_failed_ids, elapsed_seconds).
        """
        t0 = time.perf_counter()
        successes: dict[str, dict[str, Any]] = {}
        failed: list[str] = []
        permanent_failed: list[str] = []

        def _callback(
            request_id: str, response: Any, exception: Any,
            _s: dict[str, dict[str, Any]] = successes,
            _f: list[str] = failed,
            _pf: list[str] = permanent_failed,
        ) -> None:
            if exception is not None:
                if _is_retryable(exception):
                    _f.append(request_id)
                else:
                    _pf.append(request_id)
                return
            _s[request_id] = response

        try:
            thread_http = google_auth_httplib2.AuthorizedHttp(
                credentials, http=httplib2.Http(timeout=30),
            )
            thread_service = build("gmail", "v1", http=thread_http)

            batch = thread_service.new_batch_http_request(callback=_callback)
            for msg_id in chunk_ids:
                get_kwargs: dict[str, Any] = {
                    "userId": "me", "id": msg_id, "format": fmt,
                    **(extra_kwargs or {}),
                }
                if resource == "drafts":
                    request = thread_service.users().drafts().get(**get_kwargs)
                else:
                    request = thread_service.users().messages().get(**get_kwargs)
                batch.add(request, request_id=msg_id)
            batch.execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            if _is_retryable(exc):
                logger.warning(
                    "Chunk %d: batch.execute() HttpError (HTTP %s: %s) — all %d IDs marked retryable",
                    chunk_index, status, reason, len(chunk_ids),
                )
                return {}, list(chunk_ids), [], time.perf_counter() - t0
            logger.warning(
                "Chunk %d: batch.execute() HttpError (HTTP %s: %s) — all %d IDs marked permanently failed",
                chunk_index, status, reason, len(chunk_ids),
            )
            return {}, [], list(chunk_ids), time.perf_counter() - t0
        except Exception as exc:
            logger.warning(
                "Chunk %d: batch.execute() %s: %s — all %d IDs marked retryable",
                chunk_index, type(exc).__name__, exc, len(chunk_ids),
            )
            return {}, list(chunk_ids), [], time.perf_counter() - t0

        return successes, failed, permanent_failed, time.perf_counter() - t0

    def _execute_batch_get_sequential(
        self,
        message_ids: list[str],
        *,
        fmt: str,
        error_context: str,
        extra_kwargs: dict[str, Any] | None = None,
        resource: str = "messages",
    ) -> dict[str, dict[str, Any]]:
        """Fallback: sequential batch execution using self.service directly.

        ``resource`` selects between ``users().messages()`` (default) and
        ``users().drafts()`` so drafts sync can reuse the same skeleton.
        """
        all_results: dict[str, dict[str, Any]] = {}

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            chunk_results: dict[str, dict[str, Any]] = {}
            pending_ids = list(chunk)
            resolved_extra = extra_kwargs or {}

            for attempt in range(_BATCH_MAX_RETRIES + 1):
                failed_in_attempt: list[str] = []

                def _callback(
                    request_id: str, response: Any, exception: Any,
                    _r: dict[str, dict[str, Any]] = chunk_results,
                    _f: list[str] = failed_in_attempt,
                ) -> None:
                    if exception is not None:
                        if _is_retryable(exception):
                            _f.append(request_id)
                        return
                    _r[request_id] = response

                try:
                    batch = self.service.new_batch_http_request(callback=_callback)
                    for msg_id in pending_ids:
                        get_kwargs: dict[str, Any] = {
                            "userId": "me", "id": msg_id, "format": fmt,
                            **resolved_extra,
                        }
                        if resource == "drafts":
                            request = self.service.users().drafts().get(**get_kwargs)
                        else:
                            request = self.service.users().messages().get(**get_kwargs)
                        batch.add(request, request_id=msg_id)
                    batch.execute()
                except HttpError as exc:
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail {error_context} failed (HTTP {status}: {reason})."
                    ) from exc
                except Exception as exc:
                    raise EmailExternalAPIError(
                        f"Gmail unexpected {error_context} error ({type(exc).__name__}): {exc}"
                    ) from exc

                if not failed_in_attempt:
                    break

                if attempt < _BATCH_MAX_RETRIES:
                    logger.warning(
                        "Gmail batch: %d/%d messages failed (attempt %d/%d), retrying",
                        len(failed_in_attempt), len(pending_ids),
                        attempt + 1, _BATCH_MAX_RETRIES + 1,
                    )
                    time.sleep(_BATCH_RETRY_DELAY)
                    pending_ids = failed_in_attempt
                else:
                    logger.warning(
                        "Gmail batch: %d messages lost after %d attempts: %s",
                        len(failed_in_attempt), _BATCH_MAX_RETRIES + 1,
                        failed_in_attempt[:10],
                    )

            all_results.update(chunk_results)

        return all_results

    def _execute_batch_get(
        self,
        message_ids: list[str],
        *,
        fmt: str,
        error_context: str,
        extra_kwargs: dict[str, Any] | None = None,
        resource: str = "messages",
    ) -> dict[str, dict[str, Any]]:
        """Execute a batched messages.get call; returns {msg_id: response}.

        When credentials are available, chunks run in parallel via
        ThreadPoolExecutor. Falls back to sequential execution otherwise.

        ``resource`` selects between ``users().messages()`` (default) and
        ``users().drafts()`` — the drafts sync reuses this skeleton so
        the same parallel-batches-of-100 + retries logic applies.
        """
        if not message_ids:
            return {}

        if self._credentials is None:
            return self._execute_batch_get_sequential(
                message_ids, fmt=fmt, error_context=error_context,
                extra_kwargs=extra_kwargs, resource=resource,
            )

        all_results: dict[str, dict[str, Any]] = {}
        chunks: list[list[str]] = [
            message_ids[i:i + _BATCH_SIZE]
            for i in range(0, len(message_ids), _BATCH_SIZE)
        ]
        num_chunks = len(chunks)
        workers = min(_PARALLEL_MAX_WORKERS, num_chunks)

        pending_per_chunk: dict[int, list[str]] = {
            i: list(chunk) for i, chunk in enumerate(chunks)
        }
        creds = self._credentials

        logger.info(
            "Gmail parallel batch: %d chunk(s), %d worker(s), %d total IDs",
            num_chunks, workers, len(message_ids),
        )

        for attempt in range(_BATCH_MAX_RETRIES + 1):
            active_chunks = {
                i: ids for i, ids in pending_per_chunk.items() if ids
            }
            if not active_chunks:
                break

            if attempt > 0:
                logger.warning(
                    "Gmail parallel batch: retry %d/%d — %d IDs pending across %d chunk(s)",
                    attempt, _BATCH_MAX_RETRIES,
                    sum(len(ids) for ids in active_chunks.values()),
                    len(active_chunks),
                )
                time.sleep(_BATCH_RETRY_DELAY)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._execute_single_chunk,
                        ids, chunk_idx,
                        fmt=fmt,
                        extra_kwargs=extra_kwargs, credentials=creds,
                        resource=resource,
                    ): chunk_idx
                    for chunk_idx, ids in active_chunks.items()
                }
                for future in as_completed(futures):
                    chunk_idx = futures[future]
                    successes, failed, permanent_failed, _elapsed = future.result()
                    all_results.update(successes)
                    pending_per_chunk[chunk_idx] = failed
                    if permanent_failed:
                        logger.warning(
                            "Gmail parallel batch: %d permanent failures in chunk %d: %s",
                            len(permanent_failed), chunk_idx, permanent_failed[:10],
                        )

            total_pending = sum(len(ids) for ids in pending_per_chunk.values())
            if total_pending == 0:
                break

            if attempt == _BATCH_MAX_RETRIES:
                lost_ids = [
                    msg_id
                    for ids in pending_per_chunk.values()
                    for msg_id in ids
                ]
                logger.warning(
                    "Gmail parallel batch: %d messages lost after %d attempts: %s",
                    len(lost_ids), _BATCH_MAX_RETRIES + 1, lost_ids[:10],
                )

        return all_results

    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        """Fetch metadata for message IDs using Gmail BatchHttpRequest."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_messages_metadata requires authentication.")
        if not message_ids:
            return []
        raw = self._execute_batch_get(
            message_ids,
            fmt="metadata",
            error_context="batch metadata fetch",
            extra_kwargs={"metadataHeaders": ["From", "To", "Subject"]},
        )
        results: list[EmailMetadata] = []
        skipped_ids: list[str] = []
        for msg_id in message_ids:
            msg = raw.get(msg_id)
            if msg is None:
                skipped_ids.append(msg_id)
                continue
            try:
                results.append(self._parse_metadata_response(msg))
            except Exception as exc:
                logger.debug("Gmail skipped unparseable message %s: %s", msg_id, exc)
                skipped_ids.append(msg_id)
        _log_skipped_messages("metadata sync", skipped_ids, message_ids)
        return results

    @staticmethod
    def _resolve_labels(label_ids: list[str]) -> tuple[bool, str]:
        """Map Gmail label IDs to (is_read, box)."""
        labels = set(label_ids)
        is_read = "UNREAD" not in labels
        if "TRASH" in labels:
            box = "TRASH"
        elif "SPAM" in labels:
            box = "SPAM"
        elif "SENT" in labels:
            box = "SENT"
        elif "INBOX" in labels:
            box = "ALL_MAIL"
        else:
            # Received message with INBOX removed (and not SPAM/TRASH/SENT):
            # this is what "archived in Gmail" means — the message lives in
            # All Mail without the INBOX label.
            box = "ARCHIVE"
        return is_read, box

    @staticmethod
    def _parse_metadata_response(msg: dict[str, Any]) -> EmailMetadata:
        """Parse a Gmail message response (format=metadata) into EmailMetadata."""
        headers = {}
        for h in (msg.get("payload") or {}).get("headers", []):
            name = h.get("name")
            if name:
                headers[name] = h.get("value", "")

        from_header = headers.get("From", "")
        from_name, from_email = parseaddr(from_header)

        to_name, to_email = _first_recipient_from_to_header(headers.get("To", ""))

        is_read, box = GmailClient._resolve_labels(msg.get("labelIds") or [])

        internal_date = msg.get("internalDate")
        if internal_date:
            try:
                received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                received_at = datetime.now(timezone.utc)
        else:
            received_at = datetime.now(timezone.utc)

        return EmailMetadata(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("threadId", ""),
            from_email=from_email or "",
            from_name=from_name or "",
            subject=headers.get("Subject", ""),
            received_at=received_at,
            is_read=is_read,
            box=box,
            to_email=to_email,
            to_name=to_name,
        )

    def _fetch_sender_email(self) -> str:
        """Best-effort fetch of the authenticated user's email address (cached)."""
        if self._sender_email is not None:
            return self._sender_email
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            self._sender_email = profile.get("emailAddress", "")
        except Exception as exc:
            logger.debug("Gmail failed to fetch sender email: %s", exc)
            self._sender_email = ""
        return self._sender_email

    def _get_current_history_id(self) -> str:
        """Retrieve the current historyId from the user's Gmail profile."""
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            return str(profile.get("historyId", ""))
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to fetch profile for historyId (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected getProfile error ({type(exc).__name__}): {exc}"
            ) from exc

    def _incremental_email_metadata(
        self, sync_cursor: str,
    ) -> SyncResult:
        """Path 2: Incremental sync via Gmail History API."""
        # Step 1 — Paginate history.list and classify events into 3 sets
        need_get_ids: set[str] = set()
        pending_delete_ids: set[str] = set()
        label_change_ids: set[str] = set()
        new_history_id = sync_cursor

        page_token: str | None = None
        while True:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "startHistoryId": sync_cursor,
                "maxResults": 500,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token

            try:
                response = (
                    self.service.users().history().list(**list_kwargs).execute()
                )
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail history.list failed (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected history.list error ({type(exc).__name__}): {exc}"
                ) from exc

            new_history_id = str(response.get("historyId", new_history_id))

            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg_id = str(added.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        need_get_ids.add(msg_id)

                for deleted in record.get("messagesDeleted", []):
                    msg_id = str(deleted.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        pending_delete_ids.add(msg_id)

                for label_event in record.get("labelsAdded", []):
                    msg_id = str(label_event.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        label_change_ids.add(msg_id)

                for label_event in record.get("labelsRemoved", []):
                    msg_id = str(label_event.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        label_change_ids.add(msg_id)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Threshold check — abort if too many events (Step 1 is cheap, Steps 2-5 are not)
        total_event_ids = len(need_get_ids | pending_delete_ids | label_change_ids)
        if total_event_ids > _INCREMENTAL_EVENT_THRESHOLD:
            logger.info(
                "Gmail incremental: %d event IDs exceed threshold %d, falling back to bootstrap.",
                total_event_ids, _INCREMENTAL_EVENT_THRESHOLD,
            )
            raise EmailExternalAPIError(
                f"Gmail incremental sync has {total_event_ids} events, exceeding threshold."
            )

        # Step 2 — Resolve pending deletes: batch probe in groups of 100
        probe_ids = list(pending_delete_ids - need_get_ids)
        confirmed_deletes: list[str] = []
        if probe_ids:
            raw = self._execute_batch_get(
                probe_ids, fmt="minimal", error_context="delete probe",
            )
            for msg_id in probe_ids:
                if msg_id in raw:
                    need_get_ids.add(msg_id)
                else:
                    confirmed_deletes.append(msg_id)

        # Step 3 — Filter label changes: exclude messages already in get/delete
        label_only_ids = label_change_ids - need_get_ids - set(confirmed_deletes)

        # Step 4 — Batch fetch full metadata for need_get
        upserts = self.fetch_messages_metadata(list(need_get_ids)) if need_get_ids else []

        # Step 5 — Batch fetch label updates for label-only changes
        label_updates = (
            self._batch_fetch_label_updates(list(label_only_ids))
            if label_only_ids
            else []
        )

        return SyncResult(
            upserts=upserts,
            new_cursor=new_history_id,
            deletes=confirmed_deletes,
            label_updates=label_updates,
        )

    def _batch_fetch_label_updates(self, message_ids: list[str]) -> list[LabelUpdate]:
        """Fetch current labelIds for messages and build LabelUpdate objects."""
        raw = self._execute_batch_get(
            message_ids,
            fmt="minimal",
            error_context="batch label fetch",
        )
        results: list[LabelUpdate] = []
        skipped_ids: list[str] = []
        for msg_id in message_ids:
            msg = raw.get(msg_id)
            if msg is None:
                skipped_ids.append(msg_id)
                continue
            is_read, box = self._resolve_labels(msg.get("labelIds") or [])
            results.append(LabelUpdate(
                provider_message_id=msg.get("id", msg_id),
                is_read=is_read,
                box=box,
            ))
        _log_skipped_messages("label sync", skipped_ids, message_ids)
        return results

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

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

    @staticmethod
    def _build_draft_raw_message(
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        attachments: list[DraftAttachmentInput] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, bytes]:
        """Build the RFC 5322 message for Gmail drafts.create / drafts.update / drafts.send.

        Returns ``(raw_b64url, raw_bytes)``:
        - ``raw_b64url`` is the base64url-encoded form for the JSON
          ``{"message": {"raw": ...}}`` body shape (drafts.create,
          drafts.update, drafts.send simple path).
        - ``raw_bytes`` is the unencoded MIME content used by the
          resumable upload path (``message/rfc822`` body of the PUT).

        Body is HTML. Attachments are appended via
        :py:func:`build_mime_with_attachments` which delegates to
        Python's modern ``email.message.EmailMessage`` API and emits a
        ``multipart/mixed`` wrapping a ``multipart/alternative`` body
        (derived ``text/plain`` + ``text/html``) followed by each
        attachment carrying ``Content-Disposition: attachment``
        (RFC 2231 + RFC 2047 filename encoding).

        ``extra_headers`` carries arbitrary RFC 5322 header injections.
        Used by the Reply / Forward flow to add ``In-Reply-To`` and
        ``References`` so any destination client (Outlook, Apple Mail)
        re-threads even without our Gmail ``threadId``.
        """
        attachment_payloads = [
            {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "data": att.data,
                "content_id": att.content_id,
                "is_inline": att.is_inline,
            }
            for att in (attachments or [])
        ]
        raw_bytes = build_mime_with_attachments(
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            subject=subject or "",
            body=body or "",
            attachments=attachment_payloads,
            extra_headers=extra_headers,
        )
        return base64.urlsafe_b64encode(raw_bytes).decode("utf-8"), raw_bytes

    def create_draft(
        self,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        reply_to_message_id: str | None = None,
        reply_kind: str | None = None,
        original_subject: str | None = None,
    ) -> DraftMetadata:
        """
        Create a draft in Gmail via users().drafts().create(). All fields
        may be empty; Gmail accepts empty drafts. The body is HTML, sent
        as a ``multipart/alternative`` (derived ``text/plain`` +
        ``text/html``). Attachments are NOT pushed here — they are
        attached during ``send_draft_with_attachments`` (D-07).

        When ``thread_id`` is provided, Gmail's documented triple
        requirement applies: ``threadId`` + ``In-Reply-To`` / ``References``
        + matching ``Subject``. Coherence between those three is
        guaranteed locally by the service layer (which has access to
        the original :py:class:`ReplyContext` and runs
        :py:func:`validate_reply_threading_coherence` before invoking
        us) — so the client trusts the inputs and just propagates them
        to Gmail. ``original_subject`` is accepted to keep the
        contract symmetric with Outlook callers but unused here.

        ``reply_to_message_id`` and ``reply_kind`` are accepted for
        signature symmetry with Outlook but unused by Gmail (no
        ``createReply`` equivalent).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail create_draft requires authentication.")

        del reply_to_message_id, reply_kind, original_subject  # signature symmetry

        extra_headers: dict[str, str] | None = None
        if in_reply_to or references:
            extra_headers = {}
            if in_reply_to:
                extra_headers["In-Reply-To"] = in_reply_to
            if references:
                extra_headers["References"] = references

        raw_message, _ = self._build_draft_raw_message(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
            extra_headers=extra_headers,
        )

        message_payload: dict[str, Any] = {"raw": raw_message}
        if thread_id:
            message_payload["threadId"] = thread_id

        try:
            response = (
                self.service.users()
                .drafts()
                .create(userId="me", body={"message": message_payload})
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to create draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected create_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = response.get("id", "")
        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )

    def update_draft(
        self,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        """
        Update an existing Gmail draft via users().drafts().update().
        Full-field replacement — Gmail overwrites the entire draft with
        the new MIME message. The Gmail ``draft.id`` is preserved (the
        inner ``message.id`` may change, but we do not store it).
        Body is HTML, sent as a ``multipart/alternative`` (derived
        ``text/plain`` + ``text/html``).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail update_draft requires authentication.")

        raw_message, _ = self._build_draft_raw_message(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        try:
            (
                self.service.users()
                .drafts()
                .update(
                    userId="me",
                    id=provider_draft_id,
                    body={"message": {"raw": raw_message}},
                )
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to update draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected update_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )

    def delete_draft(self, provider_draft_id: str) -> None:
        """Delete a draft in Gmail via users().drafts().delete()."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail delete_draft requires authentication.")

        try:
            self.service.users().drafts().delete(
                userId="me", id=provider_draft_id,
            ).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to delete draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected delete_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_drafts(self) -> list[DraftMetadata]:
        """Fetch the most recent Gmail drafts (capped at _DRAFTS_MAX_TOTAL).

        Gmail requires two steps: (1) drafts.list (paginated) to collect up
        to _DRAFTS_MAX_TOTAL (100) draft IDs; (2) drafts.get for each ID to
        retrieve the full Message. Step (2) is executed via
        _execute_batch_get with resource="drafts" — same parallel-batches-of-100
        + 4-retries skeleton used by the email-metadata sync. With a cap of
        100 drafts this degrades to a single batch chunk, but the skeleton
        scales transparently if the cap is raised in the future.

        Gmail's drafts.list API does not support explicit ordering, but
        returns drafts in reverse chronological order by API convention
        (stable in practice, not guaranteed by the official docs).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_drafts requires authentication.")

        # _list_all_draft_ids translates its own provider exceptions into
        # EmailExternalAPIError (core/CLAUDE.md §3), so no wrapper is needed here.
        draft_ids = self._list_all_draft_ids()

        if not draft_ids:
            return []

        raw = self._execute_batch_get(
            draft_ids,
            fmt="full",
            error_context="batch drafts fetch",
            resource="drafts",
        )
        drafts: list[DraftMetadata] = []
        for item in raw.values():
            try:
                drafts.append(self._parse_gmail_draft(item))
            except Exception as exc:
                logger.warning(
                    "Gmail fetch_drafts: skipping unparseable draft %s: %s",
                    item.get("id", "?"), exc,
                )
        return drafts

    def _list_all_draft_ids(self) -> list[str]:
        """Paginated drafts.list — collects up to _DRAFTS_MAX_TOTAL draft IDs.

        Uses maxResults per page capped at both 500 (Gmail's documented max)
        and the remaining quota until _DRAFTS_MAX_TOTAL is reached. Stops
        paginating as soon as the total is hit.

        With the current cap (100), this resolves in a single drafts.list
        call — Gmail returns at most 100 IDs in one page and never follows
        nextPageToken.
        """
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < _DRAFTS_MAX_TOTAL:
            remaining = _DRAFTS_MAX_TOTAL - len(ids)
            page_size = min(500, remaining)
            # Translate the provider exception inside the method that makes the
            # call (core/CLAUDE.md §3), mirroring _list_message_ids — not two
            # frames up in fetch_drafts.
            try:
                response = (
                    self.service.users().drafts()
                    .list(userId="me", maxResults=page_size, pageToken=page_token)
                    .execute()
                )
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail failed to list drafts (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected drafts.list error ({type(exc).__name__}): {exc}"
                ) from exc
            for draft in response.get("drafts", []) or []:
                draft_id = draft.get("id")
                if draft_id:
                    ids.append(draft_id)
                    if len(ids) >= _DRAFTS_MAX_TOTAL:
                        break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return ids

    def _parse_gmail_draft(self, draft_response: dict[str, Any]) -> DraftMetadata:
        """Convert a Gmail drafts.get response (format=full) into DraftMetadata.

        Extracts subject/to/cc/bcc from headers and the body from the
        payload parts, **preferring the ``text/html`` part** so the
        rich-text composer is seeded with HTML. A legacy draft that only
        carries ``text/plain`` (created before the rich-text body, or by
        another client) is converted to HTML via
        :py:func:`plain_text_to_html` so the persisted ``body`` is always
        HTML — the migration fixes the local rows, this fixes any legacy
        draft that re-enters via ``sync_drafts``. Uses ``datetime.now()``
        for created_at/updated_at because Gmail does not expose a stable
        draft timestamp in the Message resource.
        """
        provider_draft_id = str(draft_response.get("id", ""))
        message = draft_response.get("message") or {}
        payload = message.get("payload") or {}
        headers = payload.get("headers") or []

        def _header(name: str) -> str:
            for h in headers:
                if (h.get("name") or "").lower() == name.lower():
                    return h.get("value") or ""
            return ""

        def _parse_addrs(raw: str) -> list[str]:
            # "Name <a@b.com>, Other <c@d.com>" → ["a@b.com", "c@d.com"]
            result: list[str] = []
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                if "<" in part and ">" in part:
                    start = part.find("<") + 1
                    end = part.find(">")
                    if start < end:
                        result.append(part[start:end].strip())
                        continue
                result.append(part)
            return result

        subject = _header("Subject")
        to_recipients = _parse_addrs(_header("To"))
        cc_recipients = _parse_addrs(_header("Cc"))
        bcc_recipients = _parse_addrs(_header("Bcc"))
        # Prefer the text/html part — drafts created/updated by the app
        # now carry an HTML body inside a multipart/alternative. A legacy
        # draft with only a text/plain part (pre-rich-text, or authored by
        # another client) is converted to HTML so the persisted body is
        # always HTML and the composer never shows raw newlines.
        html_body, text_body = self._extract_body_from_payload(payload)
        if html_body is not None:
            body = html_body
        else:
            body = plain_text_to_html(text_body) if text_body is not None else ""
        # The MIME serialization of the body parts appends a single
        # trailing newline that Gmail echoes back verbatim (verified for
        # both the text/plain and the text/html alternative). Strip exactly
        # one terminator (``\r\n`` first, then ``\n`` — order matters so a
        # CRLF does not leave a stray ``\r``) so the round-tripped HTML
        # matches what was composed. Skip the strip for a legacy body that
        # was just wrapped by ``plain_text_to_html`` (it ends in
        # ``</p>``, not a newline), so the guard is harmless there.
        if body.endswith("\r\n"):
            body = body[:-2]
        elif body.endswith("\n"):
            body = body[:-1]

        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )

    def delete_messages(self, message_ids: list[str]) -> list[str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail delete_messages requires authentication.")
        if not message_ids:
            return []
        # No-op: gmail.modify scope cannot call messages.delete (requires
        # restricted mail.google.com scope). Return all IDs as "succeeded" so
        # the service layer marks them DELETED locally. Gmail auto-cleans
        # trash after 30 days; reconciliation removes stale DELETED rows.
        return list(message_ids)

    def _execute_batch_modify(
        self,
        message_ids: list[str],
        request_builder: Callable[[str], Any],
        operation_name: str,
    ) -> list[str]:
        """Execute a batch modify operation (trash, untrash) in chunks.
        Returns the list of message IDs that succeeded."""
        succeeded: list[str] = []

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            failed: list[str] = []

            def _callback(
                request_id: str, response: Any, exception: Any,
                _s: list[str] = succeeded,
                _f: list[str] = failed,
            ) -> None:
                if exception is not None:
                    _f.append(request_id)
                    return
                _s.append(request_id)

            try:
                batch = self.service.new_batch_http_request(callback=_callback)
                for msg_id in chunk:
                    batch.add(request_builder(msg_id), request_id=msg_id)
                batch.execute()
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail {operation_name} batch failed (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected {operation_name} error ({type(exc).__name__}): {exc}"
                ) from exc

            if failed:
                logger.warning(
                    "Gmail %s: %d/%d messages failed in chunk: %s",
                    operation_name, len(failed), len(chunk), failed[:10],
                )

        return succeeded

    _BOX_TO_GMAIL_LABELS: dict[str, list[str]] = {
        "ALL_MAIL": ["INBOX"],
        "SPAM": ["SPAM"],
    }

    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_trash requires authentication.")
        if not items:
            return {}

        known = {mid: dest for mid, dest in items.items() if dest is not None}
        unknown = [mid for mid, dest in items.items() if dest is None]

        result: dict[str, str] = {}

        # Known destination: remove TRASH + add destination labels
        if known:
            succeeded = self._execute_batch_modify(
                list(known.keys()),
                lambda mid: self.service.users().messages().modify(
                    userId="me", id=mid,
                    body={
                        "removeLabelIds": ["TRASH"],
                        "addLabelIds": self._BOX_TO_GMAIL_LABELS.get(known[mid], []),
                    },
                ),
                "restore_from_trash",
            )
            result.update({mid: mid for mid in succeeded})

        # Unknown destination: untrash (restores original label state)
        if unknown:
            succeeded = self._execute_batch_modify(
                unknown,
                lambda mid: self.service.users().messages().untrash(userId="me", id=mid),
                "restore_from_trash_untrash",
            )
            result.update({mid: mid for mid in succeeded})

        return result

    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_trash requires authentication.")
        if not message_ids:
            return {}
        succeeded = self._execute_batch_modify(
            message_ids,
            lambda mid: self.service.users().messages().trash(userId="me", id=mid),
            "move_to_trash",
        )
        # Gmail ID doesn't change on trash
        return {mid: mid for mid in succeeded}

    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail verify_message_existence requires authentication.")
        if not message_ids:
            return []
        raw = self._execute_batch_get(
            message_ids, fmt="minimal", error_context="message existence verification",
        )
        return [msg_id for msg_id in message_ids if msg_id in raw]

    # ------------------------------------------------------------------
    # Read status
    # ------------------------------------------------------------------

    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        """Mark messages as read/unread via Gmail label modification. Returns IDs successfully updated."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail update_read_status requires authentication.")
        if not message_ids:
            return []
        if is_read:
            return self._batch_modify_labels(message_ids, remove_labels=["UNREAD"])
        else:
            return self._batch_modify_labels(message_ids, add_labels=["UNREAD"])

    # ------------------------------------------------------------------
    # Favourites — STARRED label (D-31bis)
    # ------------------------------------------------------------------

    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        """Toggle the STARRED label on a single Gmail message.

        Reuses :py:meth:`_batch_modify_labels` with a one-element list
        rather than calling ``users.messages.modify`` directly — the
        batch helper already centralises the retry policy, the
        per-chunk error classification, and the no-op behaviour for
        already-applied / already-removed labels (relevant for the
        idempotent toggle semantics of the favourites endpoint).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail set_favorite requires authentication.")
        if not provider_message_id:
            return
        labels = ["STARRED"]
        if is_favorite:
            updated = self._batch_modify_labels([provider_message_id], add_labels=labels)
        else:
            updated = self._batch_modify_labels([provider_message_id], remove_labels=labels)
        if not updated:
            raise EmailExternalAPIError(
                f"Gmail set_favorite did not affect message {provider_message_id}."
            )

    def list_favorite_ids(self) -> list[str]:
        """List message ids labelled STARRED via ``users.messages.list``.

        Each page (the initial request and every ``nextPageToken``) is
        retried on transient errors with the same manual loop the rest of
        the Gmail client uses (``_BATCH_MAX_RETRIES + 1`` = 5 attempts,
        fixed ``_BATCH_RETRY_DELAY`` = 1s between tries, classified by
        :py:func:`_is_retryable`). Google does not guarantee a
        ``Retry-After`` header, so the backoff is fixed (mirrors
        ``_batch_modify_labels`` / ``_execute_batch_get``). A transient
        hiccup on one page therefore no longer aborts the whole
        reconciliation. ``self._sleep`` keeps the waits instant in tests.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail list_favorite_ids requires authentication.")
        ids: list[str] = []
        page_token: str | None = None
        while True:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "labelIds": ["STARRED"],
                "maxResults": 500,
                "includeSpamTrash": True,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            response = self._list_favorites_page_with_retries(list_kwargs)
            for msg in response.get("messages", []) or []:
                msg_id = str(msg.get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                return ids

    def _list_favorites_page_with_retries(
        self, list_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch one STARRED-listing page, retrying transient failures.

        Mirrors the manual retry loop of ``_batch_modify_labels`` /
        ``_execute_batch_get``: up to ``_BATCH_MAX_RETRIES + 1`` attempts,
        a fixed ``_BATCH_RETRY_DELAY`` wait between them, and
        :py:func:`_is_retryable` deciding whether an exception is worth
        retrying. Non-retryable errors (404/400/403/410) propagate
        immediately as :py:class:`EmailExternalAPIError`; a still-failing
        transient after the last attempt does too.
        """
        last_exc: Exception | None = None
        for attempt in range(_BATCH_MAX_RETRIES + 1):
            try:
                return self.service.users().messages().list(**list_kwargs).execute()
            except HttpError as exc:
                if not _is_retryable(exc):
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail failed to list STARRED messages (HTTP {status}: {reason})."
                    ) from exc
                last_exc = exc
            except Exception as exc:
                # Network noise (timeouts, resets) is retryable; a genuinely
                # unexpected error is re-raised on the final attempt below.
                last_exc = exc
            if attempt < _BATCH_MAX_RETRIES:
                self._sleep(_BATCH_RETRY_DELAY)
        if isinstance(last_exc, HttpError):
            status, reason = http_error_detail(last_exc)
            raise EmailExternalAPIError(
                f"Gmail failed to list STARRED messages after "
                f"{_BATCH_MAX_RETRIES + 1} attempts (HTTP {status}: {reason})."
            ) from last_exc
        raise EmailExternalAPIError(
            f"Gmail failed to list STARRED messages after "
            f"{_BATCH_MAX_RETRIES + 1} attempts "
            f"({type(last_exc).__name__}: {last_exc})."
        ) from last_exc

    # ------------------------------------------------------------------
    # Spam operations
    # ------------------------------------------------------------------

    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Move messages to spam via Gmail label modification. Returns results for successfully moved messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_spam requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, add_labels=["SPAM"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Restore messages from spam via Gmail label modification. Returns results for successfully restored messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_spam requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(
            message_ids, remove_labels=["SPAM"], add_labels=["INBOX"],
        )
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    # ------------------------------------------------------------------
    # Archive operations
    # ------------------------------------------------------------------

    def move_to_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Archive messages via Gmail label modification (remove INBOX). The id does not change. Returns results for successfully archived messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_archive requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, remove_labels=["INBOX"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    def restore_from_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Unarchive messages via Gmail label modification (add INBOX). The id does not change. Returns results for successfully restored messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_archive requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, add_labels=["INBOX"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    # ------------------------------------------------------------------
    # Label batch helper
    # ------------------------------------------------------------------

    def _batch_modify_labels(
        self,
        message_ids: list[str],
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> list[str]:
        """Batch-modify labels on messages. Returns list of successfully modified IDs."""
        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        all_updated: list[str] = []

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            pending_ids = list(chunk)

            for attempt in range(_BATCH_MAX_RETRIES + 1):
                chunk_updated: list[str] = []
                failed_in_attempt: list[str] = []

                def _callback(
                    request_id: str, response: Any, exception: Any,
                    _u: list[str] = chunk_updated,
                    _f: list[str] = failed_in_attempt,
                ) -> None:
                    if exception is not None:
                        if _is_retryable(exception):
                            _f.append(request_id)
                        # Non-retryable (404, 400, etc.) → silently skip
                        return
                    _u.append(request_id)

                try:
                    batch = self.service.new_batch_http_request(callback=_callback)
                    for msg_id in pending_ids:
                        batch.add(
                            self.service.users().messages().modify(
                                userId="me", id=msg_id, body=body,
                            ),
                            request_id=msg_id,
                        )
                    batch.execute()
                except HttpError as exc:
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail batch label modify failed (HTTP {status}: {reason})."
                    ) from exc
                except Exception as exc:
                    raise EmailExternalAPIError(
                        f"Gmail unexpected batch label modify error ({type(exc).__name__}): {exc}"
                    ) from exc

                all_updated.extend(chunk_updated)

                if not failed_in_attempt:
                    break

                if attempt < _BATCH_MAX_RETRIES:
                    logger.warning(
                        "Gmail batch modify: %d/%d messages failed (attempt %d/%d), retrying",
                        len(failed_in_attempt), len(pending_ids),
                        attempt + 1, _BATCH_MAX_RETRIES + 1,
                    )
                    time.sleep(_BATCH_RETRY_DELAY)
                    pending_ids = failed_in_attempt
                else:
                    logger.warning(
                        "Gmail batch modify: %d messages lost after %d attempts: %s",
                        len(failed_in_attempt), _BATCH_MAX_RETRIES + 1,
                        failed_in_attempt[:10],
                    )

        return all_updated

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

    def get_account_label(self) -> str:
        """
        Return the label that identifies this Gmail account inside the app.
        """
        return self._account_label
