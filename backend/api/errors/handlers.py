"""
FastAPI exception handlers for API errors.

Handlers translate domain-specific exceptions into consistent HTTP responses.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountMisconfigured,
    AccountNotConnected,
    AccountNotFound,
    AccountOperationError,
    AccountTokensLoadError,
    ApiError,
    AppCredentialsInvalid,
    AppCredentialsLoadError,
    AppCredentialsMissing,
    AttachmentBlockedExtension,
    AttachmentCopySourceUnavailable,
    AttachmentInsertError,
    AttachmentLimitExceeded,
    AttachmentListingError,
    AttachmentLookupError,
    AttachmentMessageSizeExceeded,
    AttachmentNotFound,
    AttachmentProviderForbidden,
    AttachmentProviderUnavailable,
    AttachmentSendFailed,
    AttachmentTooLarge,
    AttachmentUnavailable,
    ConversationFetchError,
    CredentialFileError,
    DatabaseConnectionError,
    DatabaseMigrationError,
    DatabaseQueryError,
    DevLoginDisabled,
    DevLoginNotLocalhost,
    DraftAttachmentNotFound,
    DraftCreationError,
    DraftDeleteError,
    DraftListError,
    DraftNotFound,
    DraftSendError,
    DraftSyncError,
    DraftUpdateError,
    EmailContentFetchError,
    EmailReplyContextError,
    FavoriteSyncError,
    FavoriteUpdateError,
    EmailFetchError,
    EmailNotFound,
    EmailNotInTrash,
    EmailSendError,
    EnvVarError,
    ExternalAPIError,
    Forbidden,
    InvalidAdminToken,
    MailboxLookupError,
    MailboxNotFound,
    MailboxOperationError,
    MoveToTrashError,
    PurgeDisabled,
    RecipientsMissing,
    RecipientSuggestionsError,
    ReadStatusUpdateError,
    EmailListError,
    RequestTooLarge,
    ServiceUnavailableError,
    SessionOperationError,
    SpamMoveError,
    SpamRestoreError,
    TokenDecryptionError,
    TokenEncryptionError,
    TokenIntegrityError,
    TooManyRequests,
    TrashOperationError,
    Unauthorized,
    UserNotFound,
    UserOperationError,
    VirtualMailboxListError,
    VirtualMailboxNotFound,
    VirtualMailboxOperationError,
)
from api.schemas.error import ErrorResponse


_STATUS_MAP: dict[type[ApiError], int] = {
    Unauthorized: status.HTTP_401_UNAUTHORIZED,
    Forbidden: status.HTTP_403_FORBIDDEN,
    MailboxNotFound: status.HTTP_404_NOT_FOUND,
    AccountNotFound: status.HTTP_404_NOT_FOUND,
    EmailNotFound: status.HTTP_404_NOT_FOUND,
    UserNotFound: status.HTTP_404_NOT_FOUND,
    AccountMisconfigured: status.HTTP_400_BAD_REQUEST,
    AppCredentialsInvalid: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AppCredentialsMissing: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AccountConnectAuthError: status.HTTP_401_UNAUTHORIZED,
    AccountNotConnected: status.HTTP_409_CONFLICT,
    EmailContentFetchError: status.HTTP_502_BAD_GATEWAY,
    EmailReplyContextError: status.HTTP_502_BAD_GATEWAY,
    ConversationFetchError: status.HTTP_502_BAD_GATEWAY,
    EmailFetchError: status.HTTP_502_BAD_GATEWAY,
    EmailSendError: status.HTTP_502_BAD_GATEWAY,
    ReadStatusUpdateError: status.HTTP_502_BAD_GATEWAY,
    SpamMoveError: status.HTTP_502_BAD_GATEWAY,
    SpamRestoreError: status.HTTP_502_BAD_GATEWAY,
    RecipientsMissing: status.HTTP_400_BAD_REQUEST,
    ExternalAPIError: status.HTTP_502_BAD_GATEWAY,
    EnvVarError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    CredentialFileError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    DatabaseConnectionError: status.HTTP_503_SERVICE_UNAVAILABLE,
    DatabaseMigrationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    DatabaseQueryError: status.HTTP_503_SERVICE_UNAVAILABLE,
    TokenDecryptionError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    TokenEncryptionError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    TokenIntegrityError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    EmailNotInTrash: status.HTTP_409_CONFLICT,
    TrashOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    MoveToTrashError: status.HTTP_502_BAD_GATEWAY,
    EmailListError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    DraftCreationError: status.HTTP_502_BAD_GATEWAY,
    DraftDeleteError: status.HTTP_502_BAD_GATEWAY,
    DraftListError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    DraftNotFound: status.HTTP_404_NOT_FOUND,
    DraftSyncError: status.HTTP_502_BAD_GATEWAY,
    DraftUpdateError: status.HTTP_502_BAD_GATEWAY,
    DraftSendError: status.HTTP_502_BAD_GATEWAY,
    MailboxOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    MailboxLookupError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AppCredentialsLoadError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AccountTokensLoadError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AccountOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    SessionOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    UserOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # Attachment errors (see decisionesTomadasAdjuntosBackend.md tabla §5).
    AttachmentBlockedExtension: status.HTTP_400_BAD_REQUEST,
    AttachmentLimitExceeded: status.HTTP_400_BAD_REQUEST,
    AttachmentMessageSizeExceeded: status.HTTP_400_BAD_REQUEST,
    AttachmentTooLarge: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    AttachmentNotFound: status.HTTP_404_NOT_FOUND,
    AttachmentUnavailable: status.HTTP_404_NOT_FOUND,
    AttachmentCopySourceUnavailable: status.HTTP_404_NOT_FOUND,
    DraftAttachmentNotFound: status.HTTP_404_NOT_FOUND,
    AttachmentProviderForbidden: status.HTTP_502_BAD_GATEWAY,
    AttachmentProviderUnavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    AttachmentSendFailed: status.HTTP_502_BAD_GATEWAY,
    AttachmentLookupError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AttachmentInsertError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AttachmentListingError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    RequestTooLarge: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    PurgeDisabled: status.HTTP_503_SERVICE_UNAVAILABLE,
    InvalidAdminToken: status.HTTP_401_UNAUTHORIZED,
    DevLoginDisabled: status.HTTP_503_SERVICE_UNAVAILABLE,
    DevLoginNotLocalhost: status.HTTP_403_FORBIDDEN,
    # Favourites
    FavoriteUpdateError: status.HTTP_502_BAD_GATEWAY,
    FavoriteSyncError: status.HTTP_502_BAD_GATEWAY,
    # Virtual mailboxes
    VirtualMailboxNotFound: status.HTTP_404_NOT_FOUND,
    VirtualMailboxOperationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    VirtualMailboxListError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # Contacts (recipient autocomplete)
    RecipientSuggestionsError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # Health / readiness
    ServiceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # Rate limiting
    TooManyRequests: status.HTTP_429_TOO_MANY_REQUESTS,
}


def _error_payload(exc: ApiError) -> dict:
    """
    Build a standard JSON response payload for API errors.
    """
    payload = ErrorResponse(
        error={"code": exc.code, "message": exc.message, "detail": exc.detail}
    )
    return payload.model_dump()


def register_error_handlers(app: FastAPI) -> None:
    """
    Register all exception handlers for the API.
    """

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        status_code = _STATUS_MAP.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        # Emit a ``Retry-After`` header whenever the error carries one in its
        # detail (generic: any ApiError may opt in; today only TooManyRequests
        # does). The value also rides in the JSON body via ``_error_payload``,
        # which the cross-origin SPA reads (the header is not CORS-exposed).
        headers: dict[str, str] | None = None
        retry_after = exc.detail.get("retry_after") if isinstance(exc.detail, dict) else None
        if retry_after is not None:
            headers = {"Retry-After": str(int(retry_after))}
        return JSONResponse(
            status_code=status_code, content=_error_payload(exc), headers=headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        fallback = ApiError("Unexpected server error.")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload(fallback),
        )
