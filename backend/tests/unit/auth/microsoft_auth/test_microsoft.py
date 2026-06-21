"""
Unit tests for ``verify_microsoft_token`` (pure Microsoft Entra OIDC
verification).

The function makes two PyJWT calls per invocation:

1. ``jwt.decode(token, options={"verify_signature": False})`` to read ``tid``
   WITHOUT verifying the signature, only to build the expected issuer.
2. ``jwt.decode(token, key=..., algorithms=["RS256"], audience=..., issuer=...)``
   for full verification.

plus ``_jwks_client.get_signing_key_from_jwt`` to resolve the signing key.

Both PyJWT calls and the JWKS client are monkeypatched at the module level
(``_jwks_client`` is built at import time, so the patch targets the already
instantiated object, never the class — per the implementation MD).
"""

from __future__ import annotations

import pytest
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidKeyError,
    InvalidSignatureError,
    MissingRequiredClaimError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKError,
    PyJWKSetError,
)

from auth.errors.errors import AuthTokenInvalidError, AuthTokenNetworkError
from auth.microsoft_auth import microsoft as microsoft_module

_CLIENT_ID = "ms-client-id"
_TID = "11111111-2222-4333-8444-555555555555"
_ISS = f"https://login.microsoftonline.com/{_TID}/v2.0"


def _claims(**overrides) -> dict:
    """Build a verified-claims dict with a coherent tid/iss pair."""
    claims = {
        "sub": "ms-sub-123",
        "tid": _TID,
        "iss": _ISS,
        "aud": _CLIENT_ID,
        "email": "user@contoso.com",
        "name": "MS User",
        "exp": 9999999999,
        "iat": 1000000000,
        "nbf": 1000000000,
    }
    claims.update(overrides)
    return claims


def _patch_signing_key(monkeypatch, *, exc: Exception | None = None) -> None:
    """Stub ``_jwks_client.get_signing_key_from_jwt`` (success or raising)."""

    def _get_key(_token):
        if exc is not None:
            raise exc
        return object()  # opaque signing key; jwt.decode is stubbed anyway

    monkeypatch.setattr(
        microsoft_module._jwks_client, "get_signing_key_from_jwt", _get_key
    )


def _patch_decode(
    monkeypatch,
    *,
    unverified: dict | None = None,
    verified: dict | None = None,
    verify_exc: Exception | None = None,
) -> None:
    """
    Stub ``jwt.decode`` for BOTH calls. The first call (``verify_signature``
    False) returns *unverified*; the second (full verification) returns
    *verified* or raises *verify_exc*.
    """
    unverified = unverified if unverified is not None else {"tid": _TID}
    verified = verified if verified is not None else _claims()

    def _decode(_token, **kwargs):
        options = kwargs.get("options") or {}
        if options.get("verify_signature") is False:
            return dict(unverified)
        if verify_exc is not None:
            raise verify_exc
        return dict(verified)

    monkeypatch.setattr(microsoft_module.jwt, "decode", _decode)


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------

def test_valid_token_returns_claims(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch)

    claims = microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)

    assert claims["sub"] == "ms-sub-123"
    assert claims["email"] == "user@contoso.com"
    assert claims["tid"] == _TID


# ------------------------------------------------------------------
# tid pre-check (read from the unverified payload)
# ------------------------------------------------------------------

def test_missing_tid_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, unverified={})

    with pytest.raises(AuthTokenInvalidError, match="non-GUID 'tid'"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_non_guid_tid_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, unverified={"tid": "not-a-guid"})

    with pytest.raises(AuthTokenInvalidError, match="non-GUID 'tid'"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


# ------------------------------------------------------------------
# Full-verification rejections (all map to AuthTokenInvalidError)
# ------------------------------------------------------------------

def test_invalid_signature_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, verify_exc=InvalidSignatureError("bad sig"))

    with pytest.raises(AuthTokenInvalidError, match="Microsoft token rejected"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_expired_token_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, verify_exc=ExpiredSignatureError("expired"))

    with pytest.raises(AuthTokenInvalidError, match="Microsoft token rejected"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_wrong_audience_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, verify_exc=InvalidAudienceError("bad aud"))

    with pytest.raises(AuthTokenInvalidError, match="Microsoft token rejected"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_missing_required_claim_raises_invalid(monkeypatch):
    _patch_signing_key(monkeypatch)
    _patch_decode(monkeypatch, verify_exc=MissingRequiredClaimError("nbf"))

    with pytest.raises(AuthTokenInvalidError, match="Microsoft token rejected"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_iss_tid_mismatch_after_verification_raises_invalid(monkeypatch):
    """Verified iss does not match the verified tid → belt-and-braces rejection."""
    _patch_signing_key(monkeypatch)
    # Verified claims carry a tid whose derived issuer differs from the iss claim.
    _patch_decode(
        monkeypatch,
        verified=_claims(iss="https://login.microsoftonline.com/other-tid/v2.0"),
    )

    with pytest.raises(AuthTokenInvalidError, match="verified tid does not match iss"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


# ------------------------------------------------------------------
# Signing-key resolution — network vs invalid, capture ORDER is load-bearing
# ------------------------------------------------------------------

def test_jwks_connection_error_raises_network_not_invalid(monkeypatch):
    """
    PyJWKClientConnectionError is a SUBCLASS of PyJWKClientError and MUST be
    caught first — a JWKS network blip is a 502 (network), never a 401.
    """
    _patch_signing_key(
        monkeypatch, exc=PyJWKClientConnectionError("JWKS unreachable")
    )
    _patch_decode(monkeypatch)

    with pytest.raises(AuthTokenNetworkError, match="JWKS fetch network error"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


def test_jwks_kid_not_found_raises_invalid(monkeypatch):
    """PyJWKClientError (kid not found, not the connection subclass) → 401."""
    _patch_signing_key(monkeypatch, exc=PyJWKClientError("kid not found"))
    _patch_decode(monkeypatch)

    with pytest.raises(AuthTokenInvalidError, match="signing key not found"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


@pytest.mark.parametrize(
    "exc",
    [
        PyJWKError("jwk broken"),
        PyJWKSetError("jwks set broken"),
        InvalidKeyError("crypto backend broken"),
    ],
)
def test_jwks_or_crypto_backend_error_raises_network(monkeypatch, exc):
    """Broken JWK / JWKS / crypto backend is infrastructure → 502 network."""
    _patch_signing_key(monkeypatch, exc=exc)
    _patch_decode(monkeypatch)

    with pytest.raises(AuthTokenNetworkError, match="JWKS or crypto backend error"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)


# ------------------------------------------------------------------
# Generic fallback
# ------------------------------------------------------------------

def test_unexpected_error_maps_to_invalid(monkeypatch):
    """A non-PyJWT exception escaping the body → AuthTokenInvalidError fallback."""

    def _boom(_token, **_kwargs):
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr(microsoft_module.jwt, "decode", _boom)

    with pytest.raises(AuthTokenInvalidError, match="RuntimeError"):
        microsoft_module.verify_microsoft_token("raw.jwt.token", _CLIENT_ID)
