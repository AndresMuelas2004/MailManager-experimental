# H4: pinned by exact tag + multi-arch index digest for reproducible builds.
# Tag and digest must move together (the digest is authoritative; the tag is
# documentation). Updating the base image is manual — there is no dependency
# bot. `base` is shared by dev and prod, so both get the same immutable image.
FROM python:3.12.13-slim-bookworm@sha256:4181eb0137487812d8b84ee677de85e5c9f7e08b7c3e16fc564b077cff4907a8 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

EXPOSE 8000

# Development image: the source is bind-mounted at /app by the compose override
# and the override's command adds --reload, so edits on the host reload without
# a rebuild. No COPY here on purpose.
FROM base AS dev
CMD ["python", "main.py"]

# Production image: bake the source into the image (prod has no bind mount).
# .dockerignore keeps .env*, tests/, Scripts/ and caches out. The CMD runs a
# single uvicorn process without --reload (#7/#9).
# H4: drop to an unprivileged user (least privilege). The prod backend writes
# nothing to disk at runtime (logs to stdout, all state in Postgres), binds
# port 8000 (>1024, no privilege needed) and reads its own code read-only, so a
# non-root user needs no writable directory. The code stays root-owned and
# world-readable (the COPY default) so the app cannot modify its own source.
# USER lives ONLY here, never in `base`: hoisting it would also apply to the
# `dev` stage and break Podman rootless's container-root<->host-user mapping for
# the dev bind mounts. The single host prerequisite this adds: the 2 JSON files
# mounted read-only at /secrets must be readable by UID 10001 (see README /
# repository_guide.md) — root containers read them regardless of mode/owner.
FROM base AS prod
RUN useradd --create-home --uid 10001 appuser
COPY . .
USER appuser
CMD ["python", "main.py"]
