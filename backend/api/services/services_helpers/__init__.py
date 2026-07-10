"""Fachada del paquete ``services_helpers`` — utilidades compartidas por la capa de servicios.

Índice temático: cada submódulo agrupa una responsabilidad. Los consumidores
(servicios y routers) importan SIEMPRE desde esta fachada
(``from api.services.services_helpers import ...``), nunca desde los submódulos
internos.
"""

from __future__ import annotations

# Sanitizadores HTML (re-exports directos de sus pipelines dedicados)
from api.services.email_html_pipeline import prepare_email_html as sanitize_email_html
from api.services.outbound_html_pipeline import sanitize_outbound_html

# Content-Disposition de adjuntos (re-export de core.email; lo usa attachments_routers)
from core.email import format_content_disposition

# Traducción de errores de capa (auth / core / database / connect)
from .traduccion_errores import (
    is_auth_error,
    translate_auth_error,
    translate_connect_error,
    translate_core_error,
    translate_database_error,
)

# Contexto de cuentas: mailbox, manager, credenciales, tokens y errores de auth
from .contexto_cuentas import (  # _wrap_secret: privado re-exportado — lo importan los tests
    _wrap_secret,
    build_account_sync_failures,
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    unwrap_secret,
)

# Persistencia y actualización de metadatos + cursores de sync
from .persistencia_metadatos import (
    delete_email_metadata_batch,
    load_suspect_message_ids,
    load_sync_cursors,
    persist_email_metadata_batch,
    update_email_metadata_labels_batch,
    update_email_read_status_batch,
    update_email_read_status_by_thread,
    update_email_spam_status_batch,
    update_sync_cursor,
)

# Operaciones de papelera
from .papelera import (
    get_trash_emails_by_ids,
    mark_as_deleted_batch,
    move_to_trash_batch,
    restore_from_trash_batch,
    restore_from_trash_discovered_batch,
)

# Contenido de correos (cuerpos cacheados, TTL, prefetch)
from .contenido import (
    get_email_content,
    list_unread_recent_uncached,
    persist_email_content,
    purge_expired_email_content,
    touch_email_content_last_accessed,
)

# Parser de la query de búsqueda (lupa)
from .busqueda import (
    ParsedSearchQuery,
    parse_search_query,
    parse_search_tokens,
)

# Adjuntos: recálculo del flag has_attachments
from .adjuntos import recompute_has_attachments

# Mapeo de fila a modelo de respuesta
from .mapeo import row_to_email_metadata_out
