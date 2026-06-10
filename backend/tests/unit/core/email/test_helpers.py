"""Unit tests for shared email client helpers."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from core.email.errors import EmailInvalidTokenDataError
from core.email.helpers import (
    decode_mime_body,
    http_error_detail,
    inline_cid_images,
    parse_expiry,
    unwrap_app_credentials,
    unwrap_user_tokens,
    wrap_account_tokens,
)


def _b64url(raw: bytes) -> str:
    """Encode ``raw`` as Gmail-style base64url without trailing padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ── http_error_detail ───────────────────────────────────────────────


class TestHttpErrorDetail:
    def test_extracts_status_and_reason(self):
        exc = MagicMock()
        exc.resp.status = "404"
        exc.reason = "Not Found"
        status, reason = http_error_detail(exc)
        assert status == "404"
        assert reason == "Not Found"

    def test_missing_attrs_returns_unknown(self):
        exc = object()
        status, reason = http_error_detail(exc)
        assert status == "unknown"
        assert reason == "unknown"


# ── parse_expiry ────────────────────────────────────────────────────


class TestParseExpiry:
    def test_none_returns_none(self):
        assert parse_expiry(None) is None

    def test_datetime_naive_returned_as_is(self):
        dt = datetime(2024, 6, 15, 10, 30, 0)
        result = parse_expiry(dt)
        assert result == dt
        assert result.tzinfo is None

    def test_datetime_aware_converted_to_utc_naive(self):
        tz_plus5 = timezone(timedelta(hours=5))
        dt = datetime(2024, 6, 15, 15, 30, 0, tzinfo=tz_plus5)
        result = parse_expiry(dt)
        assert result == datetime(2024, 6, 15, 10, 30, 0)
        assert result.tzinfo is None

    def test_timestamp_int(self):
        ts = 1718444400  # 2024-06-15T11:00:00Z
        result = parse_expiry(ts)
        expected = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        assert result == expected
        assert result.tzinfo is None

    def test_timestamp_float(self):
        ts = 1718444400.5
        result = parse_expiry(ts)
        expected = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        assert result == expected

    def test_iso_string(self):
        result = parse_expiry("2024-06-15T10:30:00")
        assert result == datetime(2024, 6, 15, 10, 30, 0)

    def test_iso_string_with_z_suffix(self):
        result = parse_expiry("2024-06-15T10:30:00Z")
        assert result == datetime(2024, 6, 15, 10, 30, 0)
        assert result.tzinfo is None

    def test_empty_string_returns_none(self):
        assert parse_expiry("") is None

    def test_invalid_string_raises_email_invalid_expiry_error(self):
        from core.email.errors import EmailInvalidExpiryError

        with pytest.raises(EmailInvalidExpiryError, match="Invalid expiry ISO string"):
            parse_expiry("not-a-date")

    def test_unsupported_type_raises_email_invalid_expiry_error(self):
        from core.email.errors import EmailInvalidExpiryError

        with pytest.raises(EmailInvalidExpiryError, match="Unsupported expiry type"):
            parse_expiry([1, 2, 3])


# ── unwrap_app_credentials ─────────────────────────────────────────


class TestUnwrapAppCredentials:
    def test_plain_dict_unchanged(self):
        creds = {"client_id": "id", "client_secret": "secret"}
        result = unwrap_app_credentials(creds)
        assert result == {"client_id": "id", "client_secret": "secret"}

    def test_secret_str_unwrapped(self):
        creds = {"client_id": "id", "client_secret": SecretStr("secret")}
        result = unwrap_app_credentials(creds)
        assert result["client_secret"] == "secret"

    def test_none_returns_empty_dict(self):
        result = unwrap_app_credentials(None)
        assert result == {}


# ── unwrap_user_tokens ──────────────────────────────────────────────


class TestUnwrapUserTokens:
    def test_unwraps_both_fields(self):
        tokens = {
            "access_token": SecretStr("at"),
            "refresh_token": SecretStr("rt"),
        }
        result = unwrap_user_tokens(tokens)
        assert result["access_token"] == "at"
        assert result["refresh_token"] == "rt"

    def test_plain_strings_unchanged(self):
        tokens = {"access_token": "at", "refresh_token": "rt"}
        result = unwrap_user_tokens(tokens)
        assert result["access_token"] == "at"
        assert result["refresh_token"] == "rt"

    def test_none_returns_empty_dict(self):
        result = unwrap_user_tokens(None)
        assert result == {}


