"""Reply / Reply All / Forward: asunto, destinatarios, cabeceras RFC 5322 y guarda de coherencia."""

from __future__ import annotations

import re
from typing import Iterable, Literal

from ..errors import EmailReplyContextFetchError


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
