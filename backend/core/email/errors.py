from __future__ import annotations

from typing import Any


class CoreError(Exception):
    code = "core_error"
    default_message = "Core error."

    def __init__(self, message: str | None = None, detail: dict[str, Any] | None = None) -> None:
        if message is None:
            message = self.default_message
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def __str__(self) -> str:
        return self.message


class EmailError(CoreError):
    code = "email_error"
    default_message = "Email error."


class EmailAuthError(EmailError):
    code = "email_auth_error"
    default_message = "Email authentication error."


class EmailConfigError(EmailError):
    code = "email_config_error"
    default_message = "Email configuration error."


class EmailAccountRecordError(EmailConfigError):
    code = "email_account_record_error"
    default_message = "Account record is invalid."


class EmailProviderConfigError(EmailConfigError):
    code = "email_provider_config_error"
    default_message = "Provider configuration is invalid."


class EmailInvalidExpiryError(EmailConfigError):
    code = "email_invalid_expiry_error"
    default_message = "Invalid or unparseable expiry value."


class EmailInvalidCredentialsDataError(EmailConfigError):
    code = "email_invalid_credentials_data_error"
    default_message = "App credentials data is structurally invalid."


class EmailInvalidTokenDataError(EmailConfigError):
    code = "email_invalid_token_data_error"
    default_message = "Token data is structurally invalid."


class EmailMissingAppCredentialsError(EmailConfigError):
    code = "email_missing_app_credentials_error"
    default_message = "Missing app credentials."


class EmailDuplicateAccountLabelError(EmailConfigError):
    code = "email_duplicate_account_label_error"
    default_message = "Account label already exists."


class EmailAccountNotFoundError(EmailError):
    code = "email_account_not_found_error"
    default_message = "Account not found."


class EmailMissingTokenError(EmailAuthError):
    code = "email_missing_token_error"
    default_message = "Access token is missing."


class EmailMissingRefreshTokenError(EmailAuthError):
    code = "email_missing_refresh_token_error"
    default_message = "Refresh token is missing."


class EmailRefreshFailedError(EmailAuthError):
    code = "email_refresh_failed_error"
    default_message = "Token refresh failed."


class EmailNotAuthenticatedError(EmailAuthError):
    code = "email_not_authenticated_error"
    default_message = "Client is not authenticated."


class EmailRecipientsMissingError(EmailError):
    code = "email_recipients_missing_error"
    default_message = "At least one recipient is required."


class EmailExternalAPIError(EmailError):
    code = "email_external_api_error"
    default_message = "External API call failed."


class EmailAttachmentNotFound(EmailError):
    """Provider returned 404/410 for an attachment fetch — D-17."""
    code = "email_attachment_not_found"
    default_message = "Attachment is no longer available at the provider."


class EmailAttachmentDownloadFailed(EmailError):
    """Generic attachment download failure (provider 5xx, 403, network).

    The ``detail`` dict carries a ``reason`` of ``"forbidden"`` (provider
    403, suggests scope/permission issue) or ``"unavailable"`` (provider
    5xx persistent or network noise after retries) — services translate
    each into a different HTTP status (502 vs 503).
    """
    code = "email_attachment_download_failed"
    default_message = "Attachment download from the provider failed."


class EmailAttachmentTooLargeForProvider(EmailError):
    """Provider rejected an attachment as exceeding its size limit.

    The local D-01/D-02 limits should have caught this earlier; this
    error covers the edge case where a tenant configures a smaller
    limit than the application defaults to.
    """
    code = "email_attachment_too_large_for_provider"
    default_message = "Attachment exceeds the provider's per-message size limit."


class EmailAttachmentBlockedByProvider(EmailError):
    """Gmail rejected an attachment via ``400 The attachment is invalid``.

    The local blocklist (D-04a) should have caught this earlier; this
    error covers divergence between the canonical app list and the
    provider's actual filter.
    """
    code = "email_attachment_blocked_by_provider"
    default_message = "Attachment was blocked by the provider's content filter."


class EmailAttachmentSendFailed(EmailError):
    """One or more attachments failed to upload during ``send_draft`` (D-27).

    The ``detail`` dict carries ``failed_attachments`` — a list of
    ``{draft_attachment_id, filename, reason}`` records identifying
    which attachments could not be uploaded. The send has NOT been
    issued; the local draft is intact and (Outlook only) any
    attachments that succeeded before the failure carry a populated
    ``provider_attachment_id`` so a retry skips them.
    """
    code = "email_attachment_send_failed"
    default_message = "One or more attachments failed to upload during send."


class EmailReplyContextFetchError(EmailError):
    """Provider-side failure (or local coherence mismatch) while preparing
    the data needed to open a Reply / Reply All / Forward composer.

    Used by:

    - ``GmailClient.fetch_reply_context`` / ``OutlookClient.fetch_reply_context``
      when the underlying ``messages.get`` (Gmail) or ``GET /me/messages/{id}``
      (Outlook) fails after token refresh — e.g. the message was deleted
      between the user opening the viewer and clicking Reply.
    - ``validate_reply_threading_coherence`` (Gmail-side guard, see
      :py:func:`core.email.helpers.validate_reply_threading_coherence`)
      when the local triple-check fails. The ``detail['reason']`` carries
      one of ``"thread_id_mismatch"`` / ``"message_id_not_referenced"`` /
      ``"subject_mismatch"`` so the service layer can surface a precise
      message and tests can pin the failure mode.

    The service translates this to ``EmailReplyContextError`` (HTTP 502).
    """
    code = "email_reply_context_fetch_error"
    default_message = "Failed to fetch reply context from the provider."
