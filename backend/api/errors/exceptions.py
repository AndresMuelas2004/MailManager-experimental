"""
Custom API exceptions used across routers and services.

Raising these exceptions keeps error handling consistent and avoids
leaking low-level details directly to HTTP responses.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """
    Base class for API errors with a stable error code and optional details.
    """

    code = "api_error"

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class MailboxNotFound(ApiError):
    code = "mailbox_not_found"


class AccountNotFound(ApiError):
    code = "account_not_found"


class EmailNotFound(ApiError):
    code = "email_not_found"


class AccountConnectAuthError(ApiError):
    code = "account_connect_auth_error"


class AccountNotConnected(ApiError):
    code = "account_not_connected"


class CredentialFileError(ApiError):
    code = "credential_file_error"


class DatabaseConnectionError(ApiError):
    code = "database_connection_error"


class DatabaseMigrationError(ApiError):
    code = "database_migration_error"


class DatabaseQueryError(ApiError):
    code = "database_query_error"


class EmailFetchError(ApiError):
    code = "email_fetch_error"


class EmailSendError(ApiError):
    code = "email_send_error"


class ExternalAPIError(ApiError):
    code = "external_api_error"


class AccountMisconfigured(ApiError):
    code = "account_misconfigured"


class AppCredentialsInvalid(ApiError):
    code = "app_credentials_invalid"


class AppCredentialsMissing(ApiError):
    code = "app_credentials_missing"


class RecipientsMissing(ApiError):
    code = "recipients_missing"


class EnvVarError(ApiError):
    code = "env_var_error"


class Unauthorized(ApiError):
    code = "unauthorized"


class Forbidden(ApiError):
    code = "forbidden"


class TokenDecryptionError(ApiError):
    code = "token_decryption_error"


class TokenEncryptionError(ApiError):
    code = "token_encryption_error"


class TokenIntegrityError(ApiError):
    code = "token_integrity_error"


class UserNotFound(ApiError):
    code = "user_not_found"


class EmailNotInTrash(ApiError):
    code = "email_not_in_trash"


class TrashOperationError(ApiError):
    code = "trash_operation_error"


class MoveToTrashError(ApiError):
    code = "move_to_trash_error"


class ReadStatusUpdateError(ApiError):
    code = "read_status_update_error"


class SpamMoveError(ApiError):
    code = "spam_move_error"


class SpamRestoreError(ApiError):
    code = "spam_restore_error"


class ArchiveMoveError(ApiError):
    code = "archive_move_error"


class ArchiveRestoreError(ApiError):
    code = "archive_restore_error"


class EmailContentFetchError(ApiError):
    code = "email_content_fetch_error"


class EmailReplyContextError(ApiError):
    """Provider-side or coherence failure while preparing the data the
    composer needs to open a Reply / Reply All / Forward.

    Mapped to HTTP 502 — same shape as the rest of the provider-fetch
    failures (``EmailFetchError``, ``EmailContentFetchError``, …). The
    user-facing message lives in the frontend translation; the API
    just emits the code.
    """
    code = "email_reply_context_error"


class ConversationFetchError(ApiError):
    """Provider-side or unexpected failure while fetching a conversation
    thread (membership + per-message state) for the conversation viewer.

    Mapped to HTTP 502 — same family as ``EmailContentFetchError`` /
    ``EmailReplyContextError``. Raised directly by the service only for
    its own unexpected branches; a genuine provider ``EmailExternalAPIError``
    surfaces as ``ExternalAPIError`` via ``translate_core_error``.
    """
    code = "conversation_fetch_error"


class EmailListError(ApiError):
    code = "email_list_error"


class UnreadCountError(ApiError):
    """Unexpected (non-DB) failure while counting unread emails for the
    mailbox unread badge. Mapped to HTTP 500 — same family as
    ``EmailListError`` (a counting failure has no retry story)."""
    code = "unread_count_error"


class DraftCreationError(ApiError):
    code = "draft_creation_error"


class DraftListError(ApiError):
    code = "draft_list_error"


class DraftSyncError(ApiError):
    code = "draft_sync_error"


class DraftUpdateError(ApiError):
    code = "draft_update_error"


class DraftNotFound(ApiError):
    code = "draft_not_found"


class DraftDeleteError(ApiError):
    code = "draft_delete_error"


class DraftSendError(ApiError):
    code = "draft_send_error"


class MailboxLookupError(ApiError):
    """Unexpected internal failure while resolving a mailbox by id."""

    code = "mailbox_lookup_error"


class AppCredentialsLoadError(ApiError):
    """Unexpected internal failure while loading provider app credentials."""

    code = "app_credentials_load_error"


class AccountTokensLoadError(ApiError):
    """Unexpected internal failure while loading account access/refresh tokens."""

    code = "account_tokens_load_error"


class MailboxOperationError(ApiError):
    code = "mailbox_operation_error"


class AccountOperationError(ApiError):
    code = "account_operation_error"


class AccountLimitExceeded(ApiError):
    """The user already owns the maximum number of connected accounts allowed."""

    code = "account_limit_exceeded"


class SessionOperationError(ApiError):
    code = "session_operation_error"


class UserOperationError(ApiError):
    code = "user_operation_error"


# ---------------------------------------------------------------------------
# Attachment errors (D-01 to D-30, see decisionesTomadasAdjuntosBackend.md).
# Each subclass corresponds to one user-visible failure mode of the
# attachments feature; granularity is intentionally fine so the frontend
# can render a precise message and the API status_map can route to the
# right HTTP code.
# ---------------------------------------------------------------------------


class AttachmentNotFound(ApiError):
    """The attachment row does not exist or does not belong to the user."""
    code = "attachment_not_found"


class AttachmentUnavailable(ApiError):
    """The provider returned 404/410 for a previously-listed attachment (D-17)."""
    code = "attachment_unavailable"


class AttachmentCopySourceUnavailable(ApiError):
    """The Forward copy endpoint could not retrieve a source attachment
    (the provider returned 404/410 or the row was previously stamped
    ``unavailable_at``).

    Mapped to HTTP 404 — semantically aligned with the existing
    ``AttachmentUnavailable`` mapping so the frontend handles both with
    the same "no longer available" branch.
    """
    code = "attachment_copy_source_unavailable"


class AttachmentTooLarge(ApiError):
    """A single attachment exceeds the 25 MB per-file cap (D-01)."""
    code = "attachment_too_large"


class AttachmentBlockedExtension(ApiError):
    """The attachment extension is in the canonical blocklist (D-04a)."""
    code = "attachment_blocked_extension"


class AttachmentLimitExceeded(ApiError):
    """The draft already holds the maximum 25 attachments (D-03)."""
    code = "attachment_limit_exceeded"


class AttachmentMessageSizeExceeded(ApiError):
    """The cumulative message size would exceed the 25 MB cap (D-02)."""
    code = "attachment_message_size_exceeded"


class AttachmentProviderForbidden(ApiError):
    """Provider returned 403 for an attachment fetch (D-17).

    Mapped to HTTP 502 — the user's session is fine, but the provider
    does not allow this scope/permission. Likely a token degradation
    that warrants investigation; the frontend renders a generic
    "could not access the attachment" message.
    """
    code = "provider_forbidden"


class AttachmentProviderUnavailable(ApiError):
    """Provider failed with persistent 5xx after retries (D-17).

    Mapped to HTTP 503 — the failure is presumed transient. The
    frontend renders "try again in a few minutes" and the user can
    retry; the metadata row stays valid (no ``unavailable_at`` stamp).
    """
    code = "provider_unavailable"


class AttachmentSendFailed(ApiError):
    """One or more draft attachments failed to upload before send (D-27)."""
    code = "attachment_send_failed"


class RequestTooLarge(ApiError):
    """The multipart upload exceeded the 30 MB global cap (§5.4)."""
    code = "request_too_large"


class DraftAttachmentNotFound(ApiError):
    """The targeted draft attachment row does not exist."""
    code = "draft_attachment_not_found"


class AttachmentLookupError(ApiError):
    """Unexpected internal failure while resolving the draft / account row that
    must precede an ``add_draft_attachment`` insert."""

    code = "attachment_lookup_error"


class AttachmentInsertError(ApiError):
    """Unexpected internal failure while inserting a row into ``draft_attachments``."""

    code = "attachment_insert_error"


class AttachmentListingError(ApiError):
    """Unexpected internal failure while listing attachments stored for a draft
    (used during the size-cap pre-check before insert)."""

    code = "attachment_listing_error"


class PurgeDisabled(ApiError):
    """The admin purge endpoint is disabled (no env-var token, D-30)."""
    code = "purge_disabled"


class InvalidAdminToken(ApiError):
    """The X-Admin-Token header does not match the configured token (D-30)."""
    code = "invalid_admin_token"


class DevLoginDisabled(ApiError):
    """The dev-login endpoint is disabled (``DEV_LOGIN_ENABLED`` is not truthy).

    Mirrors the ``PurgeDisabled`` semantics: 503 means "this deploy was
    not configured for this operation", which must stay distinct from
    "your credentials are wrong" (401). Collapsing both would mask a
    deploy-config error behind a credential-style error.
    """
    code = "dev_login_disabled"


class DevLoginNotLocalhost(ApiError):
    """The dev-login endpoint was called from a host outside
    ``DEV_LOGIN_TRUSTED_HOSTS`` (default ``127.0.0.1``, ``::1``,
    ``localhost``).

    Defence-in-depth: even if ``DEV_LOGIN_ENABLED`` leaks to a
    non-local environment, the host check keeps the backdoor
    unreachable from the outside.
    """
    code = "dev_login_not_localhost"


# ---------------------------------------------------------------------------
# Favourites (Gmail STARRED / Outlook flag).
# ---------------------------------------------------------------------------


class FavoriteUpdateError(ApiError):
    """Provider-side failure while toggling the favourite flag."""
    code = "favorite_update_error"


class FavoriteSyncError(ApiError):
    """Provider-side failure during the manual /favorites/sync reconciliation."""
    code = "favorite_sync_error"


# ---------------------------------------------------------------------------
# Virtual mailboxes (filtered views over email_metadata).
# ---------------------------------------------------------------------------


class VirtualMailboxNotFound(ApiError):
    """Virtual mailbox does not exist or does not belong to the user."""
    code = "virtual_mailbox_not_found"


class VirtualMailboxOperationError(ApiError):
    """Unexpected DB-side failure during a virtual mailbox CRUD operation."""
    code = "virtual_mailbox_operation_error"


class VirtualMailboxListError(ApiError):
    """Unexpected failure while resolving the filtered email listing of a virtual mailbox."""
    code = "virtual_mailbox_list_error"


# ---------------------------------------------------------------------------
# Contacts (recipient autocomplete — aggregated from synced email_metadata).
# ---------------------------------------------------------------------------


class RecipientSuggestionsError(ApiError):
    """Unexpected failure while building recipient autocomplete suggestions."""
    code = "recipient_suggestions_error"


# ---------------------------------------------------------------------------
# Folders + internal rules (carpetas-y-reglas).
# ---------------------------------------------------------------------------


class FolderNotFound(ApiError):
    """Folder does not exist or does not belong to the user (404, D-22: a
    foreign folder is indistinguishable from a missing one)."""
    code = "folder_not_found"


class FolderNameConflict(ApiError):
    """A folder with the same name (case-insensitive) already exists for the
    user (409 — the unique index is the defence, this is the surface)."""
    code = "folder_name_conflict"


class FolderOperationError(ApiError):
    """Unexpected internal (non-provider) failure during a folder operation.

    Provider label/category failures do NOT reach this — they translate via
    ``translate_core_error`` to ``ExternalAPIError`` (502). This is the 500
    fallback for a DB/logic stumble on our side."""
    code = "folder_operation_error"


class RuleNotFound(ApiError):
    """Rule does not exist or does not belong to the user (404)."""
    code = "rule_not_found"


class RuleValidationError(ApiError):
    """The rule would end up with no condition after applying a PATCH (422).

    Distinct from the schema-level 422 (``{"detail": [...]}`` from FastAPI):
    this is a SERVICE re-validation of the MERGED rule, emitted with the
    project ``{"error": {...}}`` envelope."""
    code = "rule_validation_error"


class RuleOperationError(ApiError):
    """Unexpected internal (non-provider) failure during a rule operation
    (CRUD or apply enqueue). Mapped to HTTP 500."""
    code = "rule_operation_error"


class RuleApplyStatusError(ApiError):
    """Unexpected (non-DB) failure while reading a rule's apply status. Mapped
    to HTTP 500 — same family as ``BackfillStatusError`` (a read-only status
    failure has no retry story; a ``DatabaseError`` still translates to 503)."""
    code = "rule_apply_status_error"


# ---------------------------------------------------------------------------
# Background initial mass backfill.
# ---------------------------------------------------------------------------


class BackfillStatusError(ApiError):
    """Unexpected (non-DB) failure while loading the backfill status of a
    mailbox. Mapped to HTTP 500 — same family as ``EmailListError`` (a
    read-only status failure has no retry story; a ``DatabaseError`` from the
    same store call still translates to 503)."""
    code = "backfill_status_error"


class BackfillJobError(ApiError):
    """Persistence fallback for the background backfill machinery.

    Two producers, both OUTSIDE the request/response cycle so it never reaches
    a client: (1) the worker, which reuses the service persistence helpers
    (``persist_email_metadata_batch``, ``update_sync_cursor``, …) and swallows
    this (logs with ``exc_info`` + marks the job failed); (2)
    ``enqueue_backfill_on_connect``, whose caller (``complete_account_connect``)
    swallows it best-effort. Registered at 500 for completeness."""
    code = "backfill_job_error"


# ---------------------------------------------------------------------------
# Health / readiness.
# ---------------------------------------------------------------------------


class ServiceUnavailableError(ApiError):
    """The service is not ready to serve traffic — e.g. the database readiness
    probe behind ``/health`` failed. Mapped to HTTP 503 so an orchestrator or
    uptime monitor stops routing traffic to this instance."""
    code = "service_unavailable"


# ---------------------------------------------------------------------------
# Rate limiting (#11e — per-client request throttling).
# ---------------------------------------------------------------------------


class TooManyRequests(ApiError):
    """Client exceeded the allowed request rate for a throttled bucket.

    Carries ``detail = {"scope": <bucket>, "retry_after": <int seconds>}``.
    The exception handler turns ``retry_after`` into a ``Retry-After`` header.
    Mapped to HTTP 429.
    """
    code = "rate_limit_exceeded"


# ---------------------------------------------------------------------------
# Remote-email-image proxy (GET /image-proxy + admin purge).
# Consumed from an <img> tag, so the browser only distinguishes 2xx from
# non-2xx; the granular codes below matter for logs / monitoring / tests.
# ---------------------------------------------------------------------------


class ImageProxyForbidden(ApiError):
    """The image proxy request carried an invalid or missing HMAC signature.

    Mapped to HTTP 403 — the sentinel URL was not minted by our sanitiser
    (or was tampered with), so the proxy refuses to fetch it."""
    code = "image_proxy_forbidden"


class ImageProxyBlockedTarget(ApiError):
    """The remote image target was blocked by the anti-SSRF policy (private /
    loopback / link-local / metadata / CGNAT address, bad scheme, or an unsafe
    redirect hop). Mapped to HTTP 403."""
    code = "image_proxy_blocked_target"


class ImageProxyUpstreamError(ApiError):
    """The image proxy could not serve the remote image: an upstream fetch
    failure, a non-image / oversized response, or a cache read error. Mapped
    to HTTP 502 (the failure is on the upstream/proxy side, not the client)."""
    code = "image_proxy_upstream_error"
