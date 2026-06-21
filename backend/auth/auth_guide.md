> **Permanent rule — read before editing this file.**
>
> This file is loaded into context on every Claude session. A line here only justifies its tokens if it cannot be reconstructed by reading the code.
>
> **Before writing or keeping a line, ask: could I rebuild this by opening the relevant file(s) for ~30 seconds?**
> - **YES → delete it.** The code is the source of truth. Catalogs of what modules / functions / tests do, paraphrases of names or bodies, exhaustive kwarg / field / config enumerations, flow tables that mirror existing file or symbol names, and step-by-step recipes for code that is itself readable all fall here. Delete them on sight.
> - **NO → keep it.** Silent traps when extending the layer, cross-file asymmetries (siblings that don't behave alike), ordering / lifecycle rules whose violation breaks everything, invariants whose silent regression would slip through review, historical decisions whose rationale isn't in the code, and fixed identifiers (UUIDs, seeded data, magic constants) that cannot be recomputed — those earn their tokens.
>
> **When updating this file, re-read every section and delete anything that has since migrated into the code.** Staleness is worse than silence.

# Auth Layer Guide

> **General rules**: this layer MUST respect every rule defined in
> [`CLAUDE.md`](./CLAUDE.md).
> The current document contains project-specific details that complement those rules.

**Authority rule**: the code of this layer must respect what is documented here. If there is a discrepancy between this guide and existing code, this guide is the reference — fix the code, not the guide. When new functionality is added, update this guide at the end of the task to reflect the new reality.

## Traps

### Subclass capture order — network error must be caught **before** its base

A network/transport exception that is a **subclass** of the provider's generic error must be caught first, or a verification-endpoint outage gets misclassified as a bad token: the user sees 401 "invalid token" when the real failure is "verification service unreachable" (which must surface as 502 via `AuthTokenNetworkError`). Each provider expresses this differently:

- **Google** (`google.py`): `google.auth.exceptions.TransportError` is a subclass of `GoogleAuthError`.
- **Microsoft** (`microsoft.py`): `jwt.exceptions.PyJWKClientConnectionError` is a subclass of `PyJWKClientError` (both raised by `PyJWKClient.get_signing_key_from_jwt`). The connection subclass → `AuthTokenNetworkError` (502); the base "kid not found" → `AuthTokenInvalidError` (401).

### Microsoft: broken JWK / JWKS / crypto backend is infra → 502, not a 4th error subclass

`microsoft.py` maps `PyJWKError` / `PyJWKSetError` / `InvalidKeyError` (malformed key material, crypto backend broken) to `AuthTokenNetworkError`, **not** `AuthTokenInvalidError`: a broken signing-key fetch is "our side / the provider's infrastructure", not "the user's token is bad". This deliberately reuses the existing 502 subclass rather than inventing a provider-specific one (`CLAUDE.md` §9 — only add a subclass when a provider needs a *different* HTTP response).

### Never-double-wrap guard — now REQUIRED by `microsoft.py` (Google still does not need it)

The guard pattern in `auth/CLAUDE.md` §7 rule 6 is conditional. `google.py` still does not need it: `id_token.verify_oauth2_token(...)` cannot produce `AuthError`. `microsoft.py` **does** need it and carries it — its `try` body raises `AuthTokenInvalidError` / `AuthTokenNetworkError` directly (the `tid` pre-check, the per-clause `get_signing_key_from_jwt` mapping, the post-verification `iss`/`tid` re-bind), so without

```python
except AuthTokenError:  # re-raise before the generic catch
    raise
```

the trailing `except Exception` would re-wrap those typed errors as `AuthTokenInvalidError`, collapsing the network/invalid distinction (and turning a 502 into a 401).

### Microsoft tenancy `common` has no fixed issuer — bind `iss` to the token's own `tid`

`verify_microsoft_token` targets the multi-tenant `common` authority, whose `iss` is `https://login.microsoftonline.com/{tid}/v2.0` and therefore not a constant. The function reads `tid` from the **unverified** payload only to build the expected issuer (re-validated GUID-shape), verifies the token against that issuer, then re-checks `iss == .../{verified tid}/v2.0` on the now-verified claims (belt-and-braces). A reviewer "simplifying" this to a hardcoded issuer constant would either reject every legitimate tenant or accept tokens from a foreign one.

### Claim validation lives in the service, not in the auth layer

`verify_google_token` only verifies cryptographic validity and provider issuance. Business-logic checks (`sub` present, `email` present, etc.) belong in `auth_service.google_login`, which raises `Unauthorized`. This separation keeps the auth layer reusable across endpoints and free of API concerns.

### Microsoft vs Google — asymmetries that live in the auth-layer files

Two cross-provider asymmetries live in the auth-layer files themselves (the *service-side* claim rules — `email` → `preferred_username` fallback and `email_verified` NOT checked — are documented in `api_guide.md` § "New identity provider"):

- **`MICROSOFT_CLIENT_ID` is optional in `settings.py`** (defaults to `""`), unlike `GOOGLE_CLIENT_ID`, whose absence raises `AuthSettingsError`. The "is Microsoft configured?" guard lives at the point of use (`auth_service.microsoft_login` → `EnvVarError`), so a Google-only deploy still loads settings and boots. Do NOT move the guard into `get_auth_settings` — it would break Google-only deploys and every test that sets only `GOOGLE_CLIENT_ID`.
- **`microsoft.py` uses `leeway = 60 s`; `google.py` uses `10 s`.** Microsoft Entra does not specify a clock-skew tolerance, so the more generous window is intentional. Do not "align" the two values.

## Extension — new identity provider

See the general checklist in `auth/CLAUDE.md` §9. Project-specific additions:

- The existing `AuthTokenError` subclasses (`AuthTokenNetworkError`, `AuthTokenInvalidError`, `AuthTokenProviderError`) are provider-agnostic. Only create a new subclass when a provider introduces a failure mode that needs a different HTTP response or client-side handling.
- When adding a new provider module, extend the "Subclass capture order" trap above with that provider's specific subclass relationship (or confirm it doesn't apply).
