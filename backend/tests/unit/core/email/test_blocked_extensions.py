"""Unit tests for ``core.email.blocked_extensions``."""

from __future__ import annotations

import pytest

from core.email.blocked_extensions import BLOCKED_EXTENSIONS, is_blocked_extension


class TestBlockedExtensionsConstant:
    """Hard invariants on the canonical list shape (D-04a)."""

    def test_constant_is_non_empty(self):
        assert len(BLOCKED_EXTENSIONS) > 0

    def test_all_entries_lowercase(self):
        for ext in BLOCKED_EXTENSIONS:
            assert ext == ext.lower(), f"{ext!r} is not lowercase"

    def test_no_leading_dot(self):
        for ext in BLOCKED_EXTENSIONS:
            assert not ext.startswith("."), f"{ext!r} starts with a dot"

    def test_no_duplicates(self):
        # frozenset/sorted parity check — a duplicated entry would shrink the
        # set silently, which is the symptom users would never see.
        assert len(BLOCKED_EXTENSIONS) == len(set(BLOCKED_EXTENSIONS))

    def test_sorted_alphabetically(self):
        # Sort guarantees stable parity comparisons with the frontend JSON.
        assert list(BLOCKED_EXTENSIONS) == sorted(BLOCKED_EXTENSIONS)

    def test_must_contain_known_executable_extensions(self):
        # Spot-check entries that absolutely must be in the union.
        for must_have in ("exe", "bat", "cmd", "vbs", "js", "msi", "ps1"):
            assert must_have in BLOCKED_EXTENSIONS, f"missing: {must_have}"


class TestIsBlockedExtension:

    @pytest.mark.parametrize("filename", [
        "virus.exe",
        "script.BAT",       # uppercase: case-insensitive matching
        "macro.Vbs",        # mixed case
        "payload.js",
        "installer.msi",
    ])
    def test_returns_true_for_blocked(self, filename):
        assert is_blocked_extension(filename) is True

    @pytest.mark.parametrize("filename", [
        "report.pdf",
        "image.png",
        "archive.zip",
        "doc.docx",
        "spreadsheet.xlsx",
    ])
    def test_returns_false_for_allowed(self, filename):
        assert is_blocked_extension(filename) is False

    def test_empty_string(self):
        assert is_blocked_extension("") is False

    def test_no_dot_returns_false(self):
        # ``Makefile`` is allowed.
        assert is_blocked_extension("Makefile") is False

    def test_trailing_dot_returns_false(self):
        assert is_blocked_extension("name.") is False

    def test_uses_extension_after_last_dot(self):
        # ``foo.txt.exe`` is blocked because the **last** segment is ``exe``.
        assert is_blocked_extension("foo.txt.exe") is True
        # Conversely, ``foo.exe.txt`` is allowed.
        assert is_blocked_extension("foo.exe.txt") is False

    def test_unicode_filename_with_blocked_ext(self):
        assert is_blocked_extension("Información.exe") is True

    def test_unicode_filename_with_allowed_ext(self):
        assert is_blocked_extension("Información.pdf") is False
