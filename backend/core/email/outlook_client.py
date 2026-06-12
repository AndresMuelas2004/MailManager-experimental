from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

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
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
    EmailExternalAPIError,
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailNotAuthenticatedError,
    EmailRecipientsMissingError,
    EmailRefreshFailedError,
    EmailReplyContextFetchError,
)
from .helpers import (
    OutlookAttachmentStrategy,
    find_referenced_cids,
    flatten_html_document,
    inline_cid_images,
    parse_expiry,
    pick_outlook_attachment_strategy,
    plain_text_to_html,
    resolve_attachment_mime_type,
    unwrap_app_credentials,
    unwrap_user_tokens,
    wrap_account_tokens,
)

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
    "offline_access",
]

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

logger = logging.getLogger(__name__)

_DELTA_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,isRead"
)
_DELTA_PAGE_SIZE = 100

_DRAFTS_PAGE_SIZE = 100
_DRAFTS_MAX_RETRIES = 4
_DRAFTS_RETRY_DELAY = 1.0  # seconds
_DRAFTS_MAX_TOTAL = 100
_SEND_DRAFT_MAX_ATTEMPTS = 3
_SEND_DRAFT_RETRY_DELAY = 1.0  # seconds

# All attachment-touching calls send this header per request — see
# repository_guide.md and adjuntos-outlook.md § 6.1. Without it, Graph
# may interpret the path id as a mutable folder-scoped id and our
# stored ImmutableId lookups fail.
_PREFER_IMMUTABLE_HEADERS: dict[str, str] = {"Prefer": 'IdType="ImmutableId"'}

# createUploadSession / chunked PUT settings — see § 8.1 of the Outlook
# attachments doc. 4 MB is the recommended chunk size; smaller chunks
# multiply HTTP round trips, larger chunks waste bandwidth on retries.
_OUTLOOK_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
_OUTLOOK_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)

# Transient Graph statuses worth retrying. Kept identical to the literal
# already classified inline by the attachment loops (and to Gmail's
# ``_RETRYABLE_STATUS_CODES``) so both providers share one transient set.
# ``509`` / ``409`` are deliberately excluded in MVP — see
# docs/limits/favoritos.md.
_OUTLOOK_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

_BOOTSTRAP_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,"
    "receivedDateTime,isRead,parentFolderId"
)

# Conversation view ($filter=conversationId): bodies are fetched lazily
# per message via fetch_email_content, so the select stays lightweight.
# Adds ``sentDateTime`` (ordering fallback for Sent items) and ``flag``
# (favourite state) on top of the bootstrap fields.
_CONVERSATION_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,"
    "receivedDateTime,sentDateTime,isRead,parentFolderId,flag"
)

_DELTA_FOLDERS = ("inbox", "sentitems", "drafts", "deleteditems", "junkemail", "archive")

_FOLDER_TO_BOX: dict[str, str] = {
    "deleteditems": "TRASH",
    "junkemail": "SPAM",
    "sentitems": "SENT",
}

_BOX_TO_FOLDER: dict[str, str] = {
    "ALL_MAIL": "inbox",
    "SENT": "sentitems",
    "SPAM": "junkemail",
}


# ---------------------------------------------------------------------------
# Module-level helpers for the attachments flow.
# Kept at module level so they stay testable without instantiating a
# full ``OutlookClient`` and reusable across multiple methods (chunk
# uploads, downloads, send orchestration).
# ---------------------------------------------------------------------------


def _parse_graph_datetime(raw: Any) -> datetime:
    """Parse a Graph ISO-8601 timestamp (``…Z``) into an aware ``datetime``.

    Falls back to ``now(UTC)`` when the value is empty or malformed so a
    single bad timestamp never aborts a thread/sync parse.
    """
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _retry_after_seconds(headers: dict[str, str] | None) -> float | None:
    """Extract ``Retry-After`` (seconds) from a Graph response header dict.

    Both 429 and (occasionally) 503 carry this header. Outlook on Graph
    confirms it is the only way to know how long to wait —
    ``Rate-Limit-*`` headers are absent on Graph (see
    ``adjuntos-outlook.md`` § 5.4).
    """
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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


