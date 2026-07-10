"""Tests espejo de ``services_helpers.busqueda``: tokenizacion y parseo de la query de busqueda (lupa)."""

from __future__ import annotations

from api.services.services_helpers import parse_search_query, parse_search_tokens


# ------------------------------------------------------------------
# parse_search_tokens
# ------------------------------------------------------------------

class TestParseSearchTokens:

    def test_none_returns_empty_list(self):
        assert parse_search_tokens(None) == []

    def test_empty_string_returns_empty_list(self):
        assert parse_search_tokens("") == []

    def test_only_whitespace_returns_empty_list(self):
        assert parse_search_tokens("   ") == []
        assert parse_search_tokens("\t\n  \t") == []

    def test_strips_outer_whitespace_then_splits(self):
        assert parse_search_tokens("  ab  ") == ["ab"]

    def test_multiple_tokens_split_by_whitespace(self):
        assert parse_search_tokens("foo bar") == ["foo", "bar"]

    def test_collapses_internal_whitespace_runs(self):
        # str.split() with no args treats any whitespace run as one separator
        # and discards the empty pieces — important for "a    b" → ["a", "b"].
        assert parse_search_tokens("a    b\tc\nd") == ["a", "b", "c", "d"]

    def test_caps_at_max_search_tokens(self):
        # _MAX_SEARCH_TOKENS = 10; anything beyond must be silently dropped.
        q = " ".join(f"t{i}" for i in range(15))
        result = parse_search_tokens(q)
        assert len(result) == 10
        assert result == [f"t{i}" for i in range(10)]

    def test_exactly_at_cap_returns_all(self):
        q = " ".join(f"t{i}" for i in range(10))
        assert parse_search_tokens(q) == [f"t{i}" for i in range(10)]


# ------------------------------------------------------------------
# parse_search_query (free text + Gmail-style operators)
# ------------------------------------------------------------------
#
# The parser is a pure string operation: q in, ``ParsedSearchQuery`` out
# (no DB, no provider). It splits q into three independent buckets —
# free-text ``tokens`` (same semantics as ``parse_search_tokens``), typed
# ``operator_clauses`` ((kind, value) pairs the repository resolves), and a
# ``box_override`` from ``in:``. Tolerance policy: an unknown operator key
# stays literal free text; a known operator with an unsupported value is
# dropped. It never raises on the content of q.

class TestParseSearchQueryEmptyInputs:

    def test_none_returns_empty_parse(self):
        result = parse_search_query(None)
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_empty_string_returns_empty_parse(self):
        result = parse_search_query("")
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_only_whitespace_returns_empty_parse(self):
        result = parse_search_query("   \t\n  ")
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_plain_free_text_keeps_token_semantics(self):
        # No operator → identical free-text tokens to parse_search_tokens.
        assert parse_search_query("foo bar").tokens == ["foo", "bar"]
        assert parse_search_query("foo bar").operator_clauses == []


class TestParseSearchQueryTokenizationAndQuotes:

    def test_operator_with_quoted_value_separates_free_text(self):
        result = parse_search_query('from:"john doe" oferta')
        assert result.tokens == ["oferta"]
        assert result.operator_clauses == [("from_contains", "john doe")]

    def test_quoted_subject_value_is_a_single_clause(self):
        result = parse_search_query('subject:"acción requerida"')
        assert result.operator_clauses == [("subject_contains_op", "acción requerida")]
        assert result.tokens == []

    def test_quoted_free_text_phrase_is_one_token(self):
        result = parse_search_query('"frase libre"')
        assert result.tokens == ["frase libre"]
        assert result.operator_clauses == []

    def test_unterminated_quote_takes_rest_of_string(self):
        # An unclosed quote consumes everything after it as the value.
        result = parse_search_query('subject:"sin cerrar y mas')
        assert result.operator_clauses == [("subject_contains_op", "sin cerrar y mas")]


class TestParseSearchQueryOperators:

    def test_from_operator(self):
        assert parse_search_query("from:linkedin").operator_clauses == [
            ("from_contains", "linkedin"),
        ]

    def test_to_operator(self):
        assert parse_search_query("to:ana").operator_clauses == [
            ("to_contains", "ana"),
        ]

    def test_subject_operator(self):
        assert parse_search_query("subject:factura").operator_clauses == [
            ("subject_contains_op", "factura"),
        ]

    def test_has_attachment_singular(self):
        assert parse_search_query("has:attachment").operator_clauses == [
            ("has_attachments", True),
        ]

    def test_has_attachment_plural_alias(self):
        assert parse_search_query("has:attachments").operator_clauses == [
            ("has_attachments", True),
        ]

    def test_is_read(self):
        assert parse_search_query("is:read").operator_clauses == [
            ("is_read_op", True),
        ]

    def test_is_unread(self):
        assert parse_search_query("is:unread").operator_clauses == [
            ("is_read_op", False),
        ]

    def test_is_favorite(self):
        assert parse_search_query("is:favorite").operator_clauses == [
            ("is_favorite_op", True),
        ]

    def test_is_starred_is_alias_of_favorite(self):
        assert parse_search_query("is:starred").operator_clauses == [
            ("is_favorite_op", True),
        ]


