"""Cliente Gmail: clase compuesta por los mixins tematicos del paquete."""

from __future__ import annotations

import time
from typing import Callable

from google.oauth2.credentials import Credentials

from ..email_client import EmailClient
from .autenticacion import GmailAutenticacionMixin
from .borradores import GmailBorradoresMixin
from .buzones import GmailBuzonesMixin
from .contenido import GmailContenidoMixin
from .envio import GmailEnvioMixin
from .sincronizacion import GmailSincronizacionMixin


class GmailClient(
    GmailAutenticacionMixin,
    GmailSincronizacionMixin,
    GmailEnvioMixin,
    GmailBorradoresMixin,
    GmailBuzonesMixin,
    GmailContenidoMixin,
    EmailClient,
):
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

    def get_account_label(self) -> str:
        """
        Return the label that identifies this Gmail account inside the app.
        """
        return self._account_label
