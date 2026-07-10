"""Fachada del paquete ``emails_service`` — capa de servicio de correo.

Índice temático: ``sincronizacion`` (sync + prefetch/purga), ``envio`` (envío),
``papelera`` (borrar/restaurar/mover a papelera), ``lectura`` (leído/no leído),
``movimientos_buzon`` (spam/archivo), ``listado`` (listar + no leídos),
``favoritos`` (toggle + sync), ``contenido`` (cuerpo + adjuntos),
``contexto_respuesta`` (reply/forward) y ``conversacion`` (vista de hilo).
Helpers transversales en ``_comunes``. Los consumidores importan SIEMPRE desde
esta fachada (``from api.services import emails_service``), nunca desde los
submódulos.
"""

from __future__ import annotations

# Stores re-exportados: los tests de integración parchean sus métodos vía este
# path (``emails_service.account_store`` es el mismo singleton que usan los
# submódulos).
from database import (
    account_store,
    email_attachment_store,
    email_metadata_store,
)

# Sincronización de metadatos + prefetch/purga de contenido
from .sincronizacion import sync_email_metadata

# Envío de correos
from .envio import send_email

# Papelera (borrado permanente / restauración / envío a papelera)
from .papelera import manage_trash, move_to_trash

# Estado de lectura
from .lectura import update_read_status

# Movimientos de buzón (spam / archivo)
from .movimientos_buzon import (
    move_to_archive,
    move_to_spam,
    restore_from_archive,
    restore_from_spam,
)

# Listado paginado + recuento de no leídos
from .listado import count_unread_emails, list_emails

# Favoritos (toggle + sync)
from .favoritos import set_favorite, sync_favorites

# Contenido (cuerpo + adjuntos)
from .contenido import get_email_full_content

# Contexto de respuesta / reenvío
from .contexto_respuesta import get_reply_context

# Vista de conversación
from .conversacion import get_conversation
