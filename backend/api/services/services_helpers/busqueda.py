"""Parser de la query de busqueda (lupa): texto libre + operadores estilo Gmail + override de bandeja ``in:``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


_MAX_SEARCH_TOKENS = 10


def parse_search_tokens(q: str | None) -> list[str]:
    """Strip *q* and split into up to 10 non-empty whitespace-separated tokens."""
    if q is None:
        return []
    return [t for t in q.strip().split() if t][:_MAX_SEARCH_TOKENS]


# ---------------------------------------------------------------------------
# Search query parser (free text + Gmail-style operators)
# ---------------------------------------------------------------------------
#
# The lupa (``GET /emails`` and the virtual-mailbox listing) accepts a single
# ``q`` string that mixes free-text tokens with Gmail-style operators
# (``from:`` ``to:`` ``subject:`` ``has:attachment`` ``before:`` ``after:``
# ``is:read|unread|favorite|starred`` ``in:``). ``parse_search_query`` splits
# ``q`` into three independent buckets so the service can pass free-text
# tokens (unchanged semantics) and typed operator clauses to the repository,
# and apply the ``in:`` box override. Everything stays a pure string
# operation — no DB, no provider call.
#
# Tolerance policy (decided with the user, see the feature docs):
#   - Unknown operator key (``foo:bar``)        -> treated as a literal free
#                                                  text token (handled by the
#                                                  tokenizer: the prefix is not
#                                                  a known operator so the whole
#                                                  term, colon included, is a
#                                                  value).
#   - Known operator with unsupported value     -> the operator is dropped
#     (``is:importante``, ``has:drive``,           (handled in the translation
#      ``in:archivados``, ``before:ayer``)         step below).
# Never raises on the content of ``q``; only the router's ``min_length`` /
# ``max_length`` can reject ``q``.

_MAX_FREE_TOKENS = _MAX_SEARCH_TOKENS  # free-text tokens cap (same as the lupa)
_MAX_OPERATOR_CLAUSES = 10             # operator-clause cap; the excess is dropped

_KNOWN_OPERATORS = {"from", "to", "subject", "has", "before", "after", "is", "in"}

# ``is:`` values map to the (clause_kind, typed_value) the repository expects.
_IS_VALUES: dict[str, tuple[str, bool]] = {
    "read": ("is_read_op", True),
    "unread": ("is_read_op", False),
    "favorite": ("is_favorite_op", True),
    "starred": ("is_favorite_op", True),
}
_HAS_VALUES = {"attachment", "attachments"}
# ``in:`` maps to a stored ``box`` value. ``DELETED`` is intentionally absent
# — it is an internal "trash emptied" state the lupa must not be able to
# select (``in:trash`` targets ``TRASH``, never ``DELETED``).
_IN_VALUES: dict[str, str] = {
    "inbox": "ALL_MAIL",
    "allmail": "ALL_MAIL",
    "sent": "SENT",
    "spam": "SPAM",
    "trash": "TRASH",
    "archive": "ARCHIVE",
}

_SEARCH_TIMEZONE = ZoneInfo("Europe/Madrid")
# Accept AAAA/MM/DD and AAAA-MM-DD only; zero-padded, fixed widths. ``before:``
# / ``after:`` are interpreted at local Madrid midnight (see _parse_date_boundary).
_DATE_RE = re.compile(r"^(\d{4})[/-](\d{2})[/-](\d{2})$")

# Free-text/operator prefix is ASCII letters only; anything else (digit,
# accent, punctuation) starting a term means the term is not an operator.
_KEYWORD_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class ParsedSearchQuery:
    """The decomposition of a raw ``q`` search string.

    - ``tokens``: free-text phrases (quotes already stripped), capped at
      ``_MAX_FREE_TOKENS``. Same semantics the lupa always had (substring,
      accent/case-insensitive, OR across subject/from, AND across tokens).
    - ``operator_clauses``: ``(kind, typed_value)`` pairs the repository
      resolves against ``_OPERATOR_CLAUSE_BUILDERS``, capped at
      ``_MAX_OPERATOR_CLAUSES``. ``kind`` carries the ``_op`` suffix (or a
      distinct name) on purpose so it never collides with the saved-filter
      keys of ``_EXTRA_FILTER_BUILDERS``.
    - ``box_override``: a stored box value from ``in:`` (``ALL_MAIL`` / ``SENT``
      / ``SPAM`` / ``TRASH``) or ``None``. The service decides how to apply it
      (override in the regular listing, intersection in a virtual mailbox).
    """

    tokens: list[str] = field(default_factory=list)
    operator_clauses: list[tuple[str, Any]] = field(default_factory=list)
    box_override: str | None = None


def _parse_date_boundary(value: str) -> datetime | None:
    """Parse ``AAAA/MM/DD`` / ``AAAA-MM-DD`` into local Madrid midnight.

    Returns ``None`` for any malformed or out-of-range date (e.g. month 13,
    day 32, non-zero-padded fields) so the caller can silently drop the
    filter. The tz is the real ``Europe/Madrid`` zone (CET/CEST), not a fixed
    offset — ``datetime(..., tzinfo=ZoneInfo(...))`` yields the correct local
    midnight across DST so the ``>=`` / ``<`` boundary comparisons against
    ``received_at`` (TIMESTAMPTZ) line up with what the user means.
    """
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(year, month, day, tzinfo=_SEARCH_TIMEZONE)
    except ValueError:
        return None


def _tokenize_search_query(q: str) -> list[tuple[str | None, str]]:
    """Left-to-right scan of *q* into ``(keyword|None, value)`` terms.

    A term is an operator only when an ASCII-letter prefix is immediately
    followed by ``:`` AND the lowercased prefix is a known operator; the
    ``keyword`` is then the lowercased prefix and ``value`` is read next.
    Anything else is free text: ``keyword`` is ``None`` and the whole term
    (letters and colon included) becomes the ``value`` — this is what turns
    an unknown operator like ``foo:bar`` into a literal token.

    Value reading honours double quotes: a ``"`` opens a quoted value read up
    to the next ``"`` (quotes excluded, the content may contain spaces and
    ``:``); an unterminated quote takes the rest of the string. An unquoted
    value runs up to the next whitespace.
    """
    terms: list[tuple[str | None, str]] = []
    i = 0
    n = len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue

        keyword: str | None = None
        # Try to read an operator keyword: ASCII letters + immediate ':'.
        km = _KEYWORD_RE.match(q, i)
        if km is not None:
            end = km.end()
            if end < n and q[end] == ":" and km.group(0).lower() in _KNOWN_OPERATORS:
                keyword = km.group(0).lower()
                i = end + 1  # consume the keyword and the ':'

        # Read the value (quoted or up to whitespace). When this term is free
        # text (keyword is None) the scan starts at the term's first char, so
        # the letters/colon are part of the value.
        if i < n and q[i] == '"':
            i += 1
            start = i
            while i < n and q[i] != '"':
                i += 1
            value = q[start:i]
            if i < n:  # skip the closing quote when present
                i += 1
        else:
            start = i
            while i < n and not q[i].isspace():
                i += 1
            value = q[start:i]

        terms.append((keyword, value))
    return terms


def parse_search_query(q: str | None) -> ParsedSearchQuery:
    """Decompose *q* into free-text tokens, operator clauses and a box override.

    See :class:`ParsedSearchQuery` and the module-level notes for the
    tolerance policy. Never raises on the content of *q*.
    """
    if q is None:
        return ParsedSearchQuery()
    q = q.strip()
    if not q:
        return ParsedSearchQuery()

    tokens: list[str] = []
    operator_clauses: list[tuple[str, Any]] = []
    box_override: str | None = None

    for keyword, value in _tokenize_search_query(q):
        if keyword is None:
            if value:
                tokens.append(value)
            continue

        if keyword in ("from", "to", "subject"):
            if value:
                kind = {
                    "from": "from_contains",
                    "to": "to_contains",
                    "subject": "subject_contains_op",
                }[keyword]
                operator_clauses.append((kind, value))
            continue

        if keyword == "has":
            if value.lower() in _HAS_VALUES:
                operator_clauses.append(("has_attachments", True))
            continue

        if keyword in ("before", "after"):
            boundary = _parse_date_boundary(value)
            if boundary is not None:
                kind = "received_before" if keyword == "before" else "received_after"
                operator_clauses.append((kind, boundary))
            continue

        if keyword == "is":
            mapped = _IS_VALUES.get(value.lower())
            if mapped is not None:
                operator_clauses.append(mapped)
            continue

        if keyword == "in":
            mapped_box = _IN_VALUES.get(value.lower())
            if mapped_box is not None:
                # The last valid ``in:`` wins when several are present.
                box_override = mapped_box
            continue

    return ParsedSearchQuery(
        tokens=tokens[:_MAX_FREE_TOKENS],
        operator_clauses=operator_clauses[:_MAX_OPERATOR_CLAUSES],
        box_override=box_override,
    )
