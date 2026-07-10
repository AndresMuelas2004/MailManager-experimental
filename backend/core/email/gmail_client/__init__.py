"""Fachada del paquete ``gmail_client`` — expone el cliente Gmail compuesto.

Los submódulos son mixins temáticos internos; los consumidores importan
SIEMPRE desde esta fachada. Índice: ``autenticacion`` (OAuth interactivo y
silencioso), ``sincronizacion`` (metadatos: bootstrap, History API, lotes),
``envio`` (correo directo y envío de borradores), ``borradores`` (CRUD de
borradores y Reply/Forward), ``buzones`` (leído, favoritos, papelera, spam,
archivo), ``contenido`` (cuerpo+adjuntos, conversaciones, binarios) y
``_comunes`` (privados transversales).
"""

from __future__ import annotations

# Privados re-exportados únicamente porque los tests los importan desde este path.
from ._comunes import _is_retryable, _split_address_header
from .autenticacion import GMAIL_SCOPES
from .borradores import _DRAFTS_MAX_TOTAL
from .cliente import GmailClient
from .sincronizacion import _INCREMENTAL_EVENT_THRESHOLD
