"""
Pure Microsoft Entra (OIDC) ``id_token`` verification — no framework coupling.

Verifies a v2.0 ``id_token`` issued for tenancy ``common`` (personal +
organizational accounts). The multi-tenant ``common`` authority has no fixed
``iss``: the real issuer is ``https://login.microsoftonline.com/{tid}/v2.0``
where ``{tid}`` is the token's own ``tid`` claim, so the issuer is bound to the
verified ``tid`` instead of compared against a constant.
"""

from __future__ import annotations

import re

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    InvalidKeyError,
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKError,
    PyJWKSetError,
)

from auth.errors.errors import (
    AuthTokenError,
    AuthTokenInvalidError,
    AuthTokenNetworkError,
)


_JWKS_URI = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
# Clock-skew tolerance for ``exp`` / ``nbf`` (Microsoft fixes no value; 60 s is
# generous and safe — Google's path uses 10 s).
_LEEWAY_SECONDS = 60
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Built once at import and reused so the JWK Set cache survives across requests.
# ``get_signing_key_from_jwt`` refreshes the JWKS on an unknown ``kid``, so the
# 24 h lifespan only governs proactive refresh, not correctness on key rotation.
_jwks_client = PyJWKClient(_JWKS_URI, cache_keys=True, lifespan=86400)


def verify_microsoft_token(raw_id_token: str, microsoft_client_id: str) -> dict:
    """
    Verify a Microsoft Entra ``id_token`` v2.0 (tenancy ``common``) and return
    its decoded claims.

    Raises ``AuthTokenError`` subclasses on any verification failure:
      - ``AuthTokenNetworkError`` -> JWKS unreachable (network) or JWKS/crypto
        backend broken (infrastructure) -> surfaces as 502.
      - ``AuthTokenInvalidError`` -> token rejected (signature / aud / iss / exp
        / nbf / missing required claim / malformed / unknown kid) -> 401.
    """
    try:
        # (0) Read ``tid`` WITHOUT verifying the signature, only to build the
        #     expected issuer. This payload is untrusted; ``tid`` is re-checked
        #     in step (3) against the fully verified payload.
        unverified = jwt.decode(raw_id_token, options={"verify_signature": False})
        tid_candidate = unverified.get("tid")
        if not isinstance(tid_candidate, str) or not _GUID_RE.match(tid_candidate):
            raise AuthTokenInvalidError("Microsoft token missing or non-GUID 'tid' claim")
        expected_iss = f"https://login.microsoftonline.com/{tid_candidate}/v2.0"

        # (1) Resolve the signing key by the token's ``kid`` (handles rotation /
        #     unknown kid). Capture order is load-bearing:
        #     ``PyJWKClientConnectionError`` is a subclass of
        #     ``PyJWKClientError`` and must be caught FIRST, or a JWKS network
        #     blip would be misclassified as an invalid token (401 instead of
        #     502).
        try:
            signing_key = _jwks_client.get_signing_key_from_jwt(raw_id_token)
        except PyJWKClientConnectionError as exc:
            raise AuthTokenNetworkError(
                f"Microsoft JWKS fetch network error: {exc}"
            ) from exc
        except PyJWKClientError as exc:
            raise AuthTokenInvalidError(
                f"Microsoft signing key not found for kid: {exc}"
            ) from exc
        except (PyJWKError, PyJWKSetError, InvalidKeyError) as exc:
            raise AuthTokenNetworkError(
                f"Microsoft JWKS or crypto backend error: {exc}"
            ) from exc

        # (2) Full verification except the issuer (dynamic per tenant).
        #     ``algorithms`` is closed to RS256 — never derived from the header
        #     (prevents algorithm confusion / ``alg: none``).
        try:
            claims = jwt.decode(
                raw_id_token,
                key=signing_key,
                algorithms=["RS256"],
                audience=microsoft_client_id,
                issuer=expected_iss,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "nbf", "aud", "iss", "sub", "tid"]},
            )
        except InvalidTokenError as exc:
            raise AuthTokenInvalidError(
                f"Microsoft token rejected: {exc}"
            ) from exc

        # (3) Re-bind the issuer to the now-verified ``tid`` (belt and braces).
        if claims["iss"] != f"https://login.microsoftonline.com/{claims['tid']}/v2.0":
            raise AuthTokenInvalidError("Microsoft token: verified tid does not match iss")

        return claims

    except AuthTokenError:
        # Never double-wrap our own typed errors (auth/CLAUDE.md §7 rule 6):
        # this guard is required because the ``try`` body raises
        # ``AuthTokenInvalidError`` / ``AuthTokenNetworkError`` directly.
        raise
    except Exception as exc:
        raise AuthTokenInvalidError(
            f"Microsoft token verification failed ({type(exc).__name__}): {exc}"
        ) from exc
