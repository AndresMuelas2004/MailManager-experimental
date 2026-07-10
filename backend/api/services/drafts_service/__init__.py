"""Fachada del paquete ``drafts_service`` — capa de servicio de borradores.

Índice temático: ``gestion`` (crear / actualizar / borrar / listar),
``sincronizacion`` (sync desde el proveedor), ``envio`` (envío con adjuntos),
``adjuntos`` (alta / baja / copia de adjuntos) y ``_comunes`` (helpers
transversales). Los consumidores importan SIEMPRE desde esta fachada
(``from api.services import drafts_service``), nunca desde los submódulos.
"""

from __future__ import annotations

# Stores re-exportados: los consumidores (tests de integración) parchean sus
# métodos vía este path (``drafts_service.draft_store`` es el mismo singleton
# que usan los submódulos).
from database import (
    account_store,
    draft_attachment_store,
    draft_store,
    email_attachment_store,
    email_metadata_store,
)

# Gestión de borradores (crear / actualizar / borrar / listar)
from .gestion import create_draft, delete_draft, list_drafts, update_draft

# Sincronización desde el proveedor
from .sincronizacion import sync_drafts

# Envío de borradores
from .envio import send_draft

# Adjuntos de borradores
from .adjuntos import (
    add_draft_attachment,
    copy_attachments_from_email,
    remove_draft_attachment,
)