# ── wrap_account_tokens ─────────────────────────────────────────────


class TestWrapAccountTokens:
    def test_wraps_access_and_refresh(self):
        token_data = {"access_token": "at", "refresh_token": "rt", "scopes": ["s"]}
        result = wrap_account_tokens(token_data)
        assert isinstance(result["access_token"], SecretStr)
        assert result["access_token"].get_secret_value() == "at"
        assert isinstance(result["refresh_token"], SecretStr)
        assert result["refresh_token"].get_secret_value() == "rt"
        assert result["scopes"] == ["s"]

    def test_none_refresh_not_wrapped(self):
        token_data = {"access_token": "at", "refresh_token": None}
        result = wrap_account_tokens(token_data)
        assert isinstance(result["access_token"], SecretStr)
        assert result["refresh_token"] is None

    def test_invalid_input_type_raises_invalid_token_data(self):
        with pytest.raises(EmailInvalidTokenDataError):
            wrap_account_tokens(42)

    def test_string_input_raises_invalid_token_data(self):
        with pytest.raises(EmailInvalidTokenDataError):
            wrap_account_tokens("not-a-dict")


# ── inline_cid_images ──────────────────────────────────────────────


class TestInlineCidImages:
    def test_replaces_double_quoted_src(self):
        html = '<img src="cid:logo@x">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert out == '<img src="data:image/png;base64,AAA">'

    def test_replaces_single_quoted_src(self):
        html = "<img src='cid:logo@x'>"
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out

    def test_replaces_with_angle_brackets(self):
        html = '<img src="cid:<logo@x>">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out

    def test_replaces_background_attr(self):
        html = '<td background="cid:bg@x">hi</td>'
        out = inline_cid_images(html, {"bg@x": "data:image/jpeg;base64,BBB"})
        assert 'background="data:image/jpeg;base64,BBB"' in out

    def test_leaves_unmapped_cid_alone(self):
        html = '<img src="cid:missing"><img src="cid:known">'
        out = inline_cid_images(html, {"known": "data:image/png;base64,ZZZ"})
        assert 'src="cid:missing"' in out
        assert 'src="data:image/png;base64,ZZZ"' in out

    def test_empty_html_returns_empty(self):
        assert inline_cid_images("", {"x": "data:image/png;base64,AAA"}) == ""

    def test_empty_map_returns_html_unchanged(self):
        html = '<img src="cid:x">'
        assert inline_cid_images(html, {}) == html

    def test_resolves_url_func_in_css_style(self):
        html = '<div style="background-image:url(cid:bg@x)">hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert "cid:bg@x" not in out
        assert 'url("data:image/png;base64,AAAA")' in out

    def test_url_func_soft_fallback_when_cid_unknown(self):
        html = '<div style="background-image:url(cid:unknown)">hi</div>'
        out = inline_cid_images(html, {"other": "data:image/png;base64,AAA"})
        assert "cid:unknown" in out

    def test_url_func_with_double_quotes(self):
        html = '<div style=\'background-image:url("cid:bg@x")\'>hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert 'url("data:image/png;base64,AAAA")' in out

    def test_url_func_with_angle_brackets_and_whitespace(self):
        html = '<div style="background-image: url( cid:<bg@x> )">hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert 'url("data:image/png;base64,AAAA")' in out


# ── decode_mime_body ───────────────────────────────────────────────


