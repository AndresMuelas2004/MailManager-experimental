# MailManager

MailManager is a multi-account email management platform with a FastAPI backend and a React frontend.
It lets you group Gmail and Outlook accounts under mailbox entities, connect them with OAuth 2.0, fetch inbox messages across providers, and send emails from any connected account.

## Highlights

- Multi-mailbox model to isolate contexts (work, personal, clients).
- Multi-provider support: Gmail and Outlook are implemented.
- Unified inbox per mailbox across all connected accounts.
- Send email from a specific account in a mailbox.
- Rich-text composer (bold, italic, underline, bullet/numbered lists, links) producing sanitised HTML bodies; Gmail ships a `multipart/alternative` (derived plain-text + HTML), Outlook ships `contentType: HTML`. Reply / Forward quote the original inside an HTML `<blockquote>`. Body capped at ~1,000,000 characters.
- Draft creation that mirrors the draft at the provider (Gmail and Outlook).
- Draft update that replaces draft content at the provider (PATCH, Provider-First).
- Draft deletion at the provider with local cleanup (Provider-First Rule).
- Draft synchronization pulls the most recent drafts from every connected account into the local database (capped at 500 per account). Also runs reliably server-side: a draft-sync job is enqueued on every account connection (first and reconnection) and processed by the background worker, so drafts refresh without an explicit request.
- Attachments support across received emails (cache-aside download) and outgoing drafts (lazy push, atomic Gmail send / partial-resume Outlook send).
- Reply / Reply All / Forward composer flow with provider-native threading (Gmail `threadId` + RFC 5322 headers; Outlook `createReply` / `createReplyAll` / `createForward`) and server-side attachment inheritance on Outlook forwards / explicit copy on Gmail forwards.
- Conversation view: account, unified, and virtual listings collapse each thread into one row (count of the thread's messages in that box), and opening a row fetches the full message chain from the provider with cache-aside persistence ("complete the mailbox"). Favourites is the exception — it stays per-message, not grouped.
- Remote-image privacy proxy: when an email is sanitised, every remote (`http(s)`) image URL is rewritten to a signed sentinel and served through the backend (anti-SSRF fetcher + server-side cache), so the sender never sees the reader's IP and the cached HTML never stores a raw remote URL. Images are fetched lazily — only when the email is actually opened, never during background prefetch.
- Manual "Refresh" button per listing (unified, account, virtual) that re-runs the provider sync on demand, with an inline "last updated: X ago" indicator. The last-synced mark is browser-local (localStorage) — it reflects when this browser last synced, not server state.
- Batch read/unread status management across accounts.
- Unread counters: per-account and mailbox-wide unread badges (sidebar, account tabs, connected-account cards) plus an unread count in the browser tab title, for the Inbox (ALL_MAIL) and Spam boxes. Counts individual messages from the locally synced copy.
- Trash management: move emails to trash, permanently delete, or restore.
- Spam operations: move to spam and restore from spam with cross-provider support.
- Archive: move emails out of the inbox to an "Archived" view and back, with cross-provider support (Gmail removes the `INBOX` label keeping the same id; Outlook moves to the `archive` folder, rewriting the id). The main inbox (`ALL_MAIL`) no longer surfaces archived mail; a dedicated `box=ARCHIVE` view lists it.
- Favourites: per-email star/flag toggle (Provider-First) and a dedicated favourites listing. The star/flag state is captured automatically on every sync — including out-of-band changes that arrive through the incremental path — so the manual "sync favourites" button was removed; the reconciliation endpoint survives backend-only.
- Email search with Gmail-style operators (`from:`, `to:`, `subject:`, `has:attachment`, `before:`/`after:`, `is:`, `in:`) on top of free-text substring matching, all over the locally synced metadata.
- Sort + quick-filter controls on the inbox listing: order by date / sender / subject (ascending or descending) and narrow with one-click chips (unread, with attachments, starred). Applies to the unified and per-account inbox views (not favourites or virtual mailboxes); all SQL over the locally synced metadata, no provider call.
- Virtual mailboxes ("bandejas ficticias"): saved filtered views over the stored metadata of a chosen set of accounts.
- Primary recipient ("Para"): the first `To` recipient (`to_email` / `to_name`) is stored and shown in the listing.
- Recipient autocomplete in the composer: suggests known addresses (from synced received senders + sent recipients across all the user's accounts) as you type, built entirely from local metadata — no provider/address-book call.
- Per-account email signature (rich-text HTML): set one signature per connected account in Settings; it is auto-inserted when composing a new email, replying, or forwarding (never when reopening an existing draft). Stored locally only — no provider signature import — and inlined into the body, so it ships through the same outbound sanitiser as the rest of the message.
- In-app Settings area (identity, connected accounts, mailbox rename/delete, account-label editing, "sync everything", account deletion) and an interface-language switch (Spanish / English). The language is a browser-local preference (localStorage) — it is not persisted server-side.
- Responsive layout for phone-sized viewports: the side navigation collapses behind a hamburger drawer, a floating button opens the composer, viewers and the composer go full-screen, and the email listing renders as cards instead of table columns. Pure client-side presentation — same endpoints and page size as desktop.
- Login with Google or Microsoft (both OIDC `id_token` verification → server-side session cookie). Each provider creates its own user; there is no account-linking. Microsoft login is optional per deploy (disabled button when `MICROSOFT_CLIENT_ID` / `VITE_MICROSOFT_CLIENT_ID` are unset).
- Dev-login backdoor for local development (localhost-only, opt-in via env var), with optional DEV auto-login that skips the login screen entirely (`VITE_DEV_AUTO_LOGIN`).
- Containerised local stack with Podman Compose (PostgreSQL + backend + frontend).
- OAuth 2.0 interactive connect flow (browser popup + API-side redirect callback, container-friendly) plus silent re-authentication and a manual "Reconnect account" action that re-runs consent to recover a revoked/expired account without deleting its synced mail.
- Per-user connected-account limit (default 15, configurable): the "Add account" button disables at the cap, and `GET /accounts/quota` reports `{ connected, limit }`. Attempting to exceed it returns 409 `account_limit_exceeded`.
- Background initial bulk-load ("backfill"): the first time an account is connected, its history (up to 100,000 messages, configurable) downloads in the background — in parallel across all the user's connected accounts — in resumable, rate-paced waves, so emails appear progressively in the listings — replacing the old synchronous 500-message bootstrap. Runs in an in-process worker (single uvicorn worker, no extra infrastructure) driven by a checkpoint table (a failed job auto-retries a bounded number of times before staying failed); progress is polled via `GET /mailboxes/{id}/backfill-status`. Disable the worker (`BACKFILL_WORKER_ENABLED=false`) and a newly connected account falls back to the classic synchronous 500-message bootstrap.
- PostgreSQL persistence for mailboxes, accounts, and tokens.
- Strict layered architecture with centralized API error mapping.

## Architecture

Request flow:

```text
Routers (api/routers)
  -> Routers helpers (api/routers/routers_helpers.py)
  -> Services (api/services)
    -> Auth (auth/)
    -> Database (database/)
    -> Core (core/email)
      -> EmailManager
        -> GmailClient / OutlookClient
```

Layer contracts:

- `Routers`: HTTP interface only. No business logic.
- `Services`: orchestration, validation, and error translation.
- `Auth`: framework-agnostic authentication (Google / Microsoft OIDC, session management).
- `Database`: PostgreSQL persistence and token storage (independent layer).
- `Core`: provider-specific email behavior and client orchestration.

## Repository Structure

```text
MailManager/
|-- backend/
|   |-- api/
|   |   |-- routers/
|   |   |-- services/
|   |   |-- schemas/
|   |   `-- errors/
|   |-- auth/
|   |-- database/
|   |-- core/
|   |   `-- email/
|   |-- tests/
|   |   |-- unit/
|   |   |-- integration/
|   |   |-- e2e/
|   |   `-- shared/
|   |-- Dockerfile
|   `-- main.py
|-- frontend/
|   |-- src/
|   |-- Dockerfile
|   `-- package.json
|-- compose.yml
|-- requirements.txt
`-- README.md
```

## Prerequisites

**Containerised stack (recommended):**

- Podman + `podman compose` (or Docker + `docker compose`).
- Gmail OAuth app credentials JSON (Google Cloud).
- Outlook app credentials JSON (Azure app registration).

**Running without containers (for the test suites / host development):**

- Python 3.12+
- Node.js 18+
- PostgreSQL 16 (matches the image the stack runs).

## Getting Started

### Containerised stack (recommended)

The whole stack — PostgreSQL, backend, and frontend — runs with one command via `compose.yml`.

1. Copy the backend env template and fill in the secrets (Fernet key, Google client ID, credential paths):

   ```powershell
   Copy-Item backend\.env.docker.example backend\.env.docker
   ```

   Generate a Fernet key (one-time):

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Bring the stack up:

   ```powershell
   podman compose up --build
   ```

   - Migrations run automatically on backend startup (`DB_AUTO_MIGRATE=true` in `.env.docker`) — there is no separate Alembic step.
   - The backend is served by `uvicorn` (not `python main.py`).

Service URLs: backend `http://localhost:8000`, frontend `http://localhost:5173`, PostgreSQL `localhost:5432`.

### Running without containers (for the test suites)

The automated suites run from a host Python environment against a reachable PostgreSQL (the containerised one works — `postgresql://mailmanager:mailmanager@localhost:5432/mailmanager`).

1. Create a virtualenv and install backend dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Configure environment variables. The backend reads OS env vars and, as a fallback, `backend/.env` via `python-dotenv` (`override=False`, so OS-level variables take precedence). See `backend/.env.example`. The minimum for unit/integration tests is a reachable `DATABASE_URL`; provider credentials and `GOOGLE_CLIENT_ID` are only needed for E2E.

3. Apply migrations against that database (only needed outside the container, which auto-migrates):

   ```powershell
   python -m alembic -c backend/database/alembic.ini upgrade head
   ```

   For databases initialized before Alembic:

   ```powershell
   python -m alembic -c backend/database/alembic.ini stamp 0001_initial_schema
   python -m alembic -c backend/database/alembic.ini upgrade head
   ```

4. Optionally run the backend / frontend on the host (the container already serves both):

   ```powershell
   uvicorn api.app:app --app-dir backend --reload
   cd frontend; npm install; npm run dev
   ```

### Production stack

Production runs a separate, self-contained `compose.prod.yml` (nginx-built frontend + backend without `--reload` + Postgres with no published port + a Caddy reverse proxy that terminates TLS and routes `/api/*` to the backend). It is **not** an overlay of `compose.yml` — see `repository_guide.md`. The backend and frontend containers run as non-root users (least privilege); every external image (python, node, nginx-unprivileged, postgres, caddy) is pinned by exact tag + digest for reproducible builds.

1. Copy the template, fill in real values, and lock it down (on the VPS: `chmod 600 .env.production`):

   ```powershell
   Copy-Item .env.production.example .env.production
   ```

   Generate a **fresh** Fernet key for production (never reuse the dev one):

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Put ONLY the two credential JSON files in the directory referenced by `SECRETS_DIR`, and make them readable by the non-root backend container — `chmod 644` the two files, and make the directory traversable by the non-root backend — `chmod 711` the directory (a `700` directory blocks UID 10001 from reaching the files even when they are `644`). The backend runs as an unprivileged user (UID 10001), unlike a root container which would read them regardless of mode/owner. Skipping this does **not** stop the stack from booting: `/health` (verified in step 4) still returns 200 because the credentials are read lazily; the failure surfaces only on the first account connect / sync as `CredentialReadError` / `AppCredentialsLoadError` with `path=/secrets/…`.

3. Register the production OAuth redirect URIs: Google Cloud → `https://DOMAIN/api/auth/google/callback`; Azure (and `outlook_credentials.json`) → `https://DOMAIN/api/auth/outlook/callback`.

4. Bring the stack up and verify:

   ```powershell
   docker compose --env-file .env.production -f compose.prod.yml up -d --build
   ```

   Then `curl https://DOMAIN/api/health` should return `{"status":"ok"}` (Caddy strips `/api`; `/health` alone hits the SPA, not the backend).

> `VITE_API_BASE_URL` / `VITE_GOOGLE_CLIENT_ID` are baked into the frontend bundle at build time, so changing the domain requires rebuilding the image (`--build`).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL DSN used by connection pool and Alembic migrations. |
| `DB_POOL_MIN_CONN` | No | Minimum pooled DB connections. Default: `1`. |
| `DB_POOL_MAX_CONN` | No | Maximum pooled DB connections. Raised to cover the parallel background backfill — keep it `>= BACKFILL_DB_WRITE_CONCURRENCY` plus headroom for user requests. Default: `25`. |
| `DB_CONNECT_TIMEOUT_SECONDS` | No | Connection timeout for PostgreSQL. Default: `10`. |
| `DB_APPLICATION_NAME` | No | PostgreSQL `application_name`. Default: `mailmanager-api`. |
| `DB_AUTO_MIGRATE` | No | If `true`, API startup runs `alembic upgrade head`. Default: `false`. |
| `DB_SEED_TEST_DATA` | No | If `true` (default), migrations seed a phantom test user + ~100 sample emails (migration 0010). Set to `false` in production to boot against a clean DB; the schema is still built either way. Invalid value → fail-fast. Default: `true`. |
| `DB_ALEMBIC_INI_PATH` | No | Custom Alembic config path. |
| `TOKEN_ENCRYPTION_KEY` | Yes* | Fernet key for encrypting account tokens at rest. *Required unless `TOKEN_PLAINTEXT_FALLBACK_ENABLED=true`; with the fallback disabled (default) and no key, startup fails fast. |
| `TOKEN_ENCRYPTION_KEY_ID` | No | Identifier for active encryption key. Default: `v1`. |
| `TOKEN_PLAINTEXT_FALLBACK_ENABLED` | No | Allows legacy plaintext token reads/writes when no `TOKEN_ENCRYPTION_KEY` is set. When `false` (default), a keyless deploy fails to boot instead of storing plaintext. Enable only for dev/test. Default: `false`. |
| `MIA_GMAIL_CREDENTIALS_PATH` | Yes | Path to Gmail OAuth credentials JSON file. |
| `MIA_OUTLOOK_CREDENTIALS_PATH` | Yes | Path to Outlook app credentials JSON file. |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID for OIDC authentication. |
| `MICROSOFT_CLIENT_ID` | No | Azure App Registration client ID for Microsoft (Entra) login. The backend verifies the `id_token` only (no redirect URI / token exchange). Leave empty to disable Microsoft login server-side — `POST /auth/microsoft` then returns 500 `env_var_error`. Must match the frontend's `VITE_MICROSOFT_CLIENT_ID`. |
| `GOOGLE_OAUTH_REDIRECT_URI` | No | Redirect URI for the interactive Gmail connect flow. Default: `http://localhost:8000/auth/google/callback` (Google "Desktop app" clients accept any localhost redirect without registration). In production set it to `https://DOMAIN/api/auth/google/callback` and register it in Google Cloud. |
| `GMAIL_BATCH_MAX_WORKERS` | No | Max parallel workers for Gmail batch operations. Default: `5`. |
| `BACKFILL_WORKER_ENABLED` | No | Kill-switch for the background initial-bulk-load (backfill) worker. Truthy (default) starts the in-process worker thread AND enqueues a backfill on each first account connection (in lockstep); falsy disables both, so a newly connected account falls back to the synchronous 500-message bootstrap on its first sync. Default: `true`. |
| `BACKFILL_MAX_EMAILS_PER_ACCOUNT` | No | Per-account cap on the background backfill. Default: `100000`. |
| `BACKFILL_MAX_CONCURRENT` | No | Accounts backfilled in parallel by the worker pool. Default: `15`. |
| `BACKFILL_DB_WRITE_CONCURRENCY` | No | Max concurrent backfill / draft-sync DB writes (a semaphore bounding pool usage independently of `BACKFILL_MAX_CONCURRENT`). Keep `DB_POOL_MAX_CONN >=` this + headroom for user requests. Default: `8`. |
| `BACKFILL_MAX_ATTEMPTS` | No | Auto-retry budget for a failed backfill / draft-sync job before it stays permanently failed. Default: `5`. |
| `BACKFILL_GMAIL_GETS_PER_MINUTE` | No | Target Gmail `messages.get` rate during backfill (fixed conservative pacing, not adaptive). Default: `300`. |
| `BACKFILL_OUTLOOK_PAGE_DELAY_MS` | No | Delay between Outlook backfill pages. Default: `300`. |
| `BACKFILL_POLL_INTERVAL_S` | No | Dispatcher poll interval (seconds) for claiming pending backfill / draft-sync jobs. Default: `5`. |
| `MAX_ACCOUNTS_PER_USER` | No | Per-user connected-account limit enforced by `create_account` (409 `account_limit_exceeded` when exceeded) and reported by `GET /accounts/quota`. Counts all owned accounts, expired tokens included. Default: `15`. |
| `AUTH_SESSION_LIFETIME_DAYS` | No | Session duration in days. Default: `7`. |
| `AUTH_COOKIE_SECURE` | No | HTTPS-only session cookies. Default: `false`. |
| `AUTH_COOKIE_SAMESITE` | No | Session cookie `SameSite` policy: `lax` / `strict` / `none`. `none` requires `AUTH_COOKIE_SECURE=true`. Default: `lax`. |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated CORS origins. Default: `http://localhost:5173`. A `*` wildcard is rejected at startup (incompatible with credentialed CORS). |
| `RATE_LIMIT_ENABLED` | No | Opt-in per-client request throttling (login + send + provider syncs, plus a global per-IP safety net). Enabled only when truthy (`1`/`true`/`yes`/`on`); unset/falsy (default) disables it entirely. Enable it in production. State is in-process memory (single worker), lost on restart. Returns 429 `rate_limit_exceeded` with a `Retry-After` header when a bucket is exceeded. |
| `IMAGE_PROXY_SIGNING_KEY` | No* | HMAC key for the signed sentinel URLs the inbound sanitiser bakes into cached email HTML (the remote-image privacy proxy). Dev/test fall back to a fixed insecure key. *Production MUST set a strong, persistent value: `GET /image-proxy` is rate-limit-exempt, so a predictable key would turn the backend into an open image relay, and a key change invalidates every already-cached signed URL (broken images until content is re-sanitised on a cache miss; frequently-opened emails are never purged, so rotating the key means you must also `TRUNCATE email_content`). |
| `IMAGE_PROXY_REQUIRE_KEY` | No | Opt-in startup guard for the key above. When truthy (`1`/`true`/`yes`/`on`), `create_app()` refuses to boot unless `IMAGE_PROXY_SIGNING_KEY` is set to a strong value (not the dev fallback) — mirrors the CORS wildcard guard. Unset/falsy (default) keeps the dev fallback so dev/tests are unaffected. Set it to `true` in production. |
| `LOG_LEVEL` | No | Root/uvicorn log level applied by the production entrypoint (`python main.py`): `DEBUG` / `INFO` / `WARNING` / `ERROR`. Default: `INFO`. The dev server keeps uvicorn's own defaults. |
| `ATTACHMENTS_PURGE_TOKEN` | No | Bearer token for the `POST /admin/attachments/purge` maintenance endpoint. When unset, the endpoint replies 503 `purge_disabled` instead of 401 (deploy is intentionally not configured for this operation). |
| `IMAGE_PROXY_PURGE_TOKEN` | No | Bearer token for the `POST /admin/image-proxy/purge` maintenance endpoint (dedicated, not shared with `ATTACHMENTS_PURGE_TOKEN`). When unset, the endpoint replies 503 `purge_disabled` instead of 401. |
| `DEV_LOGIN_ENABLED` | No | When truthy, enables the `POST /auth/dev-login` backdoor. Unset/falsy → the endpoint replies 503 `dev_login_disabled`. Never enable in production. |
| `DEV_LOGIN_EMAIL` | No | Email of the existing user the dev-login mints a session for. Required when `DEV_LOGIN_ENABLED` is truthy (500 `env_var_error` if missing). |
| `DEV_LOGIN_TRUSTED_HOSTS` | No | Comma-separated client hosts allowed to call dev-login. Default: `127.0.0.1`, `::1`, `localhost`. A request from any other host → 403 `dev_login_not_localhost`. |

### Frontend environment variables

The following variables are consumed only by the Vite dev server / frontend bundle. They are **not** read by the backend; configure them in `frontend/.env`.

| Variable | Required | Description |
|---|---|---|
| `VITE_API_BASE_URL` | No | Frontend override for the backend URL. Defaults to `http://localhost:8000`. **Build-time** (baked into the bundle); in production set it to `https://DOMAIN/api` and rebuild the image. |
| `VITE_MICROSOFT_CLIENT_ID` | No | Azure App Registration (SPA) client ID for the "Continue with Microsoft" button. **Build-time** (baked into the bundle). Empty → the button renders disabled. Must match the backend's `MICROSOFT_CLIENT_ID`. |
| `VITE_DEV_AUTO_LOGIN` | No | When `"true"`, the dev server auto-logs-in through the backend dev-login backdoor on boot, skipping the login screen. Set in `frontend/.env.development` (not in compose env — see `repository_guide.md`). Requires `DEV_LOGIN_ENABLED` + `DEV_LOGIN_EMAIL` in the backend. Gated by `import.meta.env.DEV`, so production builds ignore it. |

Outlook credential file keys: `client_id`, `client_secret`, `tenant`, `redirect_uri`, `scopes`.

### Deployment variables (production, interpolated by compose)

Set these in `.env.production` (template: `.env.production.example`). They are consumed by `compose.prod.yml` / Caddy at deploy time, not by the backend runtime:

| Variable | Description |
|---|---|
| `DOMAIN` | Public domain Caddy serves and obtains a TLS certificate for. |
| `ACME_EMAIL` | Email used for Let's Encrypt registration. |
| `SECRETS_DIR` | Host directory holding ONLY the 2 credential JSONs, mounted read-only at `/secrets`. |
| `POSTGRES_PASSWORD` | Strong DB password; must match the one embedded in `DATABASE_URL`. |

## API Summary

Health:

- `GET /health`

Mailboxes:

- `POST /mailboxes`
- `GET /mailboxes`
- `GET /mailboxes/{mailbox_id}`
- `PATCH /mailboxes/{mailbox_id}` — Rename a mailbox (`{ "display_name": ... }`, 1–120 chars, no uniqueness). Ownership-checked like `DELETE`; a row deleted between the check and the update collapses to 404.
- `DELETE /mailboxes/{mailbox_id}`
- `GET /mailboxes/{mailbox_id}/backfill-status` — Report the background initial bulk-load (backfill) progress for each account of the mailbox: `{ accounts: [{ account_id, status, fetched_count, target_total, done }], active }` where `status ∈ pending|running|completed|failed` and `done = status ∈ {completed, failed}`. Accounts with no backfill job are omitted. Local-only (no provider call), intended for frequent polling of the live "loading…" counter.

Accounts:

- `GET /mailboxes/{mailbox_id}/accounts`
- `POST /mailboxes/{mailbox_id}/accounts` — Create an account record (the only INSERT into `accounts`). Returns 409 `account_limit_exceeded` when the user is already at `MAX_ACCOUNTS_PER_USER`.
- `GET /accounts/quota` — User-level (no mailbox prefix): the user's connected-account usage `{ connected, limit }` aggregated across every mailbox they own (expired-token accounts included). Local-only, no provider call.
- `GET /mailboxes/{mailbox_id}/accounts/{account_id}`
- `PATCH /mailboxes/{mailbox_id}/accounts/{account_id}` — Update `display_label`, `config`, and/or `signature_html` (per-account email signature, HTML, ≤10,000 chars; sanitised on save; `""` clears it, an omitted field is left untouched). `AccountOut` returns `signature_html`.
- `DELETE /mailboxes/{mailbox_id}/accounts/{account_id}`
- `POST /mailboxes/{mailbox_id}/accounts/{account_id}/connect` — starts the interactive OAuth flow; returns `{authorization_url, state}` for the browser popup.

OAuth connect callbacks (no session — validated by the single-use `state` token):

- `GET /auth/google/callback`
- `GET /auth/outlook/callback`

Emails:

- `POST /mailboxes/{mailbox_id}/emails/sync-metadata`
- `POST /mailboxes/{mailbox_id}/emails/send`
- `PATCH /mailboxes/{mailbox_id}/emails/read-status`
- `POST /mailboxes/{mailbox_id}/emails/trash`
- `POST /mailboxes/{mailbox_id}/emails/move-to-trash`
- `POST /mailboxes/{mailbox_id}/emails/spam`
- `POST /mailboxes/{mailbox_id}/emails/restore-from-spam`
- `POST /mailboxes/{mailbox_id}/emails/archive` — Move emails out of the inbox into the `ARCHIVE` box across accounts (Provider-First). Body `{ "items": [{ account_id, provider_message_id }] }`; response `{ moved_count, accounts: [{ account_id, moved }] }`.
- `POST /mailboxes/{mailbox_id}/emails/restore-from-archive` — Unarchive emails back to the inbox (`ALL_MAIL`). Same request/response shape as `/archive`.
- `GET /mailboxes/{mailbox_id}/emails` — Required query param: `box=ALL_MAIL|SENT|SPAM|TRASH|ARCHIVE`. Optional: `account_id`, `q` (search, 2-200 chars: accent/case-insensitive free-text substring across subject + sender, plus Gmail-style operators combined with AND — `from:` `to:` `subject:` `has:attachment` `before:`/`after:` `is:read|unread|favorite` `in:inbox|sent|spam|trash|archive` — see `docs/features/lupa.md`), `favorite` (when `true`, returns only favourited emails — `box=ALL_MAIL` is the anchor that excludes TRASH/SPAM unless one is requested explicitly), `group_by_thread` (when `true`, collapse each conversation into one row: aggregated `is_read`/`has_attachments`/`is_favorite`, a `thread_message_count`, and `total` counts threads instead of messages), `sort` (`date`|`sender`|`subject`, default `date`) + `sort_dir` (`asc`|`desc`, default `desc`), the quick-filter chips `unread` / `has_attachment` / `favorite_only` (combinable AND filters; `favorite_only` is the plain "starred on this box" chip, distinct from the `favorite` anchor), `limit` (default 50, max 500), `offset` (default 0). Sort + chips apply to this regular listing only — the virtual-mailbox listing and the favourites view do not take them. Returns a paginated envelope `{ items, total, limit, offset }` where `total` is the exact size of the whole filtered set in the local copy (not the page, not the provider's live mailbox). Each row in `items` carries `has_attachments` (B.lazy: starts `false`, flips to `true` on first `get_email_content`), `is_favorite`, `thread_message_count` (1 unless grouped), and the primary recipient `to_email` / `to_name` (the first `To` recipient only).
- `GET /mailboxes/{mailbox_id}/emails/unread-count` — Optional query param `box=ALL_MAIL|SPAM` (default `ALL_MAIL`; any other value → 422). Returns `{ mailbox_id, box, total, accounts: [{ account_id, unread }] }`: the mailbox-wide unread (`is_read=false`) total plus the per-account breakdown (every account, 0 included). Counts individual messages, not conversations. Local-only (no provider call) — reads only the synced copy. Feeds the unread badges in the sidebar, the per-account tabs and the connected-accounts cards, plus the browser tab title.
- `GET /mailboxes/{mailbox_id}/emails/{provider_message_id}/content` — Required query param: `account_id`. Cache-aside: the rendered HTML body is served from the local `email_content` cache or fetched from the provider (body + attachments in one read) on miss. The cached HTML has every remote image URL rewritten to a signed image-proxy sentinel (privacy — see `GET /image-proxy` below). The cache is space-bounded by a 7-day sliding TTL (every open resets the clock; bodies idle past the window are evicted during sync — shorter than the 30-day attachment-blob TTL, the two no longer match), and each `sync-metadata` additionally pre-caches the body of recent unread inbox mail in the background so opening it is instant. Response includes `attachments[]` (the strict D-13 split between inline images embedded in the body and downloadable parts). See `docs/features/visualizacion-de-correos.md`.
- `GET /image-proxy` — Serves a remote email image through the backend for privacy. **No session cookie** — the viewer iframe is sandboxed (origin "null") and the image is a cross-site subresource, so access is gated by the HMAC signature the inbound sanitiser mints (only image URLs baked into a sanitised body work). Query params `u` (base64url of the original URL) and `s` (its signature). Cache-aside over the global `image_proxy_cache` table with an anti-SSRF fetcher (rejects private/loopback/metadata targets, non-image content types, and oversized bodies); response carries `X-Content-Type-Options: nosniff` and an aggressive `Cache-Control` (the signed URL is stable → `immutable`). **Exempt from the rate limit** (the browser hits it once per image), so a strong, persistent `IMAGE_PROXY_SIGNING_KEY` is required in production.
- `GET /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/attachments/{attachment_id}` — Streams a single attachment binary with `Content-Disposition: attachment` (forced download, never inline). Cache-aside: served from local cache or fetched from the provider on miss.
- `GET /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/reply-context` — Required query param: `action=reply|reply_all|forward`. Read-only — returns the prefilled `to_recipients` / `cc_recipients` / `subject` / `body` (HTML: attribution line + the original quoted inside a `<blockquote>`) the composer needs, plus the RFC 5322 threading strings (`in_reply_to`, `references`, `thread_id`). Gmail-bound `reply` / `reply_all` runs the triple-requirement coherence guard locally before returning (502 `email_reply_context_error` on mismatch).
- `GET /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/conversation` — Returns the full message chain (`ConversationOut`: `thread_id` + `messages[]` of `EmailMetadataOut`, chronological ascending) of the conversation the opened message belongs to. Read + lazy sync (not Provider-First): it reads the thread from the provider — including Sent / Spam / Trash members — and best-effort persists newly seen messages into the local copy. Bodies are NOT included (fetched per message via the content endpoint); each viewer message reports `has_attachments=false` (B.lazy). A message with no thread short-circuits to a single-message conversation with no provider call. `conversation_fetch_error` (502) on provider failure.

Favourites:

- `PATCH /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/favorite` — Toggle the favourite flag (Provider-First: Gmail `STARRED`, Outlook `flag`). Body `{ "favorite": true|false }`. A local existence pre-check returns 404 `email_not_found` before any provider round trip; a zero-row update after a successful provider call (race) also collapses to 404.
- `POST /mailboxes/{mailbox_id}/favorites/sync` — Reconcile `is_favorite` from provider truth for one account (`account_id` query param) or every account in the mailbox. `total_synced` is the rowcount across the touched accounts; each `accounts[i].favorites_synced` is the count of favourites the provider reported. Now largely redundant — favourites are captured automatically on every sync — and no longer surfaced in the UI; kept as a manual reconciliation.

Virtual mailboxes (saved filtered views — "bandejas ficticias"):

- `GET /virtual-mailboxes` — List the caller's virtual mailboxes.
- `POST /virtual-mailboxes` — Create one from `display_name`, `account_ids` (a snapshot of the accounts it spans), and a `filter_payload`.
- `GET /virtual-mailboxes/{virtual_mailbox_id}` — Fetch one (404 `virtual_mailbox_not_found` for a foreign / missing id).
- `PATCH /virtual-mailboxes/{virtual_mailbox_id}` — Replace `display_name` / `account_ids` / `filter_payload`.
- `DELETE /virtual-mailboxes/{virtual_mailbox_id}` — Delete one.
- `GET /virtual-mailboxes/{virtual_mailbox_id}/emails` — List the emails matching the saved filter across the (still-owned) accounts in the snapshot. Reads only the local synced copy (no provider call — the open-time sync is orchestrated client-side by fanning out per-account `sync-metadata`). Same paginated `{ items, total, limit, offset }` envelope as `GET /emails` (`limit` default 50, max 500). Always grouped by conversation (one row per thread); `total` is the deduplicated thread count across accounts that share a provider message. Hard-deleted (`DELETED`) messages are never returned, even when no box filter is set.

Drafts:

- `POST /mailboxes/{mailbox_id}/accounts/{account_id}/drafts` — Create a draft at the provider and persist it locally (Provider-First; Outlook uses `Prefer: IdType="ImmutableId"`).
- `PATCH /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}` — Replace an existing draft's content at the provider (full-field replacement) and persist the new values locally. Provider-First with a pre-check: the draft must exist in the local DB (404 `draft_not_found` otherwise) before any provider call. Gmail uses `users().drafts().update()`; Outlook uses `PATCH /me/messages/{id}` with `Prefer: IdType="ImmutableId"` repeated on every call. `created_at` is preserved; `updated_at` is refreshed.
- `DELETE /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{draft_id}` — Delete a draft at the provider and remove the local row (Provider-First). Returns `{"status": "deleted"}`.
- `POST /mailboxes/{mailbox_id}/drafts/sync` — Fetch the most recent drafts from the provider(s) into the local database (full replace per account, capped at 500 drafts per account most recent by date). Optional query param `account_id`: when provided, syncs only that account; when omitted, syncs every account in the mailbox. Gmail uses parallel batched `drafts.get` calls (workers configurable via `GMAIL_BATCH_MAX_WORKERS`, default 5); Outlook uses `$top=500` + `$orderby=lastModifiedDateTime desc` paginated fetch with per-page retries. The same sync also runs server-side automatically — enqueued on every account connection and processed by the background worker — so this HTTP endpoint is the on-demand / fallback trigger.
- `POST /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}/send` — Send an existing draft at the provider and remove it from local storage. Provider-First with 3-attempt retry in the client layer. On success: deletes the local `drafts` row and persists the sent email metadata to `email_metadata` (both best-effort). Gmail returns a new `message_id`; Outlook keeps the same ID (ImmutableId). Pushes any locally-stored draft attachments to the provider as part of the send (atomic for Gmail, non-atomic for Outlook with D-27 partial-resume).
- `GET /mailboxes/{mailbox_id}/drafts` — List drafts for the mailbox (DB-only, no provider calls). Optional query param `account_id`: when provided, returns drafts of that account; when omitted, returns the unified view across all accounts in the mailbox. Ordered by `created_at DESC`. `DraftOut.body` is sanitised HTML (the rich-text composer; legacy plain-text drafts were converted by migration 0034); each draft carries `attachments[]`.
- `POST /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}/attachments` — Multipart upload (`file` field). Local-only (D-07 lazy push) — the provider draft is not touched; the bytes live in `draft_attachments` until Save/Send. Server-side validation: extension blocklist (D-04a), 25 MB per-file (D-01), 25 MB cumulative per draft (D-02), max 25 attachments per draft (D-03). Multipart bodies > 30 MB are rejected upstream as 413 `request_too_large`.
- `DELETE /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}/attachments/{draft_attachment_id}` — Local-only removal of a draft attachment row.
- `POST /mailboxes/{mailbox_id}/accounts/{account_id}/drafts/{provider_draft_id}/attachments/copy-from-email` — Copy downloadable attachments from a received email into a Forward draft (R-06 / R-12). Always returns 200 even on partial failure: per-row failures (provider 404/410, size / count cap hit, already-copied) surface via the `skipped[]` array. Idempotent — a retry skips rows already landed via `source_attachment_id`. Gmail downloads + re-uploads; Outlook is a no-op (drafts created via `createForward` already inherited attachments server-side).
- `POST /admin/attachments/purge` — Admin maintenance endpoint that drops `email_attachment_blobs` rows whose `email_attachments.last_accessed_at` is older than 30 days (TTL purge, D-15). Requires the `X-Admin-Token` header to match the `ATTACHMENTS_PURGE_TOKEN` env var; 503 `purge_disabled` when the env var is unset, 401 `invalid_admin_token` when set but the header is missing/wrong.
- `POST /admin/image-proxy/purge` — Admin maintenance endpoint that drops `image_proxy_cache` rows whose `last_accessed_at` is older than 30 days (sliding-TTL purge). Mirrors the attachments purge but with its own dedicated `IMAGE_PROXY_PURGE_TOKEN` env var (`X-Admin-Token` header): 503 `purge_disabled` when unset, 401 `invalid_admin_token` when set but the header is missing/wrong. Returns `{ purged_count, freed_bytes }`.

Contacts (recipient autocomplete):

- `GET /contacts/suggestions` — User-level (not mailbox-scoped). Required query param `q` (2-200 chars; whitespace-only collapses to `[]`). Optional `limit` (default 8, max 20). Returns a bare `[{ email, name }]` array of distinct addresses known from the synced mail of every account the user owns — senders of received mail plus recipients of sent mail — matched as accent-/case-insensitive substring, ordered by frequency then recency, excluding the user's own connected-account addresses and anything seen only in SPAM/TRASH. Local-only (no provider call).

Auth:

- `POST /auth/google` — Verify a Google `id_token` and create a session. The first login with a given Google identity creates the user.
- `POST /auth/microsoft` — Verify a Microsoft (Entra, tenancy `common`) `id_token` and create a session. The first login with a given Microsoft identity creates a **separate** user (no account-linking with Google, even at the same email). 500 `env_var_error` when `MICROSOFT_CLIENT_ID` is unset.
- `POST /auth/dev-login` — Local-development backdoor that mints a session for `DEV_LOGIN_EMAIL` without the interactive Google flow. Inert unless `DEV_LOGIN_ENABLED` is truthy (503 `dev_login_disabled` otherwise) AND the request comes from a trusted host (403 `dev_login_not_localhost` otherwise). Never enable in production.
- `GET /auth/me`
- `POST /auth/logout`
- `DELETE /auth/me`

Detailed endpoint contracts: `backend/api/api_guide.md`

## Error Response Format

All API errors follow this schema:

```json
{
  "error": {
    "code": "account_not_found",
    "message": "Account '...' not found.",
    "detail": {}
  }
}
```

Each API error code maps to a fixed HTTP status. The list below shows every code with its status. The full reference (with usage notes per class) lives in `backend/api/api_guide.md` under "Service-Layer Error Classes".

- `api_error` — 500 (default for any unmapped class)
- `mailbox_not_found` — 404
- `account_not_found` — 404
- `email_not_found` — 404
- `draft_not_found` — 404
- `user_not_found` — 404
- `virtual_mailbox_not_found` — 404
- `account_misconfigured` — 400
- `recipients_missing` — 400
- `unauthorized` — 401
- `account_connect_auth_error` — 401
- `forbidden` — 403
- `dev_login_not_localhost` — 403
- `account_not_connected` — 409
- `account_limit_exceeded` — 409
- `email_not_in_trash` — 409
- `app_credentials_invalid` — 500
- `app_credentials_missing` — 500
- `env_var_error` — 500
- `credential_file_error` — 500
- `database_connection_error` — 503
- `database_query_error` — 503
- `database_migration_error` — 500
- `token_decryption_error` — 500
- `token_encryption_error` — 500
- `token_integrity_error` — 500
- `trash_operation_error` — 500
- `email_list_error` — 500
- `unread_count_error` — 500
- `draft_list_error` — 500
- `mailbox_operation_error` — 500
- `account_operation_error` — 500
- `session_operation_error` — 500
- `user_operation_error` — 500
- `virtual_mailbox_operation_error` — 500
- `virtual_mailbox_list_error` — 500
- `recipient_suggestions_error` — 500
- `backfill_status_error` — 500
- `backfill_job_error` — 500 (background worker persistence fallback; swallowed, never sent to a client)
- `email_fetch_error` — 502
- `email_send_error` — 502
- `external_api_error` — 502
- `read_status_update_error` — 502
- `move_to_trash_error` — 502
- `spam_move_error` — 502
- `spam_restore_error` — 502
- `archive_move_error` — 502
- `archive_restore_error` — 502
- `email_content_fetch_error` — 502
- `email_reply_context_error` — 502
- `conversation_fetch_error` — 502
- `draft_creation_error` — 502
- `draft_update_error` — 502
- `draft_delete_error` — 502
- `draft_sync_error` — 502
- `draft_send_error` — 502
- `favorite_update_error` — 502
- `favorite_sync_error` — 502
- `attachment_not_found` — 404
- `attachment_unavailable` — 404
- `attachment_copy_source_unavailable` — 404
- `draft_attachment_not_found` — 404
- `attachment_blocked_extension` — 400
- `attachment_limit_exceeded` — 400
- `attachment_message_size_exceeded` — 400
- `attachment_too_large` — 413
- `request_too_large` — 413
- `attachment_send_failed` — 502 (`detail.failed_attachments[]` + `detail.succeeded[]` for D-27 resume)
- `provider_forbidden` — 502 (provider rejected the attachment fetch with 403)
- `provider_unavailable` — 503 (provider 5xx persistent after retries)
- `purge_disabled` — 503
- `dev_login_disabled` — 503
- `invalid_admin_token` — 401

## Testing

```bash
# All tests
python -m pytest backend/tests

# Unit tests
python -m pytest backend/tests/unit -v

# Integration tests (requires DATABASE_URL)
python -m pytest backend/tests/integration -v

# E2E tests (requires DATABASE_URL and provider credentials)
python -m pytest backend/tests/e2e -v -s
```

Testing docs:

- `backend/tests/unit/unit_guide.md`
- `backend/tests/integration/integration_guide.md`
- `backend/tests/e2e/e2e_guide.md`

## Additional Documentation

### Feature & limits catalog (behavior and "how far it goes")

Narrative, behavior-level documentation of every feature — what it does and what the user experiences — lives in [`docs/features/`](docs/features/README.md). The exact caps, quotas, retries and "what it does NOT support" for each feature live in the paired [`docs/limits/`](docs/limits/README.md). Each feature has a 1:1 file in both folders; start at either `README.md` index.

### Layer guides (project-specific)

- API layer guide: `backend/api/api_guide.md`
- Auth layer guide: `backend/auth/auth_guide.md`
- Database guide: `backend/database/database_guide.md`
- Core (email) guide: `backend/core/core_guide.md`

### General rules (architecture standards)

- `backend/api/CLAUDE.md`
- `backend/auth/CLAUDE.md`
- `backend/database/CLAUDE.md`
- `backend/core/CLAUDE.md`
- `backend/tests/unit/CLAUDE.md`
- `backend/tests/integration/CLAUDE.md`
- `backend/tests/e2e/CLAUDE.md`

### Other

- Frontend setup: `frontend/README.md`
- Agent guidance: `CLAUDE.md`
