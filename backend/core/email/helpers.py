"""
Shared helpers for email client implementations.

These pure functions handle token wrapping/unwrapping, expiry parsing,
filename sanitisation, retry policy and MIME assembly, and are shared
by all EmailClient subclasses.
"""

from __future__ import annotations

import base64
import binascii
import enum
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Callable, Iterable, TypeVar

from pydantic import SecretStr

from .errors import (
    EmailAttachmentSendFailed,
    EmailInvalidCredentialsDataError,
    EmailInvalidExpiryError,
    EmailInvalidTokenDataError,
)

logger = logging.getLogger(__name__)


T = TypeVar("T")


def http_error_detail(exc: Any) -> tuple[str, str]:
    """Extract (status, reason) from a googleapiclient HttpError."""
    status = getattr(getattr(exc, "resp", None), "status", "unknown")
    reason = getattr(exc, "reason", "unknown")
    return status, reason


def parse_expiry(value: Any) -> datetime | None:
    """Parse an expiry value (datetime, timestamp, or ISO string) into a naive UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError) as exc:
            raise EmailInvalidExpiryError(
                f"Invalid expiry timestamp ({type(exc).__name__}): {exc}"
            ) from exc
        except Exception as exc:
            raise EmailInvalidExpiryError(
                f"Unexpected expiry timestamp error ({type(exc).__name__}): {exc}"
            ) from exc
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise EmailInvalidExpiryError(
                f"Invalid expiry ISO string ({type(exc).__name__}): {exc}"
            ) from exc
        except Exception as exc:
            raise EmailInvalidExpiryError(
                f"Unexpected expiry parsing error ({type(exc).__name__}): {exc}"
            ) from exc
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    raise EmailInvalidExpiryError(
        f"Unsupported expiry type: {type(value).__name__}"
    )


def unwrap_app_credentials(app_credentials: dict[str, Any] | None) -> dict[str, Any]:
    """Unwrap SecretStr values from an app credentials dict."""
    try:
        payload = dict(app_credentials or {})
    except (TypeError, ValueError) as exc:
        raise EmailInvalidCredentialsDataError(
            f"Invalid app credentials ({type(exc).__name__}): {exc}"
        ) from exc
    except Exception as exc:
        raise EmailInvalidCredentialsDataError(
            f"Unexpected error unwrapping app credentials ({type(exc).__name__}): {exc}"
        ) from exc
    secret = payload.get("client_secret")
    if isinstance(secret, SecretStr):
        payload["client_secret"] = secret.get_secret_value()
    return payload


def unwrap_user_tokens(user_tokens: dict[str, Any] | None) -> dict[str, Any]:
    """Unwrap SecretStr values from a user token dict."""
    try:
        payload = dict(user_tokens or {})
    except (TypeError, ValueError) as exc:
        raise EmailInvalidTokenDataError(
            f"Invalid user tokens ({type(exc).__name__}): {exc}"
        ) from exc
    except Exception as exc:
        raise EmailInvalidTokenDataError(
            f"Unexpected error unwrapping user tokens ({type(exc).__name__}): {exc}"
        ) from exc
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if isinstance(access_token, SecretStr):
        payload["access_token"] = access_token.get_secret_value()
    if isinstance(refresh_token, SecretStr):
        payload["refresh_token"] = refresh_token.get_secret_value()
    return payload


def wrap_account_tokens(token_data: dict[str, Any]) -> dict[str, Any]:
    """Wrap sensitive token fields as SecretStr."""
    try:
        payload = dict(token_data or {})
    except (TypeError, ValueError) as exc:
        raise EmailInvalidTokenDataError(
            f"Invalid token data ({type(exc).__name__}): {exc}"
        ) from exc
    except Exception as exc:
        raise EmailInvalidTokenDataError(
            f"Unexpected error wrapping tokens ({type(exc).__name__}): {exc}"
        ) from exc
    if "access_token" in payload:
        payload["access_token"] = SecretStr(str(payload.get("access_token")))
    if "refresh_token" in payload and payload["refresh_token"] is not None:
        payload["refresh_token"] = SecretStr(str(payload.get("refresh_token")))
    return payload


def decode_mime_body(data_b64url: str, charset_hint: str | None) -> str | None:
    """Decode a base64url-encoded MIME body into a Python str.

    Gmail returns each MIME part's body as ``base64url`` bytes plus a declared
    ``charset`` taken from the part's ``Content-Type`` header. That declaration
    is unreliable: senders frequently mislabel UTF-8 bytes as ``iso-8859-1`` or
    ``windows-1252`` (a common bug in older mail clients). Trusting the hint
    blindly produces mojibake — UTF-8 byte sequences like ``C3 A9`` (``é``)
    get re-interpreted as Latin-1 and rendered as ``Ã©``.

    Strategy (UTF-8-first with validated fallback):
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


