"""
Parity test: the backend ``BLOCKED_EXTENSIONS`` constant and the
frontend ``blocked_extensions.json`` mirror must contain *exactly* the
same entries (D-04a).

Why this test belongs at the integration boundary:

- The two artefacts live in different languages (Python tuple vs JSON
  array) and there is no shared build step. CI is the only place where
  divergence becomes visible.
- A drift here is a silent correctness bug: a file that the backend
  rejects could be uploaded by the frontend, or vice versa, and the user
  would only see the inconsistency at send time after the upload bytes
  were already wasted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.email.blocked_extensions import BLOCKED_EXTENSIONS


_FRONTEND_JSON_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "blocked_extensions.json"
)


def _load_frontend_list() -> list[str]:
    if not _FRONTEND_JSON_PATH.exists():
        pytest.fail(f"Frontend mirror not found at {_FRONTEND_JSON_PATH}")
    return json.loads(_FRONTEND_JSON_PATH.read_text(encoding="utf-8"))


class TestBlockedExtensionsParity:

    def test_set_equality(self):
        backend_set = set(BLOCKED_EXTENSIONS)
        frontend_set = set(_load_frontend_list())
        only_backend = backend_set - frontend_set
        only_frontend = frontend_set - backend_set
        assert only_backend == set(), (
            f"Extensions only in backend: {sorted(only_backend)}"
        )
        assert only_frontend == set(), (
            f"Extensions only in frontend: {sorted(only_frontend)}"
        )

    def test_same_length(self):
        # Catches duplicates that set equality alone would mask.
        assert len(BLOCKED_EXTENSIONS) == len(_load_frontend_list())

    def test_frontend_list_alphabetically_sorted(self):
        # The backend constant is already enforced sorted by its own
        # invariant test. Mirror that requirement here so the JSON file
        # cannot drift in a way that breaks deterministic diffs.
        frontend = _load_frontend_list()
        assert frontend == sorted(frontend)

    def test_frontend_list_lowercase(self):
        for ext in _load_frontend_list():
            assert ext == ext.lower(), f"{ext!r} is not lowercase"
            assert not ext.startswith("."), f"{ext!r} starts with a dot"