class TestDecodeMimeBody:
    """UTF-8-first decoder with validated fallback to the declared charset."""

    def test_utf8_body_with_utf8_hint(self):
        """Correctly declared UTF-8 body decodes as UTF-8."""
        data = _b64url("Andrés — miércoles".encode("utf-8"))
        assert decode_mime_body(data, "utf-8") == "Andrés — miércoles"

    def test_utf8_body_mislabelled_as_latin1(self):
        """Gmail mojibake bug: UTF-8 bytes wrongly declared as iso-8859-1.

        Before the fix, this produced ``AndrÃ©s``. UTF-8-first decoding makes
        the strict pass succeed and the declared charset is ignored.
        """
        data = _b64url("Andrés miércoles día".encode("utf-8"))
        assert decode_mime_body(data, "iso-8859-1") == "Andrés miércoles día"

    def test_utf8_body_mislabelled_as_windows_1252(self):
        data = _b64url("Gestión Práctica Teléfono".encode("utf-8"))
        assert decode_mime_body(data, "windows-1252") == "Gestión Práctica Teléfono"

    def test_legitimate_latin1_body_with_matching_hint(self):
        """Real Latin-1 bytes with correct charset fall back from strict UTF-8."""
        # 0xE9 is ``é`` in Latin-1, but an invalid UTF-8 continuation byte.
        data = _b64url(b"Andr\xe9s")
        assert decode_mime_body(data, "iso-8859-1") == "Andrés"

    def test_legitimate_windows_1252_body(self):
        # 0x80 is ``€`` in windows-1252 but invalid UTF-8.
        data = _b64url(b"Precio: 10\x80")
        assert decode_mime_body(data, "windows-1252") == "Precio: 10€"

    def test_ascii_only_body_any_charset(self):
        data = _b64url(b"plain ascii")
        assert decode_mime_body(data, "utf-8") == "plain ascii"
        assert decode_mime_body(data, "iso-8859-1") == "plain ascii"
        assert decode_mime_body(data, None) == "plain ascii"

    def test_no_charset_hint_uses_utf8(self):
        data = _b64url("Café".encode("utf-8"))
        assert decode_mime_body(data, None) == "Café"

    def test_unknown_charset_falls_back_to_utf8_replace(self):
        """Invalid bytes + unknown charset → replacement characters, no crash."""
        data = _b64url(b"bad\x80\xff")
        result = decode_mime_body(data, "made-up-charset")
        assert result is not None
        # The invalid bytes become U+FFFD replacement characters.
        assert "\ufffd" in result

    def test_invalid_base64_returns_none(self):
        assert decode_mime_body("!!!not base64!!!", "utf-8") is None

    def test_empty_string_returns_empty(self):
        assert decode_mime_body("", "utf-8") == ""

    def test_empty_hint_treated_as_no_hint(self):
        data = _b64url("Café".encode("utf-8"))
        assert decode_mime_body(data, "") == "Café"
        assert decode_mime_body(data, "   ") == "Café"


# ── Reply / Forward helpers (R-01..R-12) ───────────────────────────


from core.email.errors import EmailReplyContextFetchError
from core.email.helpers import (
    build_in_reply_to_and_references,
    build_mime_with_attachments,
    build_quoted_body,
    build_quoted_body_html,
    build_reply_subject,
    compute_reply_recipients,
    flatten_html_document,
    html_to_plain_text_alternative,
    _html_to_text,
    plain_text_to_html,
    validate_reply_threading_coherence,
)


# ── build_reply_subject ────────────────────────────────────────────


class TestBuildReplySubject:
    """Covers the prefix-detect-and-prepend logic shared by Reply and Forward."""

    def test_reply_no_existing_prefix(self):
        assert build_reply_subject("Hello", "reply") == "Re: Hello"

    def test_reply_canonical_prefix_preserved(self):
        # The function preserves the original prefix shape (R-05 / build_reply_subject doc).
        assert build_reply_subject("Re: Hello", "reply") == "Re: Hello"

    def test_reply_uppercase_prefix_preserved(self):
        assert build_reply_subject("RE: Hello", "reply") == "RE: Hello"

    def test_reply_german_aw_prefix(self):
        # German "Aw:" is a recognised re-prefix.
        assert build_reply_subject("Aw: Hallo", "reply") == "Aw: Hallo"

    def test_reply_swedish_sv_prefix(self):
        assert build_reply_subject("Sv: Hej", "reply") == "Sv: Hej"

    def test_reply_mixed_case(self):
        # The match is case-insensitive — no extra "Re:" is prepended.
        assert build_reply_subject("rE: Hello", "reply") == "rE: Hello"

    def test_reply_all_same_as_reply(self):
        # reply_all uses the same prefix logic as reply.
        assert build_reply_subject("Hello", "reply_all") == "Re: Hello"

    def test_forward_no_existing_prefix(self):
        assert build_reply_subject("Hello", "forward") == "Fwd: Hello"

    def test_forward_fwd_prefix_preserved(self):
        assert build_reply_subject("Fwd: Hello", "forward") == "Fwd: Hello"

    def test_forward_fw_prefix_preserved(self):
        # "Fw:" is the shorter Outlook-style variant — also recognised.
        assert build_reply_subject("Fw: Hello", "forward") == "Fw: Hello"

    def test_forward_spanish_rv_prefix(self):
        assert build_reply_subject("RV: Hola", "forward") == "RV: Hola"

    def test_forward_spanish_reenv_prefix(self):
        assert build_reply_subject("Reenv: Hola", "forward") == "Reenv: Hola"

    def test_empty_subject_reply(self):
        # Empty subject collapses to just the prefix (no trailing space).
        assert build_reply_subject("", "reply") == "Re:"

    def test_empty_subject_forward(self):
        assert build_reply_subject("", "forward") == "Fwd:"

    def test_none_subject_reply(self):
        # ``original_subject=None`` is tolerated (best-effort).
        assert build_reply_subject(None, "reply") == "Re:"

    def test_subject_with_surrounding_whitespace_trimmed(self):
        assert build_reply_subject("   Hello   ", "reply") == "Re: Hello"

    def test_unknown_action_defaults_to_reply(self):
        # Soft fallback per docstring — unknown action behaves like reply.
        assert build_reply_subject("Hello", "weird") == "Re: Hello"