_CID_REF_PATTERN = re.compile(
    r"""(?P<attr>src|background)\s*=\s*(?P<quote>["']?)cid:<?(?P<cid>[^"'>\s]+?)>?(?P=quote)""",
    re.IGNORECASE,
)

# Matches ``url(cid:<id>)`` inside CSS values — e.g. ``style="background-image:url(cid:…)"``
# produced by premailer when it inlines CSS rules from <style> blocks.
_CID_URL_FUNC_PATTERN = re.compile(
    r"""url\(\s*(?P<quote>["']?)cid:<?(?P<cid>[^"'>)\s]+?)>?(?P=quote)\s*\)""",
    re.IGNORECASE,
)


def _cid_replacer(cid_map: dict[str, str], formatter):
    """Build a regex ``sub`` replacer that resolves ``cid:<id>`` references.

    ``formatter(match, data_url)`` receives the match and the resolved data URL
    and returns the replacement string. Unmapped CIDs fall through to the
    original match text (soft fallback — broken image beats lost email).
    """
    def _replace(match: re.Match[str]) -> str:
        cid = match.group("cid").strip()
        data_url = cid_map.get(cid)
        if data_url is None:
            return match.group(0)
        return formatter(match, data_url)
    return _replace


def inline_cid_images(html: str, cid_map: dict[str, str]) -> str:
    """Replace ``cid:<id>`` references with data URLs from ``cid_map``.

    Handles two forms:
    - HTML attributes: ``src="cid:…"`` / ``background="cid:…"``.
    - CSS ``url(cid:…)`` inside ``style="…"`` (after premailer CSS inlining).

    Tolerant to single/double/no quotes and optional angle brackets around
    the CID. Unmapped CIDs are left untouched (soft fallback).
    """
    if not html or not cid_map:
        return html

    html = _CID_REF_PATTERN.sub(
        _cid_replacer(cid_map, lambda m, url: f'{m.group("attr")}="{url}"'),
        html,
    )
    html = _CID_URL_FUNC_PATTERN.sub(
        _cid_replacer(cid_map, lambda _m, url: f'url("{url}")'),
        html,
    )
    return html


def find_referenced_cids(html: str | None) -> set[str]:
    """Return the set of CIDs referenced in ``html``.

    Inspects both the HTML attribute form (``src="cid:…"`` /
    ``background="cid:…"``) and the CSS ``url(cid:…)`` form (produced by
    premailer when inlining ``<style>`` rules). Used by D-13 to decide
    whether an inline-marked attachment is actually referenced by the
    body and therefore should remain inline; if no reference exists it
    is promoted to a downloadable attachment.
    """
    if not html:
        return set()
    referenced: set[str] = set()
    for match in _CID_REF_PATTERN.finditer(html):
        cid = match.group("cid").strip()
        if cid:
            referenced.add(cid)
    for match in _CID_URL_FUNC_PATTERN.finditer(html):
        cid = match.group("cid").strip()
        if cid:
            referenced.add(cid)
    return referenced


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
    ascii_fallback = (
        safe.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    )
    quoted_utf8 = urllib.parse.quote(safe, safe="")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted_utf8}"
    )


# ---------------------------------------------------------------------------
# Retry helper (D-16)
# ---------------------------------------------------------------------------


