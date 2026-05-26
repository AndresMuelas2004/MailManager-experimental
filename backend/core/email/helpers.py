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
import html as _html_lib
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Callable, Iterable, Literal, TypeVar

from pydantic import SecretStr

from .errors import (
    EmailAttachmentSendFailed,
    EmailInvalidCredentialsDataError,
    EmailInvalidExpiryError,
    EmailInvalidTokenDataError,
    EmailReplyContextFetchError,
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
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a complete RFC 5322 message with text/plain body + attachments.

    Uses ``email.message.EmailMessage`` (Python's modern email API). The
    body is set as ``text/plain; charset=utf-8`` (D-31). Each attachment
    in ``attachments`` is a dict with keys ``filename``, ``mime_type``,
    ``data`` (bytes); optional ``content_id`` and ``is_inline`` are
    honoured for inline parts (composer doesn't ship them today, but the
    helper supports them so the call site is uniform).

    ``extra_headers`` carries arbitrary RFC 5322 header injections used
    by the Reply / Forward flow (``In-Reply-To``, ``References``).
    The keys are unique by construction (it's a ``dict``) so a single
    call cannot accidentally duplicate a header — Python's
    :py:class:`email.message.EmailMessage` would otherwise append on
    repeated subscript assignment. ``None`` and empty-string values
    are silently skipped so callers can pass partial maps without a
    pre-filter.

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
    if extra_headers:
        for name, value in extra_headers.items():
            if value is None:
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            msg[name] = value_str
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


# ---------------------------------------------------------------------------
# Reply / Reply All / Forward helpers (R-01..R-12 — see Ignore/reply-feature.md)
# ---------------------------------------------------------------------------


# Common ``Re:`` prefixes across locales (English, German ``AW:``,
# Swedish ``SV:``). ``Re :`` with the rogue space is folded by the
# ``\s*:\s*`` token. We do NOT support numbered variants like ``Re[2]:``
# in MVP — the clients that emit them are very rare and the failure
# mode (the prefix is treated as part of the subject body) is benign
# (the thread still forms via ``threadId`` / ``conversationId``).
_RE_PREFIX_RE = re.compile(r"^\s*(re|aw|sv)\s*:\s*", re.IGNORECASE)

# Forward prefixes — English ``Fw:`` / ``Fwd:`` plus Spanish ``RV:`` /
# ``Reenv:`` (and the rarer plain ``Reenviar:``). Symmetric handling
# with ``_RE_PREFIX_RE``: detect-once, prepend-on-miss.
_FWD_PREFIX_RE = re.compile(r"^\s*(fwd?|rv|reenv)\s*:\s*", re.IGNORECASE)

# Repeated-prefix normaliser used by ``validate_reply_threading_coherence``
# to compare the original and the new subject regardless of how many
# ``Re:`` (or ``Fwd:``) layers were stacked in either direction. Applied
# in a loop until no further match — single ``re.sub`` would stop after
# one substitution.
_REPLY_PREFIX_NORMALISE_RE = re.compile(
    r"^(\s*(?:re|aw|sv|fwd?|rv|reenv)\s*:\s*)+", re.IGNORECASE,
)


def build_reply_subject(original_subject: str, action: str) -> str:
    """Build the ``Subject:`` for a Reply / Reply All / Forward.

    For ``reply`` / ``reply_all``: prepend ``Re:`` unless one of the
    accepted Re-variants already prefixes the subject. For ``forward``:
    prepend ``Fwd:`` unless a Fwd-variant already does.

    The original prefix is preserved (case + spacing) instead of being
    rewritten to a canonical form, so the user does not see the subject
    shape change on re-reply (Gmail web does the same).

    Unknown ``action`` values default to ``reply`` semantics — soft
    fallback, the caller stays responsible for passing a valid value.
    """
    base = (original_subject or "").strip()
    if action == "forward":
        if _FWD_PREFIX_RE.match(base):
            return base
        return f"Fwd: {base}" if base else "Fwd:"
    # reply / reply_all (and unknown actions — soft fallback)
    if _RE_PREFIX_RE.match(base):
        return base
    return f"Re: {base}" if base else "Re:"


def _normalise_subject_for_match(subject: str) -> str:
    """Strip every stacked Re/Fwd prefix and lowercase the result.

    Used by :py:func:`validate_reply_threading_coherence` to decide
    whether the draft's ``Subject:`` still belongs to the same thread
    as the original. Gmail's documented rule is "Subject headers must
    match" — the doc does NOT specify case-sensitivity nor prefix
    handling, but every reference client (Gmail web, Apple Mail,
    Outlook) tolerates ``Re:`` chains. Matching after normalisation is
    the safest interpretation: if the normalised forms are equal,
    Gmail accepts the draft into the thread; if they diverge, we
    surface a local error before paying for the round trip.
    """
    base = (subject or "").strip()
    while True:
        stripped, count = _REPLY_PREFIX_NORMALISE_RE.subn("", base, count=1)
        if count == 0:
            break
        base = stripped.strip()
    return base.lower()


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """Return ``items`` with duplicates removed, preserving first-seen order.

    Case-insensitive on the email address part — RFC 5321 declares the
    local part case-sensitive in theory but every real mail system
    treats it as case-insensitive. Returning a single canonical form
    avoids surprising the user with ``Foo@x`` and ``foo@x`` both
    landing in the CC.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def compute_reply_recipients(
    *,
    original_from: str,
    original_reply_to: list[str] | None,
    original_to: list[str] | None,
    original_cc: list[str] | None,
    current_account_email: str | None,
    action: Literal["reply", "reply_all", "forward"],
    original_box: str,
) -> tuple[list[str], list[str]]:
    """Compute ``(to_recipients, cc_recipients)`` for a Reply / Reply All / Forward.

    Rules (R-10 included):

    - ``forward`` → ``([], [])``. The user fills in the recipients.
    - ``reply`` / ``reply_all``:
        - **Primary (To)** = ``reply_to`` when present, otherwise
          ``[from]`` (RFC 2822: a sender that asks to be replied
          elsewhere — mailing lists, no-reply forwarders — gets
          respected). Empty / whitespace-only ``reply_to`` collapses
          back to ``from``.
        - **Self-reply** (the user replies to a message they themself
          sent — ``original_box == 'SENT'``): the ``To`` becomes the
          original ``to`` list, so the reply lands on the original
          recipient instead of looping back to the user.
        - **CC (Reply All only)** = ``original_to ∪ original_cc``,
          minus the current account's address and minus the primary
          To list. Deduplicated case-insensitively.
        - **CC (Reply)** = ``[]``.

    ``current_account_email`` may be ``None`` (the connect flow did not
    fetch the address); the filter just becomes a no-op and the user
    appears auto-included in the CC. The service layer can mitigate
    this by calling the provider's ``_fetch_sender_email`` cache before
    invoking us.
    """
    reply_to = [r for r in (original_reply_to or []) if r and r.strip()]
    to_list = [r for r in (original_to or []) if r and r.strip()]
    cc_list = [r for r in (original_cc or []) if r and r.strip()]

    if action == "forward":
        return [], []

    if (original_box or "").upper() == "SENT":
        primary_raw = to_list or ([original_from] if original_from else [])
    else:
        if reply_to:
            primary_raw = reply_to
        elif original_from:
            primary_raw = [original_from]
        else:
            primary_raw = []

    primary = _dedupe_preserve_order(primary_raw)

    if action != "reply_all":
        return primary, []

    raw_cc = list(to_list) + list(cc_list)
    excluded: set[str] = {item.lower() for item in primary}
    if current_account_email:
        excluded.add(current_account_email.strip().lower())
    filtered_cc = [r for r in raw_cc if r.strip().lower() not in excluded]
    return primary, _dedupe_preserve_order(filtered_cc)


def build_in_reply_to_and_references(
    original_message_id: str,
    original_references: str,
) -> tuple[str, str]:
    """Compute the RFC 5322 ``In-Reply-To`` and ``References`` headers.

    Both headers are returned **with** surrounding angle brackets if
    the original message id did not already carry them — Gmail and
    Apple Mail accept either form, but ``<id>`` is the canonical
    spelling and avoids ambiguity when a future caller concatenates
    multiple ids.

    ``References`` extends the original ``References`` chain with the
    new id (RFC 5322 § 3.6.4). If the original carried no ``References``
    header, ``References`` collapses to the single new id — that is
    still legal and lets clients that walk the chain (Apple Mail,
    Thunderbird) re-thread on the destination.

    Empty / whitespace-only ``original_message_id`` returns ``("", "")``:
    we never inject a header with a missing value, which would produce
    a malformed ``In-Reply-To: <>`` on the wire.
    """
    raw = (original_message_id or "").strip()
    if not raw:
        return "", ""
    in_reply_to = raw if raw.startswith("<") and raw.endswith(">") else f"<{raw.strip('<>')}>"
    prior = (original_references or "").strip()
    if prior:
        references = f"{prior} {in_reply_to}"
    else:
        references = in_reply_to
    return in_reply_to, references


# Tags whose textual content must NOT leak into the quoted body.
# ``html_to_text`` drops the entire subtree of these instead of
# emitting their inner text.
_TEXT_DROP_TAGS = frozenset({"script", "style", "head", "title", "meta", "link"})

# Tags that should produce a line break in the plain-text output.
# Approximates how a renderer would visually flow the document — close
# enough for quotation purposes.
_TEXT_BREAK_TAGS = frozenset(
    {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "hr",
        "blockquote",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
    }
)


def html_to_text(html: str | None, *, max_chars: int = 50_000) -> str:
    """Degrade an HTML body to plain text for the Reply / Forward quote.

    Intentionally simpler than the rendering pipeline
    (``api.services.email_html_pipeline``): that pipeline produces a
    sanitised HTML fragment suitable for an iframe; here we want a
    plain-text degradation suitable for a ``<textarea>``. Differences:

    - Scripts, styles and metadata subtrees are discarded outright (no
      ``<style>`` content leaking as visible characters).
    - Block-level tags emit a newline so the visual line breaks of the
      original survive into the quote. ``<br>`` collapses to a single
      newline; ``<p>`` / ``<div>`` / list items / table rows behave the
      same to keep the output readable without sucking in `lxml`'s
      smart-rendering layer.
    - HTML entities are decoded (``&amp;`` → ``&``).
    - Output is clipped to ``max_chars`` so a 1 MB newsletter cannot
      blow up the composer field.

    Implementation uses Python's stdlib :py:class:`html.parser.HTMLParser`
    so this module remains import-safe regardless of which optional
    HTML stack (``lxml``, ``beautifulsoup``) is available. The plan
    initially suggested lxml; stdlib produces an identical result for
    the simple needs of this helper and keeps the dependency surface
    minimal.

    ``html`` of ``None`` or empty string returns ``""`` (soft fallback —
    the caller may then build a header-only quote).
    """
    if not html:
        return ""

    from html.parser import HTMLParser

    chunks: list[str] = []
    skip_depth = 0

    class _Extractor(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                skip_depth += 1
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_endtag(self, tag: str) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                if skip_depth > 0:
                    skip_depth -= 1
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            # Self-closing tags like ``<br/>``.
            if tag.lower() in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_data(self, data: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(data)

        def handle_entityref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&{name};"))

        def handle_charref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&#{name};"))

    try:
        parser = _Extractor(convert_charrefs=False)
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover — defensive: parser bugs
        # Soft fallback: a visible-but-ugly text is better than losing
        # the entire quote. Strip tags brute-force via regex.
        logger.warning("html_to_text parser failed (%s): %s", type(exc).__name__, exc)
        stripped = re.sub(r"<[^>]+>", "", html)
        return _html_lib.unescape(stripped)[:max_chars]

    raw = "".join(chunks)
    raw = _html_lib.unescape(raw)
    # Collapse runs of blank lines to at most two so the quote stays
    # visually tight without losing intentional paragraph breaks.
    raw = re.sub(r"\n[ \t]+", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    raw = raw.strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + "\n[...truncado...]"
    return raw


_SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _format_quoted_header_date(received_at: datetime | None) -> str:
    """Format ``received_at`` like Gmail web's "El 23 de mayo de 2026 a las 14:32".

    Soft fallback to ``"el {iso}"`` if ``received_at`` is ``None`` or
    not a ``datetime`` — the quote stays readable instead of crashing
    on a corrupt row.
    """
    if not isinstance(received_at, datetime):
        return ""
    try:
        local = received_at
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone.utc)
        # Render in UTC for now — i18n is out of MVP scope (R-05).
        month = _SPANISH_MONTHS[local.month - 1] if 1 <= local.month <= 12 else str(local.month)
        return f"El {local.day} de {month} de {local.year} a las {local.hour:02d}:{local.minute:02d}"
    except Exception:  # pragma: no cover — defensive
        return f"El {received_at.isoformat()}"


def _quote_lines(text: str) -> str:
    """Prefix every line of ``text`` with ``"> "`` (Gmail-web style).

    Empty input returns ``""`` (no header without content). A trailing
    newline is preserved so the quote ends with a clean line break.
    """
    if not text:
        return ""
    out_lines = [f"> {line}" if line else ">" for line in text.split("\n")]
    return "\n".join(out_lines)


def build_quoted_body(
    original_body_html: str | None,
    original_body_text: str | None,
    *,
    from_name: str,
    from_email: str,
    received_at: datetime | None,
    action: Literal["reply", "reply_all", "forward"],
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    subject: str | None = None,
) -> str:
    """Build the plain-text body for a Reply / Reply All / Forward composer.

    Output shape (Reply / Reply All):

    ```
    <blank line>
    <blank line>
    El 23 de mayo de 2026 a las 14:32, Ana López <ana@example.com> escribió:

    > Texto original línea 1
    > Texto original línea 2
    ```

    Output shape (Forward — Gmail / Outlook web style):

    ```
    <blank line>
    <blank line>
    ---------- Mensaje reenviado ----------
    De: Ana López <ana@example.com>
    Fecha: El 23 de mayo de 2026 a las 14:32
    Asunto: <subject>
    Para: a@x, b@x
    Cc: c@x

    <body sin prefijo>
    ```

    The two leading blank lines are intentional: the composer cursor
    lands on the first line and the user types **above** the quote
    without pisarla. R-05 makes the body plain-text even when the
    original is HTML — see :py:func:`html_to_text` for the degrader.
    """
    if original_body_text:
        body_text = (original_body_text or "").strip()
    else:
        body_text = html_to_text(original_body_html)

    sender_display = (from_name or "").strip()
    sender_email = (from_email or "").strip()
    if sender_display and sender_email:
        sender = f"{sender_display} <{sender_email}>"
    else:
        sender = sender_display or sender_email or "(remitente desconocido)"

    date_line = _format_quoted_header_date(received_at)

    if action == "forward":
        parts: list[str] = ["", "", "---------- Mensaje reenviado ----------"]
        parts.append(f"De: {sender}")
        if date_line:
            parts.append(f"Fecha: {date_line}")
        if subject:
            parts.append(f"Asunto: {subject}")
        if to_recipients:
            parts.append(f"Para: {', '.join(to_recipients)}")
        if cc_recipients:
            parts.append(f"Cc: {', '.join(cc_recipients)}")
        parts.append("")
        if body_text:
            parts.append(body_text)
        return "\n".join(parts)

    # reply / reply_all
    header = f"{date_line}, {sender} escribió:" if date_line else f"{sender} escribió:"
    quoted = _quote_lines(body_text)
    if quoted:
        return f"\n\n{header}\n\n{quoted}"
    return f"\n\n{header}"


def validate_reply_threading_coherence(
    *,
    thread_id: str,
    in_reply_to: str,
    references: str,
    original_message_id: str,
    original_thread_id: str,
    original_subject: str,
    new_subject: str,
) -> None:
    """Guard the triple-requirement Gmail enforces for thread membership.

    Gmail's "Manage threads" doc states three conditions for a draft to
    be associated with an existing thread:

    1. ``threadId`` matches the original message's ``threadId``.
    2. ``In-Reply-To`` / ``References`` follow RFC 2822 (i.e. include
       the original ``Message-ID``).
    3. The ``Subject`` header matches.

    The doc does **not** document the exact HTTP code Gmail returns
    when any of the three fails (foros report 400 ``Invalid thread_id``
    intermittently). Rather than depending on provider behaviour, we
    verify the three constraints locally **before** building the MIME
    payload — that way the user gets a deterministic
    :py:class:`EmailReplyContextFetchError` with a precise
    ``detail['reason']`` instead of an opaque 502 from Gmail.

    Outlook does NOT need this guard: ``createReply`` /
    ``createReplyAll`` / ``createForward`` are server-side primitives,
    so the coherence is the provider's responsibility there.

    Raises :py:class:`EmailReplyContextFetchError` on any mismatch.
    Returns ``None`` on success.
    """
    if not thread_id or not original_thread_id:
        # Nothing to validate against — the caller should not have
        # invoked us without a thread id; treat as misconfiguration
        # rather than guess.
        raise EmailReplyContextFetchError(
            "Cannot validate threading coherence without both thread ids.",
            {"reason": "thread_id_mismatch"},
        )
    if thread_id != original_thread_id:
        raise EmailReplyContextFetchError(
            "Reply thread_id does not match the original message's thread_id.",
            {
                "reason": "thread_id_mismatch",
                "thread_id": thread_id,
                "original_thread_id": original_thread_id,
            },
        )

    original_msg_id = (original_message_id or "").strip().strip("<>")
    if not original_msg_id:
        raise EmailReplyContextFetchError(
            "Original Message-ID is missing; cannot validate reply threading.",
            {"reason": "message_id_not_referenced"},
        )
    in_reply_to_raw = (in_reply_to or "").strip().strip("<>")
    references_raw = (references or "").lower()
    needle = original_msg_id.lower()
    if needle != in_reply_to_raw.lower() and needle not in references_raw:
        raise EmailReplyContextFetchError(
            "Original Message-ID is not referenced by In-Reply-To or References.",
            {
                "reason": "message_id_not_referenced",
                "original_message_id": original_msg_id,
            },
        )

    new_norm = _normalise_subject_for_match(new_subject)
    original_norm = _normalise_subject_for_match(original_subject)
    if new_norm != original_norm:
        raise EmailReplyContextFetchError(
            "Reply subject does not match the original subject after Re/Fwd normalisation.",
            {
                "reason": "subject_mismatch",
                "new_subject": new_subject,
                "original_subject": original_subject,
            },
        )
