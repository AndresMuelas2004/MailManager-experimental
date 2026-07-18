"""
Main FastAPI application wiring for the modular API.

Routers, error handlers, and services are registered here while keeping the
entrypoint in backend/main.py unchanged.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
import os
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional local dependency
    def load_dotenv(dotenv_path: str | Path, override: bool = False, *args, **kwargs):  # type: ignore[no-redef]
        """Fallback .env loader used only when python-dotenv is unavailable."""
        path = Path(dotenv_path)
        if not path.exists():
            return False
        loaded = False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
                loaded = True
        return loaded

from core.image_proxy import close_client
from database import (
    close_pool,
    run_startup_migrations_if_enabled,
    validate_token_encryption_config,
    warmup_connection,
)
from api.errors.handlers import register_error_handlers
from api.routers.accounts_routers import (
    account_quota_router,
    router as accounts_router,
)
from api.routers.attachments_routers import (
    admin_router as attachments_admin_router,
    email_attachments_router,
)
from api.routers.auth_routers import router as auth_router
from api.routers.contacts_routers import router as contacts_router
from api.routers.drafts_routers import router as drafts_router
from api.routers.emails_routers import (
    favorites_router,
    router as emails_router,
)
from api.routers.folders_routers import router as folders_router
from api.routers.health_routers import router as health_router
from api.routers.image_proxy_routers import (
    image_proxy_admin_router,
    image_proxy_router,
)
from api.routers.mailboxes_routers import router as mailboxes_router
from api.routers.oauth_callback_routers import router as oauth_callback_router
from api.routers.routers_helpers import rate_limit_by_ip
from api.routers.rules_routers import router as rules_router
from api.routers.virtual_mailboxes_routers import router as virtual_mailboxes_router
from api.services.backfill_worker import start_backfill_worker, stop_backfill_worker
from api.services.image_proxy_signing import signing_key_is_secure

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database connection on startup and close pool on shutdown."""
    try:
        validate_token_encryption_config()
        run_startup_migrations_if_enabled()
        warmup_connection()
    except Exception as exc:
        logger.critical("Startup failed (%s): %s", type(exc).__name__, exc)
        raise
    # Start the background backfill worker best-effort: a failure here (e.g. the
    # account_backfill_jobs table missing because DB_AUTO_MIGRATE=false) must NOT
    # abort app startup — the API must serve traffic even without the worker.
    # Deliberately distinct from the fail-fast validate/migrate/warmup above.
    try:
        start_backfill_worker()
    except Exception as exc:
        logger.warning(
            "Backfill worker failed to start (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
    yield
    try:
        stop_backfill_worker()
    except Exception as exc:
        logger.warning(
            "Backfill worker failed to stop cleanly (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
    # Close the shared image-proxy HTTP client (keep-alive pool). Best-effort:
    # the OS reclaims sockets on process exit, but closing here keeps the dev
    # --reload cycle and the tests clean. A close failure must not abort shutdown.
    try:
        close_client()
    except Exception as exc:
        logger.warning(
            "Image proxy client failed to close cleanly (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
    close_pool()


def create_app() -> FastAPI:
    """
    Build the FastAPI application with modular routers and error handlers.
    """
    app = FastAPI(title="MailApp API", lifespan=lifespan)
    allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
    # Fail-closed: a wildcard origin is invalid with credentialed CORS (browsers
    # reject '*' + credentials), so refuse to boot rather than silently break
    # every cross-origin request in production.
    if "*" in origins:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS must list explicit origins, not '*': a wildcard "
            "origin is incompatible with allow_credentials=True."
        )
    # Fail-closed on the image-proxy HMAC key, mirroring the CORS wildcard guard.
    # ``/image-proxy`` is rate-limit-exempt, so booting with the public dev
    # fallback key would turn the backend into an open image relay. Gated by an
    # opt-in flag (default off) so dev / tests keep the fallback: production
    # deployments set IMAGE_PROXY_REQUIRE_KEY=true (see .env.production.example),
    # and then a missing / dev-fallback key aborts startup instead of running
    # insecure.
    require_image_proxy_key = os.environ.get(
        "IMAGE_PROXY_REQUIRE_KEY", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if require_image_proxy_key and not signing_key_is_secure():
        raise RuntimeError(
            "IMAGE_PROXY_SIGNING_KEY must be set to a strong, persistent value "
            "(not the insecure dev fallback) when IMAGE_PROXY_REQUIRE_KEY is enabled. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # ``Content-Disposition`` is NOT on the CORS response safelist, so a
        # cross-origin ``fetch`` (frontend at :5173, backend at :8000, no Vite
        # proxy) cannot read it without this. The attachment download endpoint
        # carries the real filename/extension in that header; exposing it lets
        # the browser save the file with its correct name. ``Content-Length``
        # is already safelisted (listed only to keep the intent explicit).
        expose_headers=["Content-Disposition", "Content-Length"],
    )
    register_error_handlers(app)

    # Global per-IP safety net applied at router level (one shared Depends, so
    # it counts once per request — each request matches exactly one route).
    # Exempt: health probes and the OAuth callbacks (the provider redirects the
    # user's browser there; a 429 would break a legitimate account connection).
    global_rate_limit = [Depends(rate_limit_by_ip("global"))]

    app.include_router(health_router)  # exempt
    app.include_router(auth_router, dependencies=global_rate_limit)
    app.include_router(oauth_callback_router)  # exempt
    # Exempt like the callbacks: the browser hits /image-proxy once per image
    # (a newsletter can carry dozens), so the global bucket would trip on a
    # single email open. The HMAC signature (only URLs our sanitiser minted) +
    # the anti-SSRF guard are the real abuse gate. Because it is unthrottled,
    # a strong IMAGE_PROXY_SIGNING_KEY in production is load-bearing, not just a
    # footgun — a predictable key would turn this into an open image relay.
    app.include_router(image_proxy_router)  # exempt
    app.include_router(mailboxes_router, dependencies=global_rate_limit)
    app.include_router(accounts_router, dependencies=global_rate_limit)
    app.include_router(account_quota_router, dependencies=global_rate_limit)
    app.include_router(emails_router, dependencies=global_rate_limit)
    app.include_router(favorites_router, dependencies=global_rate_limit)
    app.include_router(virtual_mailboxes_router, dependencies=global_rate_limit)
    app.include_router(folders_router, dependencies=global_rate_limit)
    app.include_router(rules_router, dependencies=global_rate_limit)
    app.include_router(contacts_router, dependencies=global_rate_limit)
    app.include_router(drafts_router, dependencies=global_rate_limit)
    app.include_router(email_attachments_router, dependencies=global_rate_limit)
    app.include_router(attachments_admin_router, dependencies=global_rate_limit)
    app.include_router(image_proxy_admin_router, dependencies=global_rate_limit)
    return app


app = create_app()