_DEFAULT_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS_SECONDS,
    is_retryable: Callable[[Exception], bool] | None = None,
    retry_after_extractor: Callable[[Exception], float | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``fn`` with up to ``attempts`` total tries on retryable errors.

    Default delays are 1s, 2s, 4s — fixed exponential backoff (D-16). If
    the caller passes ``retry_after_extractor`` and it returns a
    positive float for the failing exception, that value overrides the
    default delay (honours ``Retry-After`` from the provider).

    ``is_retryable(exc)`` decides which exceptions trigger a retry.
    Non-retryable exceptions propagate immediately. The default predicate
    retries any ``OSError`` / ``IOError`` (network noise) — provider-
    specific retry policy belongs in the caller.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if is_retryable is None:
        is_retryable = lambda exc: isinstance(exc, OSError)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = delays[min(attempt, len(delays) - 1)] if delays else 0.0
            if retry_after_extractor is not None:
                hint = retry_after_extractor(exc)
                if hint is not None and hint > 0:
                    delay = hint
            sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Upload strategy enums (D-18)
# ---------------------------------------------------------------------------


class GmailSendStrategy(enum.Enum):
    """Cut-off for Gmail draft sends — based on TOTAL MIME bytes."""
    SIMPLE = "simple"        # <= 5 MB total MIME: drafts.send with raw in JSON
    RESUMABLE = "resumable"  # > 5 MB: /upload/.../drafts/send?uploadType=resumable


class OutlookAttachmentStrategy(enum.Enum):
    """Cut-off for Outlook attachment uploads — based on per-attachment bytes."""
    SIMPLE = "simple"                  # < 3 MB: POST /attachments with contentBytes
    UPLOAD_SESSION = "upload_session"  # >= 3 MB: createUploadSession + chunked PUT


_GMAIL_SEND_RESUMABLE_THRESHOLD_BYTES: int = 5 * 1024 * 1024
_OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES: int = 3 * 1024 * 1024


def pick_gmail_send_strategy(total_mime_bytes: int) -> GmailSendStrategy:
    """Decide Gmail's send strategy from the total MIME size.

    Total here means body + base64-encoded attachments + headers — the
    full RFC 5322 message that goes on the wire. Beyond 5 MB the JSON
    metadata path no longer accepts ``Message.raw`` and the resumable
    upload URI is required.
    """
    if total_mime_bytes > _GMAIL_SEND_RESUMABLE_THRESHOLD_BYTES:
        return GmailSendStrategy.RESUMABLE
    return GmailSendStrategy.SIMPLE


def pick_outlook_attachment_strategy(per_attachment_bytes: int) -> OutlookAttachmentStrategy:
    """Decide Outlook's per-attachment upload strategy.

    Outlook decides per attachment, not per message — call this once per
    attachment. ``POST /attachments`` rejects payloads >= 3 MB; the
    upload session path rejects payloads < 3 MB
    (``ErrorAttachmentSizeShouldNotBeLessThanMinimumSize``).
    """
    if per_attachment_bytes >= _OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES:
        return OutlookAttachmentStrategy.UPLOAD_SESSION
    return OutlookAttachmentStrategy.SIMPLE


# ---------------------------------------------------------------------------
# MIME assembly for Gmail send (D-31 + Gmail multipart/mixed)
# ---------------------------------------------------------------------------


def _split_mime_type(mime_type: str | None) -> tuple[str, str]:
    """Split a MIME type into ``(maintype, subtype)`` with octet-stream fallback."""
    if not mime_type or "/" not in mime_type:
        return "application", "octet-stream"
    main, _, sub = mime_type.partition("/")
    return main.strip() or "application", sub.strip() or "octet-stream"


def build_mime_with_attachments(
    *,
    to_recipients: list[str],
    cc_recipients: list[str],
    bcc_recipients: list[str],
    subject: str,
    body: str,
    attachments: list[dict[str, Any]],
    from_email: str | None = None,
) -> bytes:
    """Build a complete RFC 5322 message with text/plain body + attachments.

    Uses ``email.message.EmailMessage`` (Python's modern email API). The
    body is set as ``text/plain; charset=utf-8`` (D-31). Each attachment
    in ``attachments`` is a dict with keys ``filename``, ``mime_type``,
    ``data`` (bytes); optional ``content_id`` and ``is_inline`` are
    honoured for inline parts (composer doesn't ship them today, but the
    helper supports them so the call site is uniform).

    Returns the raw bytes of the MIME message (not base64url-encoded).
    Callers wrap the bytes in ``base64.urlsafe_b64encode`` for the
    JSON ``Message.raw`` form, or send them as ``message/rfc822`` for
    ``uploadType=resumable``.

    The recipients lists may be empty individually, but at least one
    address overall is the caller's responsibility to enforce. Empty
    lists collapse to absent headers (Python's email API accepts that).
    """
    msg = EmailMessage()
    if from_email:
        msg["From"] = from_email
    if to_recipients:
        msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    if bcc_recipients:
        msg["Bcc"] = ", ".join(bcc_recipients)
    if subject:
        msg["Subject"] = subject
    msg.set_content(body or "", subtype="plain", charset="utf-8")

    for attachment in attachments:
        data = attachment["data"]
        if not isinstance(data, (bytes, bytearray)):
            raise EmailAttachmentSendFailed(
                f"Attachment data must be bytes/bytearray, got {type(data).__name__}",
                detail={"reason": "invalid_attachment_data"},
            )
        maintype, subtype = _split_mime_type(attachment.get("mime_type"))
        filename = attachment.get("filename") or "attachment"
        cid = attachment.get("content_id")
        is_inline = bool(attachment.get("is_inline", False))
        kwargs: dict[str, Any] = {
            "maintype": maintype,
            "subtype": subtype,
            "filename": filename,
        }
        if is_inline:
            kwargs["disposition"] = "inline"
            kwargs["cid"] = cid if cid else f"<{make_msgid(domain='mailmanager.local')[1:-1]}>"
        msg.add_attachment(bytes(data), **kwargs)

    return msg.as_bytes()
