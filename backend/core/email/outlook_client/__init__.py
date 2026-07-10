"""Fachada del paquete ``outlook_client`` — expone el cliente Outlook compuesto.

Los submódulos son mixins temáticos internos; los consumidores importan
SIEMPRE desde esta fachada. Índice: ``transporte`` (peticiones Graph con
reintentos + cabecera ImmutableId), ``autenticacion`` (OAuth con PKCE),
``sincronizacion`` (metadatos: bootstrap y deltas por carpeta), ``envio``
(correo directo, borradores y subida de adjuntos), ``borradores`` (CRUD y
createReply*/createForward), ``buzones`` (leído, favoritos, papelera, spam,
archivo) y ``contenido`` (cuerpo+adjuntos, conversaciones, binarios).
"""

from __future__ import annotations

from .autenticacion import OUTLOOK_SCOPES

# Privados re-exportados únicamente porque los tests los importan desde este path.
from .borradores import _DRAFTS_MAX_RETRIES, _DRAFTS_MAX_TOTAL
from .buzones import _BOX_TO_FOLDER
from .cliente import OutlookClient
from .envio import _extract_attachment_id_from_location
from .sincronizacion import _DELTA_FOLDERS, _DELTA_PAGE_SIZE, _DELTA_SELECT_FIELDS, _FOLDER_TO_BOX
from .transporte import GRAPH_BASE_URL, _parse_graph_datetime
