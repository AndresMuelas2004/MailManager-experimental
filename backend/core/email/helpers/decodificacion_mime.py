"""Decodificacion de cuerpos MIME (base64url + charset) con estrategia UTF-8-first."""

from __future__ import annotations

import base64
import binascii
import codecs
import logging

logger = logging.getLogger(__name__)


def _is_ascii_masquerading_charset(hint: str) -> bool:
    """True for charsets whose byte stream is pure ASCII (escape/shift based).

    ISO-2022-* (Japanese/Korean mail), HZ-GB-2312 and UTF-7 encode non-ASCII
    text entirely with bytes < 0x80, so a body in any of them ALWAYS decodes
    "successfully" under strict UTF-8 — the UTF-8-first strategy would return
    the raw escape sequences as garbage instead of failing over. For these
    charsets the declared hint must win. Resolved through ``codecs.lookup``
    so every alias (``iso2022_jp``, ``csISO2022JP``, …) maps to the same
    canonical name.
    """
    try:
        # ``CodecInfo.name`` is not separator-consistent across codecs
        # ("utf-7" vs "iso2022_jp"), so normalise before comparing.
        canonical = codecs.lookup(hint).name.replace("-", "_")
    except LookupError:
        return False
    return canonical.startswith("iso2022_") or canonical in {"hz", "utf_7"}


def decode_mime_body(data_b64url: str, charset_hint: str | None) -> str | None:
    """Decode a base64url-encoded MIME body into a Python str.

    Gmail returns each MIME part's body as ``base64url`` bytes plus a declared
    ``charset`` taken from the part's ``Content-Type`` header. That declaration
    is unreliable: senders frequently mislabel UTF-8 bytes as ``iso-8859-1`` or
    ``windows-1252`` (a common bug in older mail clients). Trusting the hint
    blindly produces mojibake — UTF-8 byte sequences like ``C3 A9`` (``é``)
    get re-interpreted as Latin-1 and rendered as ``Ã©``.

    Strategy (UTF-8-first with validated fallback):
    0. **Exception — ASCII-masquerading declared charsets.** ISO-2022-* /
       HZ / UTF-7 bodies are pure ASCII bytes, so strict UTF-8 "succeeds"
       on them while producing garbage (visible escape sequences). When the
       declared charset is one of those, try it strictly FIRST; only on
       failure fall through to the regular strategy below.
    1. **Try UTF-8 strict.** Real UTF-8 bodies always decode without error.
       A legitimate Latin-1 body with any non-ASCII byte ≥ 0x80 will fail
       on UTF-8 continuation-byte validation, so we cannot misdecode it here.
    2. **Fallback to the declared charset** (if any, and if it isn't UTF-8
       itself). Covers correctly-labelled Latin-1 / Windows-1252 / Big5 / etc.
    3. **Last resort: UTF-8 with ``errors="replace"``.** Preserves the ASCII
       portion of the body even when both prior attempts fail.

    Returns ``None`` only if the base64 payload itself is invalid.
    """
    try:
        raw = base64.urlsafe_b64decode(data_b64url + "==")
    except binascii.Error:
        return None
    declared = (charset_hint or "").strip()
    if declared and _is_ascii_masquerading_charset(declared):
        try:
            return raw.decode(declared)
        except UnicodeDecodeError:
            logger.debug(
                "decode_mime_body: declared ASCII-masquerading charset=%s failed; "
                "falling back to UTF-8-first strategy",
                declared,
            )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    hint = (charset_hint or "").strip().lower()
    if hint and hint not in {"utf-8", "utf8"}:
        try:
            decoded = raw.decode(hint, errors="replace")
            logger.debug(
                "decode_mime_body: UTF-8 strict failed; decoded with declared charset=%s",
                hint,
            )
            return decoded
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")
