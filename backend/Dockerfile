FROM python:3.12-slim-bookworm AS base

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
FROM base AS prod
COPY . .
CMD ["python", "main.py"]
