"""Fachada del paquete ``helpers`` — utilidades puras compartidas por los clientes de email.

Índice temático: cada submódulo agrupa una responsabilidad. Los consumidores
externos importan SIEMPRE desde esta fachada (``core.email.helpers``), nunca
desde los submódulos internos (regla de fachada pública de la capa core).
"""

from __future__ import annotations

# Ensamblado del mensaje RFC 5322 saliente
from .construccion_mime import build_mime_with_attachments

# Credenciales y tokens (SecretStr, expiraciones)
from .credenciales import (
    parse_expiry,
    unwrap_app_credentials,
    unwrap_user_tokens,
    wrap_account_tokens,
)

# Decodificación de cuerpos MIME entrantes
from .decodificacion_mime import decode_mime_body

# Estrategias de envío/subida por tamaño (D-18)
from .estrategias_envio import (  # _OUTLOOK…: privado re-exportado — lo importan los tests
    _OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES,
    GmailSendStrategy,
    OutlookAttachmentStrategy,
    pick_gmail_send_strategy,
    pick_outlook_attachment_strategy,
)

# Reply / Reply All / Forward (asunto, destinatarios, cabeceras, coherencia)
from .hilos_respuesta import (
    build_in_reply_to_and_references,
    build_reply_subject,
    compute_reply_recipients,
    validate_reply_threading_coherence,
)

# Conversiones HTML ↔ texto y citas del composer
from .html_texto import (  # privados re-exportados: _HTML_QUOTE… (lockstep con el sanitizador saliente) y _html_to_text — los importan los tests
    _HTML_QUOTE_BLOCKQUOTE_STYLE,
    _html_to_text,
    build_quoted_body,
    build_quoted_body_html,
    flatten_html_document,
    html_to_plain_text_alternative,
    plain_text_to_html,
)

# Imágenes inline ``cid:``
from .imagenes_cid import find_referenced_cids, inline_cid_images, normalize_cid

# Metadatos sincronizados
from .metadatos import dedupe_metadata_by_message_id

# Nombres de archivo, tipo MIME y Content-Disposition de adjuntos
from .nombres_archivo import (
    extract_filename_from_headers,
    format_content_disposition,
    resolve_attachment_mime_type,
    sanitize_filename,
)

# Reintentos con backoff (D-16)
from .reintentos import http_error_detail, retry_with_backoff