# ── compute_reply_recipients ───────────────────────────────────────


class TestComputeReplyRecipients:
    """Covers To/Cc derivation for all three actions (§5.1 + R-10)."""

    def test_reply_to_overrides_from(self):
        # R-10: mailing-list pattern — Reply-To wins over From.
        to, cc = compute_reply_recipients(
            original_from="bounce@list.com",
            original_reply_to=["editor@list.com"],
            original_to=["someone@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["editor@list.com"]
        assert cc == []

    def test_reply_without_reply_to_falls_back_to_from(self):
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["someone@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert cc == []

    def test_reply_with_whitespace_only_reply_to_falls_back(self):
        # ``reply_to`` filtered for empties; falls back to ``from``.
        to, _cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=["   ", ""],
            original_to=[],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]

    def test_self_reply_from_sent_uses_original_to(self):
        # Replying to my own SENT message → To becomes original To.
        to, cc = compute_reply_recipients(
            original_from="me@me.com",
            original_reply_to=[],
            original_to=["client@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="SENT",
        )
        assert to == ["client@x.com"]
        assert cc == []

    def test_self_reply_from_sent_case_insensitive_box(self):
        # The box check is case-insensitive (upper()).
        to, _cc = compute_reply_recipients(
            original_from="me@me.com",
            original_reply_to=[],
            original_to=["client@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="sent",
        )
        assert to == ["client@x.com"]

    def test_reply_all_excludes_current_account_email_from_cc(self):
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=["dan@x.com"],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert "me@me.com" not in cc
        assert "carol@x.com" in cc
        assert "dan@x.com" in cc

    def test_reply_all_case_insensitive_dedupe(self):
        # ``Foo@x`` and ``foo@x`` collapse to a single address.
        _to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["DUP@x.com", "dup@x.com"],
            original_cc=["DUP@X.COM"],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        # Only one of the dup variants survives.
        assert sum(1 for a in cc if a.lower() == "dup@x.com") == 1

    def test_reply_all_when_current_email_unknown_does_not_filter(self):
        # If we don't know our own address we cannot filter; the user
        # ends up in CC — explicit edge case documented in the helper.
        _to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=[],
            current_account_email=None,
            action="reply_all",
            original_box="INBOX",
        )
        assert "me@me.com" in cc
        assert "carol@x.com" in cc

    def test_reply_all_excludes_primary_from_cc(self):
        # The Reply-To / From address must not appear duplicated in CC.
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["ana@x.com", "carol@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert "ana@x.com" not in cc
        assert "carol@x.com" in cc

    def test_forward_returns_empty_tuple_regardless_of_inputs(self):
        # Forward never pre-fills recipients (§5.1).
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=["editor@list.com"],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=["dan@x.com"],
            current_account_email="me@me.com",
            action="forward",
            original_box="INBOX",
        )
        assert to == []
        assert cc == []

    def test_empty_from_returns_empty_to_for_reply(self):
        # No ``From`` and no ``Reply-To`` → empty primary list, the
        # service surfaces a UX-level error.
        to, _cc = compute_reply_recipients(
            original_from="",
            original_reply_to=[],
            original_to=[],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == []


# ── build_in_reply_to_and_references ───────────────────────────────


class TestBuildInReplyToAndReferences:
    """Covers RFC 5322 header construction (§5.4)."""

    def test_id_without_angle_brackets_gets_wrapped(self):
        irt, refs = build_in_reply_to_and_references("orig@x", "")
        assert irt == "<orig@x>"
        assert refs == "<orig@x>"

    def test_id_already_wrapped_preserved(self):
        irt, refs = build_in_reply_to_and_references("<orig@x>", "")
        assert irt == "<orig@x>"
        assert refs == "<orig@x>"

    def test_existing_references_extended(self):
        # The new id appends to the existing References chain (RFC 5322 § 3.6.4).
        irt, refs = build_in_reply_to_and_references(
            "orig@x", "<oldest@x> <middle@x>",
        )
        assert irt == "<orig@x>"
        assert refs == "<oldest@x> <middle@x> <orig@x>"

    def test_empty_id_returns_empty_strings(self):
        # Defensive: malformed Message-ID should not produce <> headers
        # on the wire (which SMTP rejects).
        irt, refs = build_in_reply_to_and_references("", "<prev@x>")
        assert irt == ""
        assert refs == ""

    def test_whitespace_only_id_returns_empty_strings(self):
        irt, refs = build_in_reply_to_and_references("   ", "<prev@x>")
        assert irt == ""
        assert refs == ""


# ── _html_to_text ───────────────────────────────────────────────────


class TestHtmlToText:
    """Covers the HTML→plain-text degrader used for the reply quote."""

    def test_empty_html_returns_empty(self):
        assert _html_to_text("") == ""
        assert _html_to_text(None) == ""

    def test_basic_text_extraction(self):
        assert "hello world" in _html_to_text("<p>hello world</p>")

    def test_script_content_discarded(self):
        out = _html_to_text("<p>visible</p><script>alert('x');</script>")
        assert "alert" not in out
        assert "visible" in out

    def test_style_content_discarded(self):
        # ``<style>`` content must never leak as visible characters.
        out = _html_to_text("<p>visible</p><style>p{color:red}</style>")
        assert "color:red" not in out
        assert "visible" in out

    def test_html_entities_decoded(self):
        # ``&amp;`` is decoded to ``&`` by the degrader.
        out = _html_to_text("a&amp;b")
        assert "&" in out
        # The literal letters around the entity survive.
        assert "a" in out
        assert "b" in out

    def test_malformed_html_does_not_crash(self):
        # Stdlib HTMLParser tolerates unbalanced tags — soft fallback.
        out = _html_to_text("<p>line<broken")
        assert "line" in out

    def test_block_tags_produce_newlines(self):
        out = _html_to_text("<p>line1</p><p>line2</p>")
        assert "line1" in out
        assert "line2" in out
        # Some line break between paragraphs.
        assert "\n" in out

    def test_truncated_to_max_chars(self):
        # A long body is clipped with a marker so the composer cannot OOM.
        long_html = "<p>" + ("a" * 100_000) + "</p>"
        out = _html_to_text(long_html, max_chars=50_000)
        assert len(out) <= 50_000 + len("\n[...truncado...]") + 1
        assert "[...truncado...]" in out


# ── build_quoted_body ──────────────────────────────────────────────


class TestBuildQuotedBody:
    """Covers the quoted-body assembly for reply / reply_all / forward."""

    _RECEIVED = datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc)

    def test_reply_uses_quoted_lines_prefix(self):
        out = build_quoted_body(
            None, "Hola\nQué tal",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        # Gmail-style "> " prefix on every line of the quote.
        assert "> Hola" in out
        assert "> Qué tal" in out
        # The header reads "El <date>, <sender> escribió:".
        assert "escribió:" in out
        assert "Ana" in out and "ana@x.com" in out

    def test_reply_all_same_shape_as_reply(self):
        # The quoted-body output for reply_all matches reply for the
        # same inputs — the cc difference is in compute_reply_recipients,
        # not here.
        out = build_quoted_body(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply_all",
        )
        assert "> Body" in out
        assert "escribió:" in out

    def test_forward_uses_mensaje_reenviado_header(self):
        out = build_quoted_body(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="forward",
            to_recipients=["alice@x"], cc_recipients=["bob@x"],
            subject="Hello",
        )
        # Forward uses a block header instead of "> " quoting.
        assert "Mensaje reenviado" in out
        assert "De: Ana <ana@x.com>" in out
        assert "Asunto: Hello" in out
        assert "Para: alice@x" in out
        assert "Cc: bob@x" in out
        # The body is NOT prefixed with "> " in forward shape.
        assert "> Body" not in out

    def test_uses_text_body_when_provided(self):
        # text_body wins over html_body (already-degraded path).
        out = build_quoted_body(
            "<p>html version</p>", "plain version",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "plain version" in out
        assert "html version" not in out

    def test_degrades_html_when_no_text(self):
        # Fall back to _html_to_text when text_body is None.
        out = build_quoted_body(
            "<p>only html</p>", None,
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "only html" in out

    def test_empty_body_still_emits_header(self):
        # No body just produces the header line.
        out = build_quoted_body(
            None, "",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "escribió:" in out


# ── plain_text_to_html ─────────────────────────────────────────────


class TestPlainTextToHtml:
    """Legacy plain-text body → minimal HTML fragment (escape then nl2br)."""

    def test_escapes_ampersand_lt_gt(self):
        # & must be escaped BEFORE < / > so their entities are not re-escaped.
        assert plain_text_to_html("a & b < c > d") == "<p>a &amp; b &lt; c &gt; d</p>"

    def test_newline_becomes_break(self):
        assert plain_text_to_html("line1\nline2") == "<p>line1<br>line2</p>"

    def test_crlf_normalised_to_single_break(self):
        # \r\n must collapse to one <br>, never <br><br>.
        assert plain_text_to_html("crlf\r\nthere") == "<p>crlf<br>there</p>"

    def test_lone_cr_becomes_break(self):
        assert plain_text_to_html("cr\rthere") == "<p>cr<br>there</p>"

    def test_wraps_in_single_paragraph(self):
        assert plain_text_to_html("hello") == "<p>hello</p>"

    def test_empty_returns_empty_string(self):
        assert plain_text_to_html("") == ""

    def test_whitespace_only_returns_empty_string(self):
        assert plain_text_to_html("   ") == ""

    def test_none_returns_empty_string(self):
        assert plain_text_to_html(None) == ""


# ── flatten_html_document ──────────────────────────────────────────


class TestFlattenHtmlDocument:
    """Strip <html>/<head>/<body> wrappers + head-only subtrees (Outlook round-trip)."""

    def test_unwraps_body_fragment_from_full_document(self):
        html = (
            "<html><head><meta charset=us-ascii><title>T</title></head>"
            "<body><p>x</p></body></html>"
        )
        assert flatten_html_document(html) == "<p>x</p>"

    def test_fragment_without_wrapper_returned_intact(self):
        assert flatten_html_document("<p>already fragment</p>") == "<p>already fragment</p>"

    def test_discards_script_style_subtrees(self):
        html = "<html><body><div>d</div><script>bad()</script><style>p{}</style></body></html>"
        result = flatten_html_document(html)
        assert "<div>d</div>" in result
        assert "bad()" not in result
        assert "p{}" not in result
        assert "<script" not in result.lower()
        assert "<style" not in result.lower()

    def test_drops_title_and_meta_content(self):
        html = "<html><head><title>Subject Leak</title></head><body><p>body</p></body></html>"
        result = flatten_html_document(html)
        assert "Subject Leak" not in result
        assert "<p>body</p>" in result

    def test_empty_returns_empty_string(self):
        assert flatten_html_document("") == ""

    def test_none_returns_empty_string(self):
        assert flatten_html_document(None) == ""


# ── html_to_plain_text_alternative ─────────────────────────────────


class TestHtmlToPlainTextAlternative:
    """Derived text/plain leg of Gmail's multipart/alternative (enriched degrader)."""

    def test_link_text_differs_from_url(self):
        # <a href="url">text</a> → "text (url)".
        out = html_to_plain_text_alternative('<a href="http://x.com">link text</a>')
        assert out == "link text (http://x.com)"

    def test_link_text_equals_url(self):
        # When text == href, emit only the url (no redundant duplication).
        out = html_to_plain_text_alternative('<a href="http://x.com">http://x.com</a>')
        assert out == "http://x.com"

    def test_bullet_list_uses_dash_marker(self):
        out = html_to_plain_text_alternative("<ul><li>one</li><li>two</li></ul>")
        assert "- one" in out
        assert "- two" in out

    def test_ordered_list_uses_incrementing_counter(self):
        out = html_to_plain_text_alternative("<ol><li>first</li><li>second</li></ol>")
        assert "1. first" in out
        assert "2. second" in out

    def test_blockquote_lines_prefixed_with_gt(self):
        # Every line inside a <blockquote> gets the RFC 3676 "> " prefix.
        out = html_to_plain_text_alternative("<blockquote>line1<br>line2</blockquote>")
        assert "> line1" in out
        assert "> line2" in out

    def test_nested_blockquote_doubles_the_prefix(self):
        out = html_to_plain_text_alternative(
            "<blockquote><p>outer</p><blockquote><p>inner</p></blockquote></blockquote>"
        )
        assert "> outer" in out
        assert "> > inner" in out

    def test_collapses_runs_of_blank_lines_to_two(self):
        out = html_to_plain_text_alternative("<p>a</p>\n\n\n\n<p>b</p>")
        assert "\n\n\n" not in out

    def test_truncates_without_marker(self):
        # ~1 MB cap is SILENT — this is the body the recipient reads, not a
        # quote, so there is no "[...truncado...]" marker (unlike _html_to_text).
        big = "<p>" + ("x" * 2_000_000) + "</p>"
        out = html_to_plain_text_alternative(big)
        assert len(out) <= 1_000_000
        assert "[...truncado...]" not in out

    def test_empty_returns_empty_string(self):
        assert html_to_plain_text_alternative("") == ""

    def test_none_returns_empty_string(self):
        assert html_to_plain_text_alternative(None) == ""


# ── build_quoted_body_html ─────────────────────────────────────────


class TestBuildQuotedBodyHtml:
    """HTML reply / forward quote: attribution line + <blockquote> fragment."""

    _RECEIVED = datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc)

    def test_reply_wraps_original_in_blockquote(self):
        out = build_quoted_body_html(
            None, "Hola\nQué tal",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        # The original is degraded then re-promoted inside a <blockquote>.
        assert "<blockquote" in out
        assert "Hola<br>Qué tal" in out
        # Attribution line precedes the quote.
        assert "escribió:" in out
        assert "Ana" in out

    def test_reply_escapes_sender_email_angle_brackets(self):
        # The attribution line escapes the email so an &lt;…&gt; cannot
        # smuggle markup into the seeded HTML.
        out = build_quoted_body_html(
            None, "body",
            from_name="A&B", from_email="a@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        assert "&lt;a@x.com&gt;" in out
        assert "A&amp;B" in out

    def test_reply_all_same_shape_as_reply(self):
        out = build_quoted_body_html(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply_all",
        )
        assert "<blockquote" in out
        assert "escribió:" in out

    def test_forward_uses_mensaje_reenviado_block(self):
        out = build_quoted_body_html(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="forward",
            to_recipients=["alice@x"], cc_recipients=["bob@x"],
            subject="Hello",
        )
        assert "Mensaje reenviado" in out
        assert "De: Ana &lt;ana@x.com&gt;" in out
        assert "Asunto: Hello" in out
        assert "Para: alice@x" in out
        assert "Cc: bob@x" in out
        assert "<blockquote" in out

    def test_blockquote_style_within_outbound_allowlist(self):
        # The inline <blockquote> style must use only properties the
        # outbound sanitizer keeps (lockstep with _HTML_QUOTE_BLOCKQUOTE_STYLE).
        out = build_quoted_body_html(
            None, "body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        from api.services.outbound_html_pipeline import sanitize_outbound_html
        sanitized = sanitize_outbound_html(out)
        # The quote frame survives a re-sanitisation pass (no-op on persist).
        assert "border-left" in sanitized.lower()
        assert "<blockquote" in sanitized

    def test_uses_text_body_when_provided(self):
        # text_body wins over html_body (already-degraded path).
        out = build_quoted_body_html(
            "<p>html version</p>", "plain version",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "plain version" in out
        assert "html version" not in out

    def test_degrades_html_when_no_text(self):
        out = build_quoted_body_html(
            "<p>only html</p>", None,
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "only html" in out

    def test_empty_body_still_emits_header_without_blockquote(self):
        out = build_quoted_body_html(
            None, "",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "escribió:" in out
        # No empty <blockquote> when there is nothing to quote.
        assert "<blockquote" not in out


# ── validate_reply_threading_coherence ─────────────────────────────


class TestValidateReplyThreadingCoherence:
    """Covers the local Gmail-side guard for thread membership (§4.1, R-09 / §14.9)."""

    def _ok_kwargs(self):
        return dict(
            thread_id="thr-1",
            in_reply_to="<orig@x>",
            references="<orig@x>",
            original_message_id="orig@x",
            original_thread_id="thr-1",
            original_subject="Hello",
            new_subject="Re: Hello",
        )

    def test_happy_path_returns_none(self):
        # No exception, returns None (sentinel for "OK").
        assert validate_reply_threading_coherence(**self._ok_kwargs()) is None

    def test_thread_id_mismatch_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["thread_id"] = "thr-DIFFERENT"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "thread_id_mismatch"

    def test_message_id_not_referenced_raises(self):
        # neither in_reply_to nor references mention the original id.
        kwargs = self._ok_kwargs()
        kwargs["in_reply_to"] = "<other@x>"
        kwargs["references"] = "<other@x>"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "message_id_not_referenced"

    def test_subject_mismatch_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["new_subject"] = "Re: Completely Different Subject"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "subject_mismatch"

    def test_subject_stacked_re_prefixes_normalised(self):
        # ``Re: Re: Re: Hello`` matches ``Hello`` after normalisation —
        # the helper must not reject for cosmetic prefix stacks.
        kwargs = self._ok_kwargs()
        kwargs["new_subject"] = "Re: Re: Re: Hello"
        # Should NOT raise.
        assert validate_reply_threading_coherence(**kwargs) is None

    def test_missing_thread_ids_raises_mismatch(self):
        # Calling with empty thread ids is a programming bug, surfaced as
        # ``thread_id_mismatch``.
        kwargs = self._ok_kwargs()
        kwargs["thread_id"] = ""
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "thread_id_mismatch"

    def test_empty_original_message_id_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["original_message_id"] = ""
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "message_id_not_referenced"

    def test_message_id_matched_via_references_chain(self):
        # The id can match either ``in_reply_to`` OR somewhere in
        # ``references`` — both forms count as "referenced".
        kwargs = self._ok_kwargs()
        kwargs["in_reply_to"] = "<unrelated@x>"
        kwargs["references"] = "<oldest@x> <orig@x>"
        # Should NOT raise — references contains orig@x.
        assert validate_reply_threading_coherence(**kwargs) is None


# ── build_mime_with_attachments — extra_headers plumbing ───────────


class TestBuildMimeExtraHeaders:
    """The ``extra_headers`` kwarg is the Reply path's plumbing for
    injecting ``In-Reply-To`` and ``References`` into the Gmail MIME."""

    def test_extra_headers_injected_into_mime(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>", "References": "<orig@x>"},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in text
        assert "References: <orig@x>" in text

    def test_none_value_skipped_silently(self):
        # Soft fallback: a caller passing partial headers (one set, one
        # ``None``) does not produce a malformed empty header.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>", "References": None},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in text
        # ``References`` was None → silently dropped, no blank header.
        assert "References:" not in text

    def test_empty_string_value_skipped(self):
        # Whitespace-only values collapse to "no header" like ``None``.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "   ", "References": ""},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To:" not in text
        assert "References:" not in text

    def test_no_extra_headers_preserves_original_shape(self):
        # The default path (no extra headers) still produces a valid MIME.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Hello",
            body="body",
            attachments=[],
            extra_headers=None,
        )
        text = raw.decode("utf-8", errors="replace")
        # Reply headers absent in a non-reply MIME.
        assert "In-Reply-To" not in text
        assert "References" not in text

    def test_dict_key_uniqueness_prevents_duplicate_headers(self):
        # Single dict → at most one occurrence per header name in the MIME
        # (the implementation's main guard against duplicate header bugs).
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>"},
        )
        text = raw.decode("utf-8", errors="replace")
        assert text.count("In-Reply-To:") == 1
