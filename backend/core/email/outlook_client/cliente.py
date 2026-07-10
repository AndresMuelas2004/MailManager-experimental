"""Cliente Outlook: clase compuesta por los mixins tematicos del paquete."""

from __future__ import annotations

import time
from typing import Callable

from ..email_client import EmailClient
from .autenticacion import OutlookAutenticacionMixin
from .borradores import OutlookBorradoresMixin
from .buzones import OutlookBuzonesMixin
from .contenido import OutlookContenidoMixin
from .envio import OutlookEnvioMixin
from .sincronizacion import OutlookSincronizacionMixin
from .transporte import OutlookTransporteMixin


class OutlookClient(
    OutlookAutenticacionMixin,
    OutlookSincronizacionMixin,
    OutlookEnvioMixin,
    OutlookBorradoresMixin,
    OutlookBuzonesMixin,
    OutlookContenidoMixin,
    OutlookTransporteMixin,
    EmailClient,
):
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

    def get_account_label(self) -> str:
        """
        Return the label that identifies this Outlook account inside the app.
        """
        return self._account_label
