"""Autenticacion OAuth de Gmail: flujo interactivo en dos fases y refresco silencioso."""

from __future__ import annotations

from typing import Any

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ..errors import (
    EmailExternalAPIError,
    EmailInvalidCredentialsDataError,
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailProviderConfigError,
    EmailRefreshFailedError,
)
from ..helpers import parse_expiry, unwrap_app_credentials, unwrap_user_tokens, wrap_account_tokens


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailAutenticacionMixin:
    """Metodos de autenticacion de :class:`~core.email.gmail_client.cliente.GmailClient`."""

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

