"""
Database package public surface.
"""

from __future__ import annotations

from database.connection import close_pool
from database.errors import (
    ConnectionPoolError,
    CredentialReadError,
    DatabaseError,
    MigrationError,
    QueryError,
    SettingsError,
    TokenCryptoError,
    TokenDecryptError,
    TokenEncryptError,
    TokenValidationError,
    UnknownProviderError,
)
from database.lifecycle import (
    run_startup_migrations_if_enabled,
    validate_token_encryption_config,
    warmup_connection,
)
from database.repositories import (
    account_backfill_store,
    account_store,
    draft_attachment_store,
    draft_store,
    draft_sync_store,
    email_attachment_store,
    email_content_store,
    email_metadata_store,
    mailbox_store,
    session_store,
    user_store,
    virtual_mailbox_store,
)
from database.security import load_app_credentials
from database.settings import get_frontend_origin, get_google_oauth_redirect_uri

__all__ = [
    "account_backfill_store",
    "account_store",
    "close_pool",
    "draft_attachment_store",
    "draft_store",
    "draft_sync_store",
    "email_attachment_store",
    "email_content_store",
    "ConnectionPoolError",
    "CredentialReadError",
    "DatabaseError",
    "email_metadata_store",
    "get_frontend_origin",
    "get_google_oauth_redirect_uri",
    "load_app_credentials",
    "mailbox_store",
    "MigrationError",
    "QueryError",
    "run_startup_migrations_if_enabled",
    "session_store",
    "SettingsError",
    "TokenCryptoError",
    "TokenDecryptError",
    "TokenEncryptError",
    "TokenValidationError",
    "UnknownProviderError",
    "user_store",
    "validate_token_encryption_config",
    "virtual_mailbox_store",
    "warmup_connection",
]