class OutlookClient(EmailClient):
    """
    Concrete implementation of EmailClient for Outlook accounts.
    This class talks to Microsoft Graph API for mail operations
    and to the Microsoft identity platform for OAuth2 authentication.
    """

    def __init__(
        self,
        account_label: str = "outlook",
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._account_label = account_label
        self._access_token: str | None = None
        self._sender_email: str | None = None
        self._sender_name: str | None = None
        # Injection point for the favourite retry loops so tests do not
        # wait on real backoff delays. Defaults to ``time.sleep`` in
        # production.
        self._sleep = sleep

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def begin_interactive_auth(
        self,
        app_credentials: dict[str, Any] | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """
        Build the Microsoft authorization URL (PKCE) for a user-driven flow.

        The backend runs headless (container), so no browser is opened and
        no local callback server is started: the caller forwards the URL to
        the end user's browser and Microsoft redirects to the configured
        ``redirect_uri`` (an HTTP endpoint of this API, registered in the
        Azure app registration). When *redirect_uri* is not given, the one
        declared in the app credentials JSON is used.
        """
        credentials_payload = unwrap_app_credentials(app_credentials)
        if not credentials_payload:
            raise EmailMissingAppCredentialsError()

        client_id = str(credentials_payload.get("client_id") or "").strip()
        client_secret = str(credentials_payload.get("client_secret") or "").strip()
        tenant = str(credentials_payload.get("tenant") or "common").strip()
        resolved_redirect = str(redirect_uri or credentials_payload.get("redirect_uri") or "").strip()
        scopes = self._resolve_scopes(credentials_payload)

        if not client_id or not client_secret or not resolved_redirect or not scopes:
            raise EmailMissingAppCredentialsError("Missing required app credentials.")

        # PKCE code verifier / challenge
        code_verifier = secrets.token_urlsafe(72)[:128]
        if len(code_verifier) < 43:
            code_verifier = f"{code_verifier}{'x' * (43 - len(code_verifier))}"
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("utf-8")).digest()
            )
            .rstrip(b"=")
            .decode("utf-8")
        )
        state = secrets.token_urlsafe(32)

        authorize_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
        auth_query = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": resolved_redirect,
            "response_mode": "query",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        auth_url = f"{authorize_url}?{urllib.parse.urlencode(auth_query)}"

        return {
            "authorization_url": auth_url,
            "state": state,
            "flow_state": {
                "code_verifier": code_verifier,
                "redirect_uri": resolved_redirect,
                "tenant": tenant,
                "scopes": scopes,
            },
        }

    def complete_interactive_auth(
        self,
        app_credentials: dict[str, Any] | None = None,
        flow_state: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """
        Exchange the authorization code captured by the redirect callback
        for Outlook tokens, reusing the PKCE verifier generated by
        ``begin_interactive_auth``.
        """
        credentials_payload = unwrap_app_credentials(app_credentials)
        if not credentials_payload:
            raise EmailMissingAppCredentialsError()

        auth_code = str(code or "").strip()
        if not auth_code:
            raise EmailExternalAPIError("Outlook OAuth completion is missing the authorization code.")

        state_payload = dict(flow_state or {})
        code_verifier = str(state_payload.get("code_verifier") or "").strip()
        resolved_redirect = str(state_payload.get("redirect_uri") or "").strip()
        if not code_verifier or not resolved_redirect:
            raise EmailExternalAPIError("Outlook OAuth completion is missing the in-progress flow state.")

        client_id = str(credentials_payload.get("client_id") or "").strip()
        client_secret = str(credentials_payload.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise EmailMissingAppCredentialsError("Missing required app credentials.")

        tenant = str(state_payload.get("tenant") or credentials_payload.get("tenant") or "common").strip()
        raw_scopes = state_payload.get("scopes")
        scopes = (
            [str(s).strip() for s in raw_scopes if str(s).strip()]
            if isinstance(raw_scopes, (list, tuple))
            else []
        ) or self._resolve_scopes(credentials_payload)
        token_url = self._token_url(tenant)

        try:
            token_response = self._token_request(token_url, {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": auth_code,
                "redirect_uri": resolved_redirect,
                "code_verifier": code_verifier,
                "scope": " ".join(scopes),
            })
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected token exchange error ({type(exc).__name__}): {exc}"
            ) from exc

        access_token = token_response.get("access_token")
        if not access_token:
            raise EmailExternalAPIError("Outlook failed to obtain access token: token response missing access_token.")

        self._access_token = access_token
        email_address, _display_name = self._fetch_sender_profile()

        token_record = {
            "access_token": access_token,
            "refresh_token": token_response.get("refresh_token"),
            "expiry": self._compute_expiry(token_response.get("expires_in")),
            "scopes": scopes,
            "email_address": email_address or None,
        }
        return wrap_account_tokens(token_record)

    def authenticate_silent(
        self,
        app_credentials: dict[str, Any] | None = None,
        user_tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Non-interactive authentication: reuse a valid access token or refresh it.
        Returns updated wrapped tokens when a refresh occurs, None otherwise.
        Microsoft may rotate the refresh token on every refresh, so both the
        access and refresh tokens are persisted whenever a refresh happens.
        """
        credentials_payload = unwrap_app_credentials(app_credentials)
        if not credentials_payload:
            raise EmailMissingAppCredentialsError()

        token_payload = unwrap_user_tokens(user_tokens)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise EmailMissingTokenError()

        refresh_token = token_payload.get("refresh_token")
        expiry = parse_expiry(token_payload.get("expiry"))

        # Determine whether the current access token has expired.
        is_expired = False
        if expiry is not None:
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            is_expired = expiry <= now_utc

        if not is_expired:
            self._access_token = access_token
            return None

        if not refresh_token:
            raise EmailMissingRefreshTokenError()

        client_id = str(credentials_payload.get("client_id") or "").strip()
        client_secret = str(credentials_payload.get("client_secret") or "").strip()
        tenant = str(credentials_payload.get("tenant") or "common").strip()
        scopes = self._resolve_scopes(credentials_payload, token_payload)

        if not client_id or not client_secret:
            raise EmailMissingAppCredentialsError("Missing required app credentials.")

        token_url = self._token_url(tenant)

        try:
            token_response = self._token_request(token_url, {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "scope": " ".join(scopes),
            })
        except EmailExternalAPIError as exc:
            raise EmailRefreshFailedError(f"Outlook failed to refresh access token: {exc}") from exc
        except Exception as exc:
            raise EmailRefreshFailedError(
                f"Outlook unexpected refresh token exchange error ({type(exc).__name__}): {exc}"
            ) from exc

        new_access_token = token_response.get("access_token")
        if not new_access_token:
            raise EmailRefreshFailedError("Outlook failed to refresh access token: response missing access_token.")

        # Microsoft may return a new refresh token (rotating tokens).
        new_refresh_token = token_response.get("refresh_token") or refresh_token

        self._access_token = new_access_token

        token_record = {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "expiry": self._compute_expiry(token_response.get("expires_in")),
            "scopes": scopes,
        }
        return wrap_account_tokens(token_record)

    # ------------------------------------------------------------------
    # Email operations
    # ------------------------------------------------------------------

    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        """Fetch email metadata from Outlook using Microsoft Graph Delta Query."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_email_metadata requires authentication.")

        if sync_cursor is not None:
            try:
                return self._incremental_email_metadata(sync_cursor)
            except EmailExternalAPIError:
                pass  # Fallback to bootstrap (e.g. expired deltaLink)

        return self._bootstrap_email_metadata(max_total)

    # ------------------------------------------------------------------
    # Metadata sync — private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_folder_cursors(folder_cursors: dict[str, str]) -> str:
        """Serialize per-folder deltaLinks into a JSON cursor string."""
        return json.dumps({"v": 1, "folders": folder_cursors})

    @staticmethod
    def _decode_folder_cursors(sync_cursor: str) -> dict[str, str] | None:
        """Deserialize a JSON cursor string. Returns None if invalid or legacy format."""
        try:
            data = json.loads(sync_cursor)
            if isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get("folders"), dict):
                return data["folders"]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _parse_graph_message(msg: dict[str, Any], box: str) -> EmailMetadata:
        """Parse a Microsoft Graph message resource into EmailMetadata."""
        from_obj = msg.get("from") or {}
        email_address = from_obj.get("emailAddress") or {}

        to_name, to_email = OutlookClient._first_recipient_from_graph_recipients(
            msg.get("toRecipients"),
        )

        received_at = _parse_graph_datetime(msg.get("receivedDateTime", ""))

        return EmailMetadata(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("conversationId") or "",
            from_email=email_address.get("address") or "",
            from_name=email_address.get("name") or "",
            subject=msg.get("subject") or "",
            received_at=received_at,
            is_read=msg.get("isRead", False),
            box=box,
            to_email=to_email,
            to_name=to_name,
        )

    def _fetch_folder_delta(
        self,
        folder_name: str,
        url: str,
        upserts: list[EmailMetadata],
        deletes: list[str],
        max_collect: int | None = None,
        label_updates: list[LabelUpdate] | None = None,
    ) -> str:
        """Paginate a single folder's delta query until deltaLink is obtained.

        Appends parsed messages to *upserts* and removed IDs to *deletes*.
        When *label_updates* is provided, partial delta messages (missing
        ``from``) are appended there instead of being parsed as full upserts.
        When *max_collect* is set, stops collecting messages after the limit
        but continues paginating to obtain the deltaLink.
        Returns the deltaLink.
        """
        box = _FOLDER_TO_BOX.get(folder_name, "ALL_MAIL")
        collected_enough = False

        while True:
            response = self._graph_request("GET", url)
            if not collected_enough:
                for msg in response.get("value", []):
                    if max_collect is not None and len(upserts) >= max_collect:
                        collected_enough = True
                        break
                    if msg.get("@removed"):
                        msg_id = msg.get("id", "")
                        if msg_id:
                            deletes.append(msg_id)
                            logger.debug(
                                "Outlook delta [%s] REMOVED id=%s",
                                folder_name, msg_id,
                            )
                    elif "from" not in msg and label_updates is not None:
                        label_updates.append(LabelUpdate(
                            provider_message_id=msg["id"],
                            is_read=msg.get("isRead", False),
                            box=box,
                        ))
                        logger.debug(
                            "Outlook delta [%s] LABEL id=%s is_read=%s",
                            folder_name, msg["id"], msg.get("isRead"),
                        )
                    else:
                        try:
                            upserts.append(self._parse_graph_message(msg, box))
                            logger.debug(
                                "Outlook delta [%s] UPSERT id=%s subject=%r box=%s",
                                folder_name, msg.get("id", "?"),
                                msg.get("subject", "?"), box,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Outlook delta (%s): skipping unparseable message %s: %s",
                                folder_name, msg.get("id", "?"), exc,
                            )

            next_link = response.get("@odata.nextLink")
            delta_link = response.get("@odata.deltaLink")

            if delta_link:
                return delta_link

            if not next_link:
                logger.warning("Outlook delta (%s): response has neither nextLink nor deltaLink", folder_name)
                return ""

            url = next_link

    def _resolve_special_folder_ids(self) -> dict[str, str]:
        """Fetch Graph IDs for sentitems, deleteditems and junkemail.

        Returns a mapping {folder_id: box} so that each message's
        parentFolderId can be classified into SENT, TRASH or SPAM.
        Any folder not in this mapping defaults to ALL_MAIL.
        """
        folder_id_to_box: dict[str, str] = {}
        for folder_name, box in _FOLDER_TO_BOX.items():
            try:
                url = f"{GRAPH_BASE_URL}/me/mailFolders/{folder_name}?$select=id"
                response = self._graph_request("GET", url)
                folder_id = response.get("id", "")
                if folder_id:
                    folder_id_to_box[folder_id] = box
            except EmailExternalAPIError:
                logger.warning(
                    "Outlook bootstrap: failed to resolve folder '%s', skipping.",
                    folder_name,
                )
        return folder_id_to_box

    def _fetch_recent_messages(
        self,
        max_total: int,
        folder_id_to_box: dict[str, str],
    ) -> list[EmailMetadata]:
        """Fetch the most recent messages across all folders via GET /me/messages.

        Uses $orderby=receivedDateTime desc so the API returns messages
        sorted by date (most recent first).  The parentFolderId of each
        message is compared against *folder_id_to_box* to assign the
        correct box value; anything not matched defaults to ALL_MAIL.
        """
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            f"?$select={_BOOTSTRAP_SELECT_FIELDS}"
            f"&$orderby=receivedDateTime+desc"
            f"&$top={min(max_total, 1000)}"
        )
        upserts: list[EmailMetadata] = []

        while url and len(upserts) < max_total:
            response = self._graph_request("GET", url)
            for msg in response.get("value", []):
                if len(upserts) >= max_total:
                    break
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                try:
                    upserts.append(self._parse_graph_message(msg, box))
                except Exception as exc:
                    logger.warning(
                        "Outlook bootstrap: skipping unparseable message %s: %s",
                        msg.get("id", "?"), exc,
                    )
            url = response.get("@odata.nextLink")

        return upserts

    def _bootstrap_email_metadata(self, max_total: int) -> SyncResult:
        """Path 1: Fetch most recent messages across all folders, then init delta cursors."""
        # Step 1: Discover special folder IDs for box classification.
        folder_id_to_box = self._resolve_special_folder_ids()

        # Step 2: Fetch the most recent messages across all folders.
        upserts = self._fetch_recent_messages(max_total, folder_id_to_box)

        # Step 3: Initialize per-folder delta cursors for future incremental syncs.
        folder_cursors: dict[str, str] = {}
        for folder in _DELTA_FOLDERS:
            url = (
                f"{GRAPH_BASE_URL}/me/mailFolders/{folder}/messages/delta"
                f"?$select={_DELTA_SELECT_FIELDS}&$top={_DELTA_PAGE_SIZE}"
            )
            try:
                delta_link = self._fetch_folder_delta(
                    folder, url, [], [],
                    max_collect=0,
                )
                if delta_link:
                    folder_cursors[folder] = delta_link
            except EmailExternalAPIError:
                logger.warning(
                    "Outlook bootstrap: delta init for '%s' failed, skipping.",
                    folder,
                )

        return SyncResult(
            upserts=upserts,
            new_cursor=self._encode_folder_cursors(folder_cursors),
            is_full_sync=True,
        )

    def _incremental_email_metadata(self, sync_cursor: str) -> SyncResult:
        """Path 2: Incremental sync via per-folder stored deltaLinks."""
        folder_cursors = self._decode_folder_cursors(sync_cursor)
        if folder_cursors is None:
            raise EmailExternalAPIError("Outlook: invalid or legacy sync cursor, falling back to bootstrap.")

        upserts: list[EmailMetadata] = []
        deletes: list[str] = []
        label_updates: list[LabelUpdate] = []
        new_cursors: dict[str, str] = {}
        all_failed = True

        for folder, delta_link in folder_cursors.items():
            try:
                new_delta = self._fetch_folder_delta(
                    folder, delta_link, upserts, deletes,
                    label_updates=label_updates,
                )
                new_cursors[folder] = new_delta if new_delta else delta_link
                all_failed = False
            except EmailExternalAPIError:
                logger.warning("Outlook incremental: folder '%s' failed, keeping previous cursor.", folder)
                new_cursors[folder] = delta_link

        if all_failed and folder_cursors:
            raise EmailExternalAPIError("Outlook: all folder delta queries failed.")

        return SyncResult(
            upserts=upserts,
            new_cursor=self._encode_folder_cursors(new_cursors),
            deletes=deletes,
            label_updates=label_updates,
        )

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

    @staticmethod
    def _parse_graph_datetime(value: Any) -> datetime:
        """Parse an ISO-8601 datetime from a Graph response with soft
        fallback to ``datetime.now(timezone.utc)``. Accepts both ``Z``
        suffix and ``+00:00`` offsets.
        """
        if isinstance(value, str) and value:
            try:
                raw = value[:-1] + "+00:00" if value.endswith("Z") else value
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _build_draft_graph_payload(
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Build the Graph Message payload used by both create_draft
        (POST /me/messages) and update_draft (PATCH /me/messages/{id}),
        and reused (nested under ``{"message": ...}``) by
        ``createReply`` / ``createReplyAll`` / ``createForward``. Both
        endpoints accept the same shape; update semantically replaces
        every listed field with the new value.

        Body is sent as ``contentType: "HTML"``. The composer emits
        sanitised HTML (negrita, cursiva, listas, enlaces); Graph accepts
        arbitrary HTML on write and re-wraps / sanitises it on read (the
        round-trip is handled by :py:meth:`_parse_outlook_draft`).
        """
        return {
            "subject": subject or "",
            "body": {"contentType": "HTML", "content": body or ""},
            "toRecipients": [{"emailAddress": {"address": r}} for r in to_recipients],
            "ccRecipients": [{"emailAddress": {"address": r}} for r in cc_recipients],
            "bccRecipients": [{"emailAddress": {"address": r}} for r in bcc_recipients],
        }

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
        Create a draft in Outlook.

        - **Standalone draft** (no ``reply_to_message_id`` / ``reply_kind``):
          ``POST /me/messages`` with the message payload — same path
          the composer has always used. ``thread_id`` / ``in_reply_to`` /
          ``references`` are accepted for signature symmetry with Gmail
          but ignored on the wire (Outlook needs ``createReply`` to
          fix ``conversationId``).

        - **Reply / Reply All / Forward draft** (``reply_to_message_id``
          and ``reply_kind`` both set): routes through
          :py:meth:`_create_draft_via_reply`, which calls
          ``POST /me/messages/{id}/createReply`` /
          ``createReplyAll`` / ``createForward`` with the message body
          in the JSON payload — a single round trip per R-09. The
          provider stitches ``conversationId`` server-side.

        CRITICAL: every Graph call here sends
        ``Prefer: IdType="ImmutableId"`` so the returned id stays stable
        across state transitions (e.g. when the draft is later sent).
        Without this header, Outlook may return a mutable ID that
        changes on send and break any future ``GET`` / ``PATCH`` /
        ``DELETE`` keyed by ``provider_draft_id``.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook create_draft requires authentication.")

        del thread_id, in_reply_to, references, original_subject  # symmetry with Gmail

        payload = self._build_draft_graph_payload(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        if reply_to_message_id and reply_kind in ("reply", "reply_all", "forward"):
            return self._create_draft_via_reply(
                reply_to_message_id=reply_to_message_id,
                kind=reply_kind,
                message_payload=payload,
                to_recipients=to_recipients,
                cc_recipients=cc_recipients,
                bcc_recipients=bcc_recipients,
                subject=subject,
                body=body,
            )

        try:
            response = self._graph_request(
                "POST",
                f"{GRAPH_BASE_URL}/me/messages",
                body=payload,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected create_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = str(response.get("id", ""))
        created_at = self._parse_graph_datetime(response.get("createdDateTime"))
        updated_at = self._parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _create_draft_via_reply(
        self,
        *,
        reply_to_message_id: str,
        kind: str,
        message_payload: dict[str, Any],
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        """``createReply`` / ``createReplyAll`` / ``createForward`` with body JSON (R-09).

        Single round trip — the message payload (subject, body,
        recipients) is sent in the same call, so we don't need a
        follow-up PATCH. Graph fixes ``conversationId`` server-side so
        the draft and the eventual sent message belong to the original
        thread.

        Pre-validates ``toRecipients`` for ``reply`` / ``reply_all``:
        Graph's documented XOR constraint requires at least one
        ``toRecipients`` (either root or under ``message``); skipping
        it would produce a guaranteed 400 we want to avoid paying
        quota for. ``forward`` is allowed with empty recipients
        because the composer fills them later, and Graph allows the
        forward draft to land in Drafts even if the user has yet to
        type a recipient.
        """
        endpoint_map = {
            "reply": "createReply",
            "reply_all": "createReplyAll",
            "forward": "createForward",
        }
        endpoint = endpoint_map.get(kind)
        if endpoint is None:
            raise EmailExternalAPIError(
                f"Outlook _create_draft_via_reply called with invalid kind '{kind}'."
            )
        recipients = message_payload.get("toRecipients") or []
        if kind in ("reply", "reply_all") and not recipients:
            # Pre-check: the XOR constraint of the createReply body schema
            # rejects a payload with no toRecipients at all. Surfacing
            # locally produces a deterministic error instead of an
            # opaque 400.
            raise EmailRecipientsMissingError(
                f"Outlook {endpoint} requires at least one recipient."
            )

        encoded_id = urllib.parse.quote(reply_to_message_id, safe="")
        url = f"{GRAPH_BASE_URL}/me/messages/{encoded_id}/{endpoint}"

        # Body shape (R-09 / §15.2): ONLY ``message`` — no ``comment``,
        # no ``toRecipients`` at the root. Sending either alongside
        # ``message.body`` / ``message.toRecipients`` produces a 400
        # by Graph's XOR constraint.
        request_body = {"message": message_payload}

        try:
            response = self._graph_request(
                "POST", url, body=request_body,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected {endpoint} error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = str(response.get("id", ""))
        created_at = self._parse_graph_datetime(response.get("createdDateTime"))
        updated_at = self._parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
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
        Update an existing Outlook draft via PATCH /me/messages/{id}.

        The ``Prefer: IdType="ImmutableId"`` header is repeated on every
        subsequent call because the stored ``provider_draft_id`` is an
        Immutable ID (created with that same header). Without the header
        Graph may interpret the path parameter as a mutable ID and fail
        the lookup.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook update_draft requires authentication.")

        payload = self._build_draft_graph_payload(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        try:
            response = self._graph_request(
                "PATCH",
                f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(provider_draft_id, safe='')}",
                body=payload,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected update_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        returned_id = str(response.get("id") or provider_draft_id)
        created_at = self._parse_graph_datetime(response.get("createdDateTime"))
        updated_at = self._parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=returned_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
        )

    def delete_draft(self, provider_draft_id: str) -> None:
        """Delete a draft in Outlook via DELETE /me/messages/{id}.

        Uses Prefer: IdType="ImmutableId" because the provider_draft_id
        stored locally was obtained with that header during create_draft.
        Without it, Graph would interpret the ID as a mutable folder-scoped
        ID and the request would fail.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook delete_draft requires authentication.")

        try:
            self._graph_request(
                "DELETE",
                f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(provider_draft_id, safe='')}",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected delete_draft error ({type(exc).__name__}): {exc}"
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

    def fetch_drafts(self) -> list[DraftMetadata]:
        """Fetch the most recent Outlook drafts (capped at _DRAFTS_MAX_TOTAL).

        Unlike Gmail, a single paginated endpoint returns the complete
        Message object per draft (subject, body, recipients, timestamps)
        so there is no need for a second round of per-draft get calls.
        The cost of parallelism doesn't apply here because OData pagination
        is sequential by design (each page yields the URL of the next one).

        Uses $orderby=lastModifiedDateTime desc so the most recently edited
        drafts come first — with a cap of 100, this means the caller always
        receives the 100 most recently modified drafts.

        Each page fetch is retried up to _DRAFTS_MAX_RETRIES times on
        transient EmailExternalAPIError before giving up.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_drafts requires authentication.")

        select_fields = (
            "id,subject,body,toRecipients,ccRecipients,bccRecipients,"
            "from,createdDateTime,lastModifiedDateTime"
        )
        url: str | None = (
            f"{GRAPH_BASE_URL}/me/mailFolders/drafts/messages"
            f"?$select={select_fields}"
            f"&$top={_DRAFTS_PAGE_SIZE}"
            f"&$orderby=lastModifiedDateTime%20desc"
        )

        drafts: list[DraftMetadata] = []
        while url and len(drafts) < _DRAFTS_MAX_TOTAL:
            response = self._fetch_drafts_page_with_retries(url)
            for msg in response.get("value") or []:
                try:
                    drafts.append(self._parse_outlook_draft(msg))
                except Exception as exc:
                    logger.warning(
                        "Outlook fetch_drafts: skipping unparseable draft %s: %s",
                        msg.get("id", "?"), exc,
                    )
                if len(drafts) >= _DRAFTS_MAX_TOTAL:
                    break
            url = response.get("@odata.nextLink")
        return drafts

    def _fetch_drafts_page_with_retries(self, url: str) -> dict[str, Any]:
        """Fetch one drafts page, retrying up to _DRAFTS_MAX_RETRIES times.

        Uses Prefer: IdType="ImmutableId" so the provider_draft_id is stable
        across future folder moves (consistent with create_draft).
        """
        last_exc: Exception | None = None
        for attempt in range(_DRAFTS_MAX_RETRIES + 1):
            try:
                return self._graph_request(
                    "GET", url,
                    extra_headers=_PREFER_IMMUTABLE_HEADERS,
                )
            except EmailExternalAPIError as exc:
                last_exc = exc
                if attempt < _DRAFTS_MAX_RETRIES:
                    logger.warning(
                        "Outlook drafts page fetch failed (attempt %d/%d), retrying: %s",
                        attempt + 1, _DRAFTS_MAX_RETRIES + 1, exc,
                    )
                    time.sleep(_DRAFTS_RETRY_DELAY)
                    continue
                raise
        # Unreachable in practice: the loop either returns or raises on the
        # final attempt. Guard defensively to tolerate `python -O` (which
        # strips asserts) and any unforeseen loop-exit path.
        if last_exc is not None:
            raise last_exc
        raise EmailExternalAPIError(
            "Outlook: unexpected exit from drafts retry loop without error."
        )

    def _parse_outlook_draft(self, msg: dict[str, Any]) -> DraftMetadata:
        """Convert a Graph Message JSON into DraftMetadata.

        Extracts address fields from the ``emailAddress.address`` sub-keys
        and parses ``createdDateTime`` / ``lastModifiedDateTime`` via the
        shared :py:meth:`_parse_graph_datetime` helper.

        Body handling depends on ``contentType``:

        - ``HTML`` — Graph returns the content wrapped in a full
          ``<html><head><meta …us-ascii></head><body>…</body></html>``
          document (and NOT byte-for-byte the HTML we sent). It is
          flattened to the ``<body>`` fragment via
          :py:func:`flatten_html_document` so the composer is seeded with
          clean HTML.
        - anything else (legacy ``Text``) — converted to HTML via
          :py:func:`plain_text_to_html` so the persisted body is always
          HTML, matching the migration + the Gmail parse path.
        """
        provider_draft_id = str(msg.get("id") or "")
        subject = msg.get("subject") or ""
        body_section = msg.get("body") or {}
        raw_content = body_section.get("content") or ""
        content_type = (body_section.get("contentType") or "").lower()
        if content_type == "html":
            body_text = flatten_html_document(raw_content)
        else:
            body_text = plain_text_to_html(raw_content)

        def _addrs(key: str) -> list[str]:
            out: list[str] = []
            for recipient in msg.get(key) or []:
                address = ((recipient or {}).get("emailAddress") or {}).get("address")
                if address:
                    out.append(str(address))
            return out

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=_addrs("toRecipients"),
            cc_recipients=_addrs("ccRecipients"),
            bcc_recipients=_addrs("bccRecipients"),
            subject=subject,
            body=body_text,
            created_at=self._parse_graph_datetime(msg.get("createdDateTime")),
            updated_at=self._parse_graph_datetime(msg.get("lastModifiedDateTime")),
        )

    def delete_messages(self, message_ids: list[str]) -> list[str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook delete_messages requires authentication.")
        if not message_ids:
            return []
        # No-op: permanent deletion is not performed at the provider.
        # The service layer marks these as DELETED locally; the provider
        # retains the messages in Trash until its own retention policy
        # purges them.  See core_guide.md § Trash Management Operations.
        return list(message_ids)

    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook restore_from_trash requires authentication.")
        if not items:
            return {}
        results: dict[str, str] = {}
        for msg_id, dest_box in items.items():
            folder = _BOX_TO_FOLDER.get(dest_box, "inbox") if dest_box else "inbox"
            try:
                response = self._graph_request(
                    "POST",
                    f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": folder},
                )
                results[msg_id] = response.get("id", msg_id)
            except EmailExternalAPIError:
                logger.warning("Outlook restore_from_trash: failed to restore message %s", msg_id)
        return results

    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook move_to_trash requires authentication.")
        if not message_ids:
            return {}
        results: dict[str, str] = {}
        for msg_id in message_ids:
            try:
                response = self._graph_request(
                    "POST", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": "deleteditems"},
                )
                results[msg_id] = response.get("id", msg_id)
            except EmailExternalAPIError:
                logger.warning("Outlook move_to_trash: failed to trash message %s", msg_id)
        return results

    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_messages_metadata requires authentication.")
        if not message_ids:
            return []
        folder_id_to_box = self._resolve_special_folder_ids()
        results: list[EmailMetadata] = []
        for msg_id in message_ids:
            url = f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}?$select={_BOOTSTRAP_SELECT_FIELDS}"
            try:
                msg = self._graph_request("GET", url)
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                results.append(self._parse_graph_message(msg, box))
            except EmailExternalAPIError:
                logger.warning("Outlook fetch_messages_metadata: failed to fetch %s", msg_id)
        return results

    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        """Mark messages as read/unread via Microsoft Graph API. Returns IDs successfully updated."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook update_read_status requires authentication.")
        if not message_ids:
            return []
        updated: list[str] = []
        for msg_id in message_ids:
            try:
                self._graph_request("PATCH", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}", body={"isRead": is_read})
                updated.append(msg_id)
            except EmailExternalAPIError as exc:
                # Best-effort: a single bad id (deleted server-side, etc.)
                # must not abort the whole batch. Log so silent failures are
                # observable, mirroring the behaviour of ``move_to_trash`` /
                # ``restore_from_trash``.
                logger.warning(
                    "Outlook update_read_status skipped message %s: %s", msg_id, exc,
                )
        return updated

    # ------------------------------------------------------------------
    # Favourites — followupFlag (Outlook flag)
    # ------------------------------------------------------------------

    def _graph_request_json_with_retries(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        operation: str,
    ) -> dict[str, Any]:
        """Run one Graph request with ``Retry-After``-aware retries.

        Drives :py:meth:`_graph_request_raw` through the same manual
        backoff loop as :py:meth:`fetch_attachment_binary`: transient
        statuses (``_OUTLOOK_RETRYABLE_STATUS_CODES`` plus the synthetic
        ``503`` ``_graph_request_raw`` folds ``URLError`` into) back off
        and retry, honouring ``Retry-After`` when present and falling
        back to ``_OUTLOOK_RETRY_DELAYS_SECONDS`` (3 attempts total).
        Any other status propagates immediately as
        :py:class:`EmailExternalAPIError` without consuming a retry —
        permanent errors (``400``/``401``/``403``/``404``/``409``/``422``)
        never retry. ``_PREFER_IMMUTABLE_HEADERS`` is re-sent on every
        attempt (and, for paginated callers, every page) because Graph
        does not remember the preference across calls.

        Used by the favourite PATCH (``set_favorite``) and each page of
        the favourite listing (``list_favorite_ids``). ``self._sleep`` is
        the injection point that keeps the retry waits instant in tests.
        """
        for attempt, delay in enumerate(_OUTLOOK_RETRY_DELAYS_SECONDS, start=1):
            status, headers, payload = self._graph_request_raw(
                method, url, body=body, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            if 200 <= status < 300:
                if status == 204 or not payload:
                    return {}
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                    raise EmailExternalAPIError(
                        f"Outlook {operation} returned an unparseable response "
                        f"({type(exc).__name__})."
                    ) from exc
                return parsed if isinstance(parsed, dict) else {}
            if status not in _OUTLOOK_RETRYABLE_STATUS_CODES:
                # Permanent failure — fail immediately without retrying.
                raise EmailExternalAPIError(
                    f"Outlook {operation} failed (HTTP {status})."
                )
            # Retryable status: back off and retry, unless this was the
            # last attempt — then fall through to the post-loop raise.
            if attempt < len(_OUTLOOK_RETRY_DELAYS_SECONDS):
                retry_after = _retry_after_seconds(headers)
                self._sleep(retry_after if retry_after is not None else delay)
        raise EmailExternalAPIError(
            f"Outlook {operation} exhausted retries on transient errors."
        )

    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        """Toggle ``flag.flagStatus`` for a single Outlook message.

        Uses the Immutable-ID Prefer header (every message-touching
        Graph call must repeat it, see core_guide.md) and retries
        transient throttling/5xx responses honouring ``Retry-After`` (3
        attempts) via :py:meth:`_graph_request_json_with_retries`. The
        toggle is idempotent at Graph: re-setting the same value is a
        no-op, so a retry after a timeout is safe.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook set_favorite requires authentication.")
        if not provider_message_id:
            return
        flag_status = "flagged" if is_favorite else "notFlagged"
        url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_message_id, safe='')}"
        )
        self._graph_request_json_with_retries(
            "PATCH",
            url,
            body={"flag": {"flagStatus": flag_status}},
            operation="set_favorite",
        )

    def list_favorite_ids(self) -> list[str]:
        """List ids of every flagged Outlook message in the mailbox.

        Filters with ``$filter=flag/flagStatus eq 'flagged'`` and pages
        through ``@odata.nextLink`` until the result set is exhausted.
        ImmutableId is preferred so the returned ids stay stable across
        future moves (and match the ids already in ``email_metadata``).
        Each page (the initial request and every ``nextLink``) retries
        transient errors honouring ``Retry-After`` — a throttled page is
        retried, not fatal to the reconciliation.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook list_favorite_ids requires authentication.")
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            "?$filter=flag/flagStatus%20eq%20'flagged'"
            "&$select=id"
            "&$top=100"
        )
        ids: list[str] = []
        while url:
            response = self._graph_request_json_with_retries(
                "GET", url, operation="list_favorite_ids",
            )
            for msg in response.get("value", []) or []:
                msg_id = str(msg.get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)
            url = response.get("@odata.nextLink")
        return ids

    def fetch_conversation(self, thread_id: str) -> list[ConversationMessage]:
        """Fetch every message of an Outlook conversation (metadata + state, NO body).

        Filters the whole mailbox with ``$filter=conversationId eq
        '<id>'`` — the scope of ``/me/messages`` spans Sent Items, Junk
        and Deleted Items, so the thread is reconstructed across folders.
        The ``conversationId`` is base64 (contains ``+`` / ``/`` / ``=``)
        and is percent-encoded exactly once before being wrapped in the
        single quotes Graph requires. ``$orderby`` is deliberately
        omitted — combining it with ``$filter=conversationId`` returns
        ``400 InefficientFilter`` — so messages are sorted in the client
        by ``receivedDateTime`` (falling back to ``sentDateTime`` for
        Sent items that lack it). Each message is parsed with the same
        :py:meth:`_parse_graph_message` used by sync and enriched with
        ``flag.flagStatus`` for ``is_favorite``.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_conversation requires authentication.")

        folder_id_to_box = self._resolve_special_folder_ids()

        encoded_conversation_id = urllib.parse.quote(thread_id, safe="")
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            f"?$filter=conversationId%20eq%20'{encoded_conversation_id}'"
            f"&$select={_CONVERSATION_SELECT_FIELDS}"
            "&$top=50"
        )

        messages: list[ConversationMessage] = []
        while url:
            response = self._graph_request(
                "GET", url, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            for msg in response.get("value", []) or []:
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                try:
                    meta = self._parse_graph_message(msg, box)
                    received_at = meta.received_at
                    if not msg.get("receivedDateTime") and msg.get("sentDateTime"):
                        received_at = _parse_graph_datetime(msg.get("sentDateTime"))
                    is_favorite = (msg.get("flag") or {}).get("flagStatus") == "flagged"
                    messages.append(
                        ConversationMessage(
                            provider_message_id=meta.provider_message_id,
                            thread_id=meta.thread_id,
                            from_email=meta.from_email,
                            from_name=meta.from_name,
                            subject=meta.subject,
                            received_at=received_at,
                            is_read=meta.is_read,
                            is_favorite=is_favorite,
                            box=meta.box,
                            to_email=meta.to_email,
                            to_name=meta.to_name,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Outlook fetch_conversation: skipping unparseable message %s: %s",
                        msg.get("id", "?"), exc,
                    )
            url = response.get("@odata.nextLink")

        messages.sort(key=lambda m: (m.received_at, m.provider_message_id))
        return messages

    # ------------------------------------------------------------------
    # Spam operations
    # ------------------------------------------------------------------

    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Move messages to spam via Microsoft Graph API. Returns results for successfully moved messages."""
        return self._move_messages(message_ids, "junkemail", "move_to_spam")

    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Restore messages from spam via Microsoft Graph API. Returns results for successfully restored messages."""
        return self._move_messages(message_ids, "inbox", "restore_from_spam")

    def _move_messages(
        self,
        message_ids: list[str],
        destination_id: str,
        operation: str,
    ) -> list[SpamMoveResult]:
        """Move messages to a folder via the Graph /move endpoint. Returns results for successfully moved messages."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError(f"Outlook {operation} requires authentication.")
        if not message_ids:
            return []
        results: list[SpamMoveResult] = []
        for msg_id in message_ids:
            try:
                response = self._graph_request(
                    "POST",
                    f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": destination_id},
                )
                new_id = response.get("id", msg_id)
                results.append(SpamMoveResult(old_id=msg_id, new_id=new_id))
            except EmailExternalAPIError as exc:
                logger.warning(
                    "Outlook spam-move failed for message %s: %s", msg_id, exc
                )
        return results

    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook verify_message_existence requires authentication.")
        if not message_ids:
            return []
        existing: list[str] = []
        for msg_id in message_ids:
            url = f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}?$select=id"
            try:
                self._graph_request("GET", url)
                existing.append(msg_id)
            except EmailExternalAPIError as exc:
                logger.warning(
                    "Outlook verify_message_existence failed for message %s: %s",
                    msg_id, exc,
                )
        return existing

    def fetch_email_content(self, provider_message_id: str) -> EmailContent:
        """Fetch the full body content for a single Outlook message.

        Inlines referenced ``cid:…`` images as ``data:`` URLs (D-13
        strict: only CIDs actually referenced by the HTML body are
        inlined; the rest surface via :py:meth:`list_message_attachments`
        as downloadable attachments).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_email_content requires authentication.")
        try:
            escaped_id = urllib.parse.quote(provider_message_id, safe="")
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select=body,hasAttachments",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            body = response.get("body", {})
            content_type = body.get("contentType", "").lower()
            content = body.get("content")
            if content_type != "html":
                return EmailContent(html_body=None, text_body=content)
            if content and response.get("hasAttachments"):
                cid_map, _ = self._classify_attachments(escaped_id, content)
                if cid_map:
                    content = inline_cid_images(content, cid_map)
            return EmailContent(html_body=content, text_body=None)
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected fetch_email_content error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_reply_context(self, provider_message_id: str) -> ReplyContext:
        """Single ``GET /me/messages/{id}`` covering every Reply / Forward
        input field, with provider parsing isolated in
        :py:meth:`_reply_context_from_graph_message`.

        ``$select`` enumerates only the fields the composer needs:
        ``from``, ``toRecipients``, ``ccRecipients``, ``replyTo``,
        ``subject``, ``body``, ``internetMessageId``,
        ``internetMessageHeaders``, ``receivedDateTime``,
        ``conversationId``, ``parentFolderId``, ``hasAttachments``.

        ``parentFolderId`` is mapped to a box via the existing
        :py:meth:`_resolve_special_folder_ids` helper so the
        self-reply override ("I replied to my own Sent message") can
        be derived in the service layer.

        Errors from Graph are wrapped as
        :py:class:`EmailReplyContextFetchError` so the service maps
        them to the reply-specific ``EmailReplyContextError`` (HTTP
        502).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_reply_context requires authentication."
            )
        escaped_id = urllib.parse.quote(provider_message_id, safe="")
        select_fields = (
            "from,sender,toRecipients,ccRecipients,replyTo,subject,body,"
            "internetMessageId,internetMessageHeaders,receivedDateTime,"
            "conversationId,parentFolderId,hasAttachments"
        )
        try:
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select={select_fields}",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError as exc:
            raise EmailReplyContextFetchError(
                f"Failed to fetch reply context for Outlook message {provider_message_id}: {exc.message}",
                detail={"reason": "provider_fetch_failed"},
            ) from exc
        except CoreError:
            # Any other CoreError (e.g. a reply-context error from a future
            # helper inside the try) passes through untouched instead of being
            # re-wrapped by the generic handler below (core/CLAUDE.md §5).
            raise
        except Exception as exc:
            raise EmailReplyContextFetchError(
                f"Outlook unexpected fetch_reply_context error ({type(exc).__name__}): {exc}",
                detail={"reason": "provider_fetch_failed"},
            ) from exc

        # Best-effort folder resolution. A provider failure here just
        # collapses to ``ALL_MAIL`` — the self-reply override is a UX
        # nicety, not a correctness invariant. Narrowed to the provider
        # error (core/CLAUDE.md §5): an unexpected exception must not be
        # silently swallowed into the wrong box.
        try:
            folder_id_to_box = self._resolve_special_folder_ids()
        except EmailExternalAPIError as exc:  # pragma: no cover — defensive
            logger.warning(
                "Outlook reply context: folder resolution failed (%s): %s",
                type(exc).__name__, exc,
            )
            folder_id_to_box = {}
        parent_folder_id = response.get("parentFolderId") or ""
        box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")

        return self._reply_context_from_graph_message(
            provider_message_id=provider_message_id, message=response, box=box,
        )

    @staticmethod
    def _emails_from_recipients(recipients: Any) -> list[str]:
        """Decompose a Graph ``recipients`` list into plain email strings.

        Graph returns ``[{"emailAddress": {"address": "...", "name": "..."}}, ...]``.
        We surface a flat list of addresses for symmetry with
        :py:meth:`GmailClient._split_address_header` and to keep the
        :py:class:`ReplyContext` shape provider-agnostic.
        """
        out: list[str] = []
        if not isinstance(recipients, list):
            return out
        for entry in recipients:
            if not isinstance(entry, dict):
                continue
            addr_obj = entry.get("emailAddress") or {}
            if not isinstance(addr_obj, dict):
                continue
            addr = (addr_obj.get("address") or "").strip()
            if addr and "@" in addr:
                out.append(addr)
        return out

    @staticmethod
    def _extract_email_address(recipient: Any) -> tuple[str, str]:
        """Return ``(email, name)`` from a Graph ``recipient`` object.

        Graph wraps single-recipient fields (``from``, ``sender``) in
        the shape ``{"emailAddress": {"address": "...", "name": "..."}}``.
        Returns ``("", "")`` when the structure is missing or invalid.
        """
        if not isinstance(recipient, dict):
            return "", ""
        addr_obj = recipient.get("emailAddress") or {}
        if not isinstance(addr_obj, dict):
            return "", ""
        addr = (addr_obj.get("address") or "").strip()
        name = (addr_obj.get("name") or "").strip()
        return addr, name

    @staticmethod
    def _first_recipient_from_graph_recipients(
        recipients: Any,
    ) -> tuple[str, str]:
        """Extract ``(name, email)`` of the first valid Graph recipient.

        Symmetric with :py:meth:`_emails_from_recipients` (same Graph
        shape parsing) but preserves the display name for the leading
        entry — used to populate ``EmailMetadata.to_email`` / ``to_name``
        during sync. Returns ``("", "")`` when no recipient carries a
        valid ``@`` address.
        """
        if not isinstance(recipients, list):
            return "", ""
        for entry in recipients:
            if not isinstance(entry, dict):
                continue
            addr_obj = entry.get("emailAddress") or {}
            if not isinstance(addr_obj, dict):
                continue
            addr = (addr_obj.get("address") or "").strip()
            if not addr or "@" not in addr:
                continue
            name = (addr_obj.get("name") or "").strip()
            return name, addr
        return "", ""

    @staticmethod
    def _references_from_internet_headers(headers: Any) -> str:
        """Extract the raw ``References`` value from ``internetMessageHeaders``.

        The Graph property is a list of ``{name, value}`` records. We
        match ``References`` case-insensitively (RFC 5322 header names
        are case-insensitive). A missing header collapses to ``""``.
        """
        if not isinstance(headers, list):
            return ""
        for entry in headers:
            if not isinstance(entry, dict):
                continue
            if (entry.get("name") or "").lower() == "references":
                return str(entry.get("value") or "")
        return ""

    def _reply_context_from_graph_message(
        self,
        *,
        provider_message_id: str,
        message: dict[str, Any],
        box: str,
    ) -> ReplyContext:
        """Pure-shape conversion from Graph ``message`` JSON to ``ReplyContext``.

        Split from :py:meth:`fetch_reply_context` so the HTTP layer
        and the parsing layer can be tested independently (fakes that
        feed pre-shaped dicts don't need to mock the Graph stack).
        """
        from_email, from_name = self._extract_email_address(message.get("from"))
        if not from_email:
            # Graph occasionally omits ``from`` (delegated mailboxes,
            # certain on-behalf-of sends). ``sender`` is documented as
            # the actual sending mailbox and is always populated for
            # received messages — fall back to it so the composer can
            # still seed ``to_recipients`` for Reply.
            from_email, sender_name = self._extract_email_address(message.get("sender"))
            if not from_name:
                from_name = sender_name

        body_section = message.get("body") or {}
        content_type = (body_section.get("contentType") or "").lower()
        content = body_section.get("content")
        if content_type == "html":
            html_body = content
            text_body = None
        else:
            html_body = None
            text_body = content

        message_id_raw = (message.get("internetMessageId") or "").strip().strip("<>")
        references = self._references_from_internet_headers(
            message.get("internetMessageHeaders"),
        )
        received_at = self._parse_graph_datetime(message.get("receivedDateTime"))

        return ReplyContext(
            provider_message_id=provider_message_id,
            thread_id=str(message.get("conversationId") or ""),
            from_email=from_email,
            from_name=from_name,
            reply_to=self._emails_from_recipients(message.get("replyTo")),
            to_recipients=self._emails_from_recipients(message.get("toRecipients")),
            cc_recipients=self._emails_from_recipients(message.get("ccRecipients")),
            subject=str(message.get("subject") or ""),
            body_html=html_body,
            body_text=text_body,
            received_at=received_at,
            message_id=message_id_raw,
            references=references,
            box=box,
        )

    def list_message_attachments(
        self,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        """List downloadable attachments + inline cid_map for an Outlook message.

        Single ``GET /me/messages/{id}`` (with body) plus ``GET .../attachments``
        — the body call is needed only to compute referenced CIDs (D-13).
        We could in theory share the call with ``fetch_email_content`` but
        in the cache-aside flow the service invokes both; the duplication
        is bounded (two cheap GETs) and the call sites stay simple.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook list_message_attachments requires authentication."
            )
        escaped_id = urllib.parse.quote(provider_message_id, safe="")
        try:
            body_response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select=body",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        body_section = body_response.get("body") or {}
        html_body = (
            body_section.get("content")
            if (body_section.get("contentType") or "").lower() == "html"
            else None
        )
        cid_map, downloadable = self._classify_attachments(
            escaped_id, html_body, provider_message_id=provider_message_id,
        )
        return downloadable, cid_map

    def _classify_attachments(
        self,
        escaped_message_id: str,
        html_body: str | None,
        *,
        provider_message_id: str | None = None,
    ) -> tuple[dict[str, str], list[AttachmentMetadata]]:
        """Walk Outlook's attachment list applying the D-13 strict rule.

        Returns ``(cid_map, downloadable)`` — the same shape as Gmail's
        ``_classify_attachments`` so the service code is symmetrical:
        - ``cid_map`` holds inline images whose ``contentId`` IS
          referenced by ``html_body``.
        - ``downloadable`` lists every other attachment the user should
          see (``isInline=false`` or inline-marked-but-unreferenced).

        The Outlook ``attachment.id`` is used as ``provider_attachment_id``
        because the call sends ``Prefer: IdType="ImmutableId"`` per
        request (the cache key per D-06b-clave is stable while the
        message stays in the same mailbox).
        """
        try:
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_message_id}/attachments"
                "?$select=id,name,contentType,contentBytes,size,contentId,isInline",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError as exc:
            # Intentional best-effort: an attachments listing failure must
            # not abort the whole content fetch. Returning empty lists
            # produces a body with no inline images and no attachments
            # listed — the user sees the message text, and a retry of the
            # content endpoint will pick the lists up once Graph recovers.
            logger.warning("Outlook attachments fetch failed: %s", exc)
            return {}, []
        except Exception as exc:
            # Same best-effort path for any unexpected failure (parse error,
            # Graph schema drift, etc.). Logged with the type so silent
            # regressions remain observable.
            logger.warning(
                "Outlook attachments unexpected error (%s): %s",
                type(exc).__name__, exc,
            )
            return {}, []

        referenced_cids = find_referenced_cids(html_body)
        cid_map: dict[str, str] = {}
        downloadable: list[AttachmentMetadata] = []
        position_counter = 0

        for attachment in response.get("value") or []:
            odata_type = (attachment.get("@odata.type") or "").lower()
            if odata_type and "fileattachment" not in odata_type:
                # Item / reference attachments are surfaced as
                # downloadables for completeness; binary fetch for
                # referenceAttachment is out-of-scope MVP (returns 405
                # at the provider). The user sees them in the list with
                # the provider's declared mime/size.
                pass
            cid_raw = (attachment.get("contentId") or "").strip().strip("<>").strip() or None
            name = attachment.get("name") or "attachment"
            # B-MIME + B-OUTLOOK-LOWER: resolve the type through the shared
            # helper. A generic declared type (``application/octet-stream``)
            # is overridden by the type inferred from the filename extension;
            # a specific declared type is kept verbatim, dropping the previous
            # forced ``.lower()`` that made Outlook diverge from Gmail.
            content_type = resolve_attachment_mime_type(name, attachment.get("contentType"))
            content_bytes_b64 = attachment.get("contentBytes")
            is_inline = bool(attachment.get("isInline"))
            attachment_id = str(attachment.get("id") or "")
            size = int(attachment.get("size") or 0)

            if (
                is_inline
                and cid_raw
                and cid_raw in referenced_cids
                and content_bytes_b64
                and content_type.lower().startswith("image/")
            ):
                cid_map[cid_raw] = f"data:{content_type};base64,{content_bytes_b64}"
                continue

            downloadable.append(
                AttachmentMetadata(
                    provider_message_id=provider_message_id or "",
                    part_id=None,
                    provider_attachment_id=attachment_id or None,
                    filename=str(name),
                    mime_type=content_type,
                    size=size,
                    content_id=cid_raw,
                    is_inline=is_inline,
                    position=position_counter,
                )
            )
            position_counter += 1

        return cid_map, downloadable

    def fetch_attachment_binary(
        self,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        """Download a single Outlook attachment binary on demand.

        Uses ``GET /me/messages/{id}/attachments/{att}/$value`` to skip
        the base64 round-trip (recommended in
        ``adjuntos-outlook.md`` § 4.5). Retries transient errors up to 3
        attempts with 1s/2s/4s backoff, honouring ``Retry-After`` when
        Graph emits it. Maps 404/410 to :py:class:`EmailAttachmentNotFound`
        and 403 to :py:class:`EmailAttachmentDownloadFailed` with reason
        ``forbidden`` (D-17).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_attachment_binary requires authentication."
            )
        if not attachment.provider_attachment_id:
            raise EmailAttachmentNotFound(
                "Outlook attachment is missing provider_attachment_id; cannot resolve $value."
            )
        url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_message_id, safe='')}/attachments/"
            f"{urllib.parse.quote(attachment.provider_attachment_id, safe='')}/$value"
        )
        for attempt, delay in enumerate(_OUTLOOK_RETRY_DELAYS_SECONDS, start=1):
            status, headers, payload = self._graph_request_raw(
                "GET", url, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            if 200 <= status < 300:
                return AttachmentBinary(
                    mime_type=attachment.mime_type or "application/octet-stream",
                    filename=attachment.filename,
                    data=payload or b"",
                    size=len(payload or b""),
                )
            if status in (404, 410):
                raise EmailAttachmentNotFound(
                    f"Outlook attachment {attachment.provider_attachment_id} returned {status}.",
                    {"reason": "missing"},
                )
            if status == 403:
                raise EmailAttachmentDownloadFailed(
                    f"Outlook attachment fetch forbidden (HTTP {status}).",
                    {"reason": "forbidden"},
                )
            if status not in (429, 500, 502, 503, 504):
                # Non-retryable status — fail immediately.
                raise EmailAttachmentDownloadFailed(
                    f"Outlook attachment fetch failed (HTTP {status}).",
                    {"reason": "unavailable"},
                )
            # Retryable status: back off and retry, unless this was the last
            # attempt — then fall through to the post-loop raise (now
            # reachable, instead of the previous dead fallback).
            if attempt < len(_OUTLOOK_RETRY_DELAYS_SECONDS):
                retry_after = _retry_after_seconds(headers)
                time.sleep(retry_after if retry_after is not None else delay)
        raise EmailAttachmentDownloadFailed(
            "Outlook attachment fetch exhausted retries without a final response.",
            {"reason": "unavailable"},
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

    def get_account_label(self) -> str:
        """
        Return the label that identifies this Outlook account inside the app.
        """
        return self._account_label

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_sender_profile(self) -> tuple[str, str]:
        """Best-effort fetch of the authenticated user's email and display name (cached).

        Tries three strategies in order:
        1. GET /me (requires User.Read scope)
        2. Decode JWT access token claims
        3. Read the ``from`` field of a recently sent message (Mail.ReadWrite)
        """
        if self._sender_email is not None:
            return self._sender_email, self._sender_name or ""

        email, name = "", ""

        # Try 1: Graph API /me endpoint (needs User.Read scope)
        try:
            response = self._graph_request(
                "GET", f"{GRAPH_BASE_URL}/me?$select=displayName,mail,userPrincipalName",
            )
            email = response.get("mail") or response.get("userPrincipalName") or ""
            name = response.get("displayName") or ""
        except Exception as exc:
            logger.debug("Outlook /me profile fetch failed: %s", exc)

        # Try 2: decode JWT access token claims
        if not email:
            email, name = self._parse_token_claims()

        # Try 3: read from a recently sent message (needs Mail.ReadWrite)
        if not email:
            try:
                response = self._graph_request(
                    "GET",
                    f"{GRAPH_BASE_URL}/me/mailFolders/sentitems/messages?$top=1&$select=from",
                )
                messages = response.get("value", [])
                if messages:
                    from_obj = messages[0].get("from") or {}
                    email_addr = from_obj.get("emailAddress") or {}
                    email = email_addr.get("address") or ""
                    name = email_addr.get("name") or ""
            except Exception as exc:
                logger.debug("Outlook sent message profile fetch failed: %s", exc)

        self._sender_email = email
        self._sender_name = name
        return self._sender_email, self._sender_name

    def _parse_token_claims(self) -> tuple[str, str]:
        """Extract (email, display_name) from the JWT access token claims."""
        if not self._access_token:
            return "", ""
        try:
            parts = self._access_token.split(".")
            if len(parts) != 3:
                return "", ""
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            claims = json.loads(base64.urlsafe_b64decode(payload))
            email = (
                claims.get("preferred_username")
                or claims.get("email")
                or claims.get("unique_name")
                or claims.get("upn")
                or ""
            )
            name = claims.get("name") or ""
            return email, name
        except Exception as exc:
            logger.debug("Outlook failed to parse JWT claims: %s", exc)
            return "", ""

    def _resolve_scopes(
        self,
        credentials_payload: dict[str, Any],
        token_payload: dict[str, Any] | None = None,
    ) -> list[str]:
        """Resolve scopes from credentials or token payload, with a default fallback."""
        scopes_raw = credentials_payload.get("scopes")
        if scopes_raw is None and token_payload is not None:
            scopes_raw = token_payload.get("scopes")
        if isinstance(scopes_raw, str):
            return [s for s in scopes_raw.replace(",", " ").split() if s]
        if isinstance(scopes_raw, (list, tuple, set)):
            return [str(s).strip() for s in scopes_raw if str(s).strip()]
        return list(OUTLOOK_SCOPES)

    @staticmethod
    def _token_url(tenant: str) -> str:
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    @staticmethod
    def _compute_expiry(expires_in_raw: Any) -> str | None:
        if expires_in_raw is None:
            return None
        try:
            expires_in = int(expires_in_raw)
            if expires_in < 0:
                expires_in = 0
            return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        except (TypeError, ValueError):
            return None

    def _token_request(self, token_url: str, payload: dict[str, str]) -> dict[str, Any]:
        """POST to the Microsoft identity token endpoint and return the parsed response."""
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        req = urllib.request.Request(token_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = error_body
            try:
                error_json = json.loads(error_body) if error_body else {}
                if isinstance(error_json, dict):
                    err = error_json.get("error")
                    desc = error_json.get("error_description")
                    if err or desc:
                        detail = f"{err or 'error'}: {desc or 'unknown error'}"
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            raise EmailExternalAPIError(f"Outlook failed to call token endpoint: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmailExternalAPIError(f"Outlook failed to reach token endpoint: {exc.reason}") from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook failed during token request ({type(exc).__name__}): {exc}"
            ) from exc

        try:
            token_response = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise EmailExternalAPIError("Outlook failed to parse token endpoint response: invalid JSON.") from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook failed during token response parsing ({type(exc).__name__}): {exc}"
            ) from exc

        if not isinstance(token_response, dict):
            raise EmailExternalAPIError("Outlook failed at token endpoint: invalid response payload.")

        if token_response.get("error"):
            err = str(token_response.get("error") or "error")
            desc = str(token_response.get("error_description") or "unknown error")
            raise EmailExternalAPIError(f"Outlook failed at token endpoint: {err}: {desc}")

        return token_response

    def _graph_request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated JSON request to Microsoft Graph API."""
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 204:
                    return {}
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = error_body
            try:
                error_json = json.loads(error_body) if error_body else {}
                if isinstance(error_json, dict):
                    err_code = error_json.get("error", {})
                    if isinstance(err_code, dict):
                        detail = f"{err_code.get('code', 'error')}: {err_code.get('message', '')}"
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            raise EmailExternalAPIError(f"Outlook failed Graph API call: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmailExternalAPIError(f"Outlook failed to reach Graph API: {exc.reason}") from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook failed Graph API request ({type(exc).__name__}): {exc}"
            ) from exc

    def _graph_request_raw(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Authenticated Graph request returning raw status + headers + bytes.

        Used by ``fetch_attachment_binary`` to read ``/$value`` (binary
        body, not JSON) and by the favourite retry loops (``set_favorite``
        PATCH / ``list_favorite_ids`` GET) which need the response headers
        to honour ``Retry-After``. Errors do not raise from this method —
        the caller drives retry / classification per status.

        When ``body`` is provided it is JSON-serialised and sent with a
        ``Content-Type: application/json`` header (the favourite PATCH);
        ``body=None`` keeps the bodyless GET behaviour unchanged.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
            # Convert connection-level failures (DNS, timeout, refused) to a
            # synthetic 503 so the caller's retry loop treats them the same
            # as a transient provider 5xx (D-16). Without this, the retry
            # loop in ``fetch_attachment_binary`` only matches by status
            # code and a network failure would skip retries entirely.
            logger.warning(
                "Outlook Graph raw request network error (treated as 503): %s",
                exc.reason,
            )
            return (503, {}, b"")

