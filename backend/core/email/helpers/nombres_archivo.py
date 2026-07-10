"""Nombres de archivo de adjuntos: sanitizacion (D-20), tipo MIME (B-MIME) y Content-Disposition (D-21)."""

from __future__ import annotations

import mimetypes
import re
import urllib.parse
from email.header import decode_header, make_header
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Filename sanitisation (D-20)
# ---------------------------------------------------------------------------

_RESERVED_FS_CHARS = set('/\\:?*<>|"')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def _split_extension(name: str) -> tuple[str, str]:
    """Split a filename into ``(stem, extension_with_dot_or_empty)``.

    The extension is everything after the **last** ``.`` (lowercase
    comparison happens elsewhere). Filenames without a ``.`` return
    ``(name, "")``. A leading ``.`` (dotfiles like ``.env``) is treated
    as part of the stem so the file is not orphaned without a name.
    """
    if not name:
        return "", ""
    idx = name.rfind(".")
    if idx <= 0 or idx == len(name) - 1:
        return name, ""
    return name[:idx], name[idx:]


def sanitize_filename(name: str, existing: Iterable[str] = ()) -> str:
    """Sanitise an attachment filename for storage and download (D-20).

    Rules:
    - Replace path-separator and Windows-reserved characters
      (``/`` ``\\`` ``:`` ``?`` ``*`` ``<`` ``>`` ``|`` ``"``) and
      control bytes (``0x00``..``0x1F``) with ``-``.
    - Replace ``..`` sequences with ``-`` to neutralise path traversal.
    - Preserve UTF-8 characters (accents, CJK) — slugifying them would
      mangle legitimate filenames.
    - Preserve the extension (text after the last ``.``).
    - Prefix Windows-reserved base names (``CON``, ``PRN``, ``AUX``,
      ``NUL``, ``COM1``..``COM9``, ``LPT1``..``LPT9``) with ``_`` so
      ``CON.pdf`` becomes ``_CON.pdf``.
    - If the resulting name collides with one already in ``existing``,
      append `` (1)``, `` (2)``... before the extension.
    """
    raw = name or ""
    cleaned_chars: list[str] = []
    for ch in raw:
        if ch in _RESERVED_FS_CHARS or (ord(ch) < 0x20):
            cleaned_chars.append("-")
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars).replace("..", "-").strip()
    if not cleaned:
        cleaned = "attachment"

    stem, ext = _split_extension(cleaned)
    if stem.upper() in _RESERVED_WINDOWS_NAMES:
        stem = "_" + stem
    base = stem + ext

    existing_set = set(existing)
    if base not in existing_set:
        return base
    counter = 1
    while True:
        candidate = f"{stem} ({counter}){ext}"
        if candidate not in existing_set:
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Attachment MIME type resolution (B-MIME)
# ---------------------------------------------------------------------------

# The runtime/test container (Linux, Python 3.12) ships a mimetypes table that
# does NOT know the common Office Open XML, RAR/7z, and WebP extensions:
# guess_type returns None for .docx/.xlsx/.pptx/.rar/.7z/.webp (verified inside
# mailmanager-backend-1). Without registering them, the star case of the fix —
# an attachment declared as the generic ``application/octet-stream`` but named
# ``factura.xlsx`` — would still resolve to octet-stream and download without
# its real type. Registration is therefore MANDATORY, not optional. ``add_type``
# is idempotent: registering a type already known to the table is a no-op, so
# this never clobbers extensions that already resolve natively (.pdf, .png, …).
_EXTRA_MIME_TYPES: tuple[tuple[str, str], ...] = (
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    ("application/vnd.rar", ".rar"),
    ("application/x-7z-compressed", ".7z"),
    ("image/webp", ".webp"),
)
for _mime, _ext in _EXTRA_MIME_TYPES:
    mimetypes.add_type(_mime, _ext)

# MIME types the provider declares when it does not really know the type. When
# the declared type is one of these, the filename extension is the more
# reliable signal.
_GENERIC_MIME_TYPES = frozenset(
    {
        "",
        "application/octet-stream",
        "application/binary",
        "binary/octet-stream",
    }
)


