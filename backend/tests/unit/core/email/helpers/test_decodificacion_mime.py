"""Tests espejo de ``core.email.helpers.decodificacion_mime`` (base64url + charset UTF-8-first)."""

from __future__ import annotations

import base64

from core.email.helpers import decode_mime_body


def _b64url(raw: bytes) -> str:
    """Encode ``raw`` as Gmail-style base64url without trailing padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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

    def test_iso_2022_jp_body_with_matching_hint(self):
        """ISO-2022-JP bytes are pure ASCII (escape-sequence based), so strict
        UTF-8 would "succeed" and return the raw escape sequences as garbage —
        the declared charset must win for ASCII-masquerading encodings.
        """
        data = _b64url("こんにちは".encode("iso-2022-jp"))
        assert decode_mime_body(data, "iso-2022-jp") == "こんにちは"

    def test_utf7_body_with_matching_hint(self):
        # "10€" in UTF-7 is b"10+IKw-" — valid ASCII, hence valid strict
        # UTF-8; without the carve-out the decoder returned "10+IKw-".
        data = _b64url("10€".encode("utf-7"))
        assert decode_mime_body(data, "utf-7") == "10€"

    def test_utf8_body_mislabelled_as_iso_2022_jp_falls_back(self):
        # The declared-first carve-out only applies when the declared decode
        # succeeds; real UTF-8 bytes (≥ 0x80) fail ISO-2022-JP strict and the
        # decoder falls back to the regular UTF-8-first strategy.
        data = _b64url("Café".encode("utf-8"))
        assert decode_mime_body(data, "iso-2022-jp") == "Café"
