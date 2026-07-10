"""Tests espejo de ``services_helpers.traduccion_errores``: translate_connect_error e is_auth_error."""

from __future__ import annotations

from api.errors.exceptions import (
    AccountConnectAuthError,
    ExternalAPIError,
)
from api.services.services_helpers import is_auth_error, translate_connect_error
from core.email.errors import EmailAuthError, EmailExternalAPIError


# ------------------------------------------------------------------
# translate_connect_error
# ------------------------------------------------------------------

class TestTranslateConnectError:

    def test_email_auth_error_returns_account_connect_auth_error(self):
        exc = EmailAuthError("Token rejected.")
        result = translate_connect_error(exc)
        assert isinstance(result, AccountConnectAuthError)
        assert result.detail.get("core_code") == EmailAuthError.code

    def test_other_core_error_uses_standard_mapping(self):
        exc = EmailExternalAPIError("API fail")
        result = translate_connect_error(exc)
        assert isinstance(result, ExternalAPIError)

    def test_non_core_error_uses_fallback(self):
        exc = RuntimeError("unexpected")
        result = translate_connect_error(exc)
        assert isinstance(result, AccountConnectAuthError)


# ------------------------------------------------------------------
# is_auth_error
# ------------------------------------------------------------------

class TestIsAuthError:

    def test_true_for_email_auth_error(self):
        exc = EmailAuthError("token expired")
        assert is_auth_error(exc) is True

    def test_false_for_other_core_error(self):
        exc = EmailExternalAPIError("API fail")
        assert is_auth_error(exc) is False

    def test_false_for_non_core_error(self):
        exc = RuntimeError("something")
        assert is_auth_error(exc) is False