def resolve_attachment_mime_type(filename: str | None, declared_mime_type: str | None) -> str:
    """Resolve the most reliable MIME type for a received attachment.

    Priority (B-MIME, agreed with the user):
    1. A **specific** declared type wins and is returned verbatim — its
       case is preserved (no forced lowercase), unifying the historical
       Gmail/Outlook asymmetry on the declared type.
    2. If the declared type is generic (``application/octet-stream`` and
       friends) or absent, derive the type from the filename extension via
       :func:`mimetypes.guess_type` (Office types registered above).
    3. Fall back to ``application/octet-stream`` when neither yields a type.

    A generic declared type must never shadow a recognisable extension, and
    an extension guess must never shadow a specific declared type.
    """
    declared = (declared_mime_type or "").strip()
    if declared and declared.lower() not in _GENERIC_MIME_TYPES:
        return declared
    guessed, _enc = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Attachment filename recovery from raw MIME headers (B-NAME-GMAIL)
# ---------------------------------------------------------------------------

# Detects an RFC 2047 encoded-word (``=?charset?B?...?=`` / ``...?Q?...?=``).
# RFC 2231 (``filename*=utf-8''…``) is decoded by ``email.message.Message``
# itself; only the encoded-word form needs a manual pass.
_RFC2047_ENCODED_WORD_RE = re.compile(r"=\?[^?]+\?[bBqQ]\?[^?]*\?=")


def _decode_rfc2047_if_needed(value: str) -> str:
    """Decode an RFC 2047 encoded-word to Unicode; idempotent on plain text.

    Only acts when the value literally contains an encoded-word token, so
    applying it to an already-legible name is a no-op. Soft-fails to the
    raw value on any decode error — a slightly ugly name beats losing it.
    """
    if not value or not _RFC2047_ENCODED_WORD_RE.search(value):
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # pragma: no cover — defensive: malformed encoded-word
        return value


def extract_filename_from_headers(headers: Iterable[dict[str, Any]] | None) -> str | None:
    """Recover an attachment filename from raw MIME headers (B-NAME-GMAIL).

    Some senders leave a Gmail ``MessagePart.filename`` empty and put the
    name only in ``Content-Disposition: …; filename="x"`` or, failing that,
    ``Content-Type: …; name="x"``. ``headers`` is the provider's
    ``[{"name": ..., "value": ...}, ...]`` list.

    Precedence: ``Content-Disposition: filename=`` → ``Content-Type: name=``.
    RFC 2231 (``filename*=utf-8''…``) is decoded by the legacy
    :class:`email.message.Message` parser; RFC 2047 encoded-words are decoded
    on top of the parser's output. Returns ``None`` when neither header
    carries a usable name.
    """
    raw_cd: str | None = None
    raw_ct: str | None = None
    for header in headers or []:
        name = (header.get("name") or "").lower()
        value = (header.get("value") or "").strip()
        if not value:
            continue
        if name == "content-disposition" and raw_cd is None:
            raw_cd = value
        elif name == "content-type" and raw_ct is None:
            raw_ct = value

    if raw_cd:
        message = Message()
        message["Content-Disposition"] = raw_cd
        filename = message.get_filename()
        if filename:
            decoded = _decode_rfc2047_if_needed(str(filename)).strip()
            if decoded:
                return decoded

    if raw_ct:
        message = Message()
        message["Content-Type"] = raw_ct
        name_param = message.get_param("name")
        if name_param:
            # get_param returns a 3-tuple for RFC 2231 values; collapse it.
            if isinstance(name_param, tuple):
                name_param = collapse_rfc2231_value(name_param)
            decoded = _decode_rfc2047_if_needed(str(name_param)).strip()
            if decoded:
                return decoded

    return None


# ---------------------------------------------------------------------------
# Content-Disposition header (D-21)
# ---------------------------------------------------------------------------


def format_content_disposition(filename: str) -> str:
    """Build an ``attachment; filename=…; filename*=UTF-8''…`` header.

    Emits both the legacy ASCII form (``filename="…"``, with ``?``
    fallbacks for non-ASCII bytes) and the RFC 5987 percent-encoded
    form (``filename*=UTF-8''…``) so all common clients render the
    correct UTF-8 name. FastAPI does not produce this combined form
    automatically — the service constructs the header explicitly.
    """
    safe = filename or "attachment"
    # Neutralise characters that break the quoted-string ``filename="…"``
    # form: non-ASCII (``?`` after the replace round-trip), the ``"``
    # delimiter itself, and the ``\`` RFC 6266 escape char. The real
    # UTF-8 name still rides on ``filename*`` for capable clients.
    ascii_fallback = (
        safe.encode("ascii", errors="replace")
        .decode("ascii")
        .replace("?", "_")
        .replace('"', "_")
        .replace("\\", "_")
    )
    quoted_utf8 = urllib.parse.quote(safe, safe="")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted_utf8}"
    )