class TestParseSearchQueryInOverride:

    def test_in_inbox_maps_to_all_mail(self):
        assert parse_search_query("in:inbox").box_override == "ALL_MAIL"

    def test_in_allmail_maps_to_all_mail(self):
        assert parse_search_query("in:allmail").box_override == "ALL_MAIL"

    def test_in_sent_maps_to_sent(self):
        assert parse_search_query("in:sent").box_override == "SENT"

    def test_in_spam_maps_to_spam(self):
        assert parse_search_query("in:spam").box_override == "SPAM"

    def test_in_trash_maps_to_trash(self):
        assert parse_search_query("in:trash").box_override == "TRASH"

    def test_in_is_not_an_operator_clause(self):
        # ``in:`` lives in ``box_override`` ONLY — it must never leak into
        # operator_clauses (the service applies it as a box override).
        result = parse_search_query("in:sent")
        assert result.operator_clauses == []

    def test_invalid_in_value_yields_no_override(self):
        assert parse_search_query("in:archivados").box_override is None

    def test_in_deleted_is_not_selectable(self):
        # ``DELETED`` is an internal "trash emptied" state the lupa must not
        # be able to target; ``in:deleted`` is an unsupported value.
        assert parse_search_query("in:deleted").box_override is None

    def test_last_valid_in_wins(self):
        assert parse_search_query("in:inbox in:trash").box_override == "TRASH"

    def test_later_invalid_in_does_not_clear_earlier_valid(self):
        assert parse_search_query("in:sent in:archivados").box_override == "SENT"


class TestParseSearchQueryDates:

    def test_before_slash_format_is_madrid_midnight(self):
        result = parse_search_query("before:2026/01/01")
        assert len(result.operator_clauses) == 1
        kind, value = result.operator_clauses[0]
        assert kind == "received_before"
        assert (value.year, value.month, value.day) == (2026, 1, 1)
        assert value.tzinfo is not None
        # Europe/Madrid, not a fixed UTC offset.
        assert value.tzinfo.key == "Europe/Madrid"

    def test_after_dash_format_is_madrid_midnight(self):
        result = parse_search_query("after:2026-01-01")
        kind, value = result.operator_clauses[0]
        assert kind == "received_after"
        assert (value.year, value.month, value.day) == (2026, 1, 1)
        assert value.tzinfo.key == "Europe/Madrid"

    def test_before_and_after_inclusivity_kinds(self):
        # after → received_after (>=, inclusive); before → received_before
        # (<, exclusive). The kind names encode the boundary the repository
        # builders translate to SQL.
        result = parse_search_query("after:2026-01-01 before:2026-02-01")
        kinds = {k for k, _ in result.operator_clauses}
        assert kinds == {"received_after", "received_before"}

    def test_invalid_month_is_dropped(self):
        assert parse_search_query("before:2026/13/01").operator_clauses == []

    def test_invalid_day_is_dropped(self):
        assert parse_search_query("before:2026/02/31").operator_clauses == []

    def test_non_zero_padded_date_is_dropped(self):
        # The regex requires fixed widths: 2026/1/1 does not match.
        assert parse_search_query("before:2026/1/1").operator_clauses == []

    def test_relative_date_word_is_dropped(self):
        assert parse_search_query("before:ayer").operator_clauses == []


class TestParseSearchQueryTolerance:

    def test_unknown_operator_becomes_literal_token(self):
        # ``foo`` is not a known operator, so the whole term (colon included)
        # is free text.
        result = parse_search_query("foo:bar")
        assert result.tokens == ["foo:bar"]
        assert result.operator_clauses == []

    def test_unsupported_is_value_is_dropped(self):
        result = parse_search_query("is:importante")
        assert result.operator_clauses == []
        assert result.tokens == []

    def test_unsupported_has_value_is_dropped(self):
        result = parse_search_query("has:drive")
        assert result.operator_clauses == []

    def test_empty_operator_value_is_dropped(self):
        # ``from:`` with no value (followed by whitespace) emits nothing.
        result = parse_search_query("from: oferta")
        assert result.operator_clauses == []
        assert result.tokens == ["oferta"]

    def test_never_raises_on_arbitrary_content(self):
        # Punctuation, accents, colons in the middle — none of it raises.
        parse_search_query('::: ¿qué? from:"a:b" 漢字 has:')


class TestParseSearchQueryCaseInsensitivity:

    def test_uppercase_operator_key_is_recognised(self):
        assert parse_search_query("FROM:linkedin").operator_clauses == [
            ("from_contains", "linkedin"),
        ]

    def test_mixed_case_is_key_and_value(self):
        assert parse_search_query("Is:Unread").operator_clauses == [
            ("is_read_op", False),
        ]

    def test_uppercase_in_value_is_recognised(self):
        assert parse_search_query("in:SENT").box_override == "SENT"

    def test_from_value_case_is_preserved(self):
        # Operator KEYS are case-insensitive, but the VALUE keeps its case
        # (the repository lowercases it for the ILIKE comparison).
        assert parse_search_query("from:LinkedIn").operator_clauses == [
            ("from_contains", "LinkedIn"),
        ]


class TestParseSearchQueryCombinationAndCaps:

    def test_mixed_free_text_and_operators_split_correctly(self):
        result = parse_search_query("hola from:linkedin mundo subject:oferta")
        assert result.tokens == ["hola", "mundo"]
        assert result.operator_clauses == [
            ("from_contains", "linkedin"),
            ("subject_contains_op", "oferta"),
        ]

    def test_free_text_tokens_capped_at_ten(self):
        q = " ".join(f"t{i}" for i in range(15))
        result = parse_search_query(q)
        assert len(result.tokens) == 10
        assert result.tokens == [f"t{i}" for i in range(10)]

    def test_operator_clauses_capped_at_ten(self):
        q = " ".join(f"from:s{i}" for i in range(15))
        result = parse_search_query(q)
        assert len(result.operator_clauses) == 10

    def test_repeated_operator_keeps_each_occurrence(self):
        # ``from:a from:b`` is two independent clauses (ANDed downstream),
        # not a single overwritten one.
        result = parse_search_query("from:a from:b")
        assert result.operator_clauses == [
            ("from_contains", "a"),
            ("from_contains", "b"),
        ]
