"""
Unit tests for ``api.routers.routers_helpers.enforce_multipart_size_limit``.

The dependency is purely synchronous and does not touch any external
state — it only inspects the ``Content-Length`` header. The tests use a
minimal stub Request with a header dict.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.errors.exceptions import RequestTooLarge
from api.routers.routers_helpers import enforce_multipart_size_limit


def _request(headers: dict[str, str]):
    return SimpleNamespace(headers=headers)


class TestEnforceMultipartSizeLimit:

    def test_no_content_length_passes(self):
        # No header -> chunked transfer encoding; the helper falls through
        # so Starlette's own runtime guard handles the upper bound.
        enforce_multipart_size_limit(_request({}))

    def test_below_30mb_passes(self):
        enforce_multipart_size_limit(_request({"content-length": str(29 * 1024 * 1024)}))

    def test_exactly_30mb_passes(self):
        # The check is strict greater-than — exactly 30 MB is allowed.
        enforce_multipart_size_limit(_request({"content-length": str(30 * 1024 * 1024)}))

    def test_above_30mb_raises_request_too_large(self):
        with pytest.raises(RequestTooLarge):
            enforce_multipart_size_limit(
                _request({"content-length": str(30 * 1024 * 1024 + 1)}),
            )

    def test_invalid_content_length_passes(self):
        # Malformed header is skipped — the dependency is best-effort.
        enforce_multipart_size_limit(_request({"content-length": "not-a-number"}))

    def test_empty_content_length_passes(self):
        enforce_multipart_size_limit(_request({"content-length": ""}))
