# H4: pinned by exact tag + multi-arch index digest for reproducible builds.
# Tag and digest must move together (the digest is authoritative; the tag is
# documentation). Updating the base image is manual — there is no dependency
# bot. `base` is shared by dev and build/prod, so dev gets the same immutable
# image (HMR and the bind mount are unaffected).
FROM node:22.22.3-bookworm-slim@sha256:16d364eebf6b62da439dc993d9b80940c78b0ca38438452f011ab9a25c752644 AS base

WORKDIR /app

# Both flags are load-bearing: --legacy-peer-deps avoids the @types/react 19 vs
# @testing-library peer ERESOLVE; --ignore-scripts skips the husky `prepare`
# hook (no git in this image).
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund --legacy-peer-deps --ignore-scripts

# Development image: Vite dev server with HMR. Source is bind-mounted at /app by
# the compose override, with an anonymous volume preserving node_modules.
FROM base AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# Build stage: compile the static bundle. VITE_* are build-time constants baked
# into the bundle, passed as build args by compose.prod.yml.
FROM base AS build
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_GOOGLE_CLIENT_ID
ARG VITE_MICROSOFT_CLIENT_ID
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID \
    VITE_MICROSOFT_CLIENT_ID=$VITE_MICROSOFT_CLIENT_ID
RUN npm run build

# Production image: nginx serves the static build with SPA fallback (nginx.conf).
# TLS termination and /api routing are handled by Caddy in front.
# H4: nginx-unprivileged already runs as the unprivileged "nginx" user (UID 101)
# and listens on 8080 (its main nginx.conf moves pid/temp to writable /tmp/*), so
# no USER and no writable dirs are needed. conf.d and the web root are inherited
# from the official library/nginx upstream, so the two COPY lines are unchanged;
# COPY leaves the files root-owned world-readable, which UID 101 reads fine
# (static serving is read-only). Pinned by exact tag + multi-arch index digest
# (tag and digest move together). EXPOSE 8080 is lockstep with nginx.conf's
# `listen 8080` and the Caddyfile's `frontend:8080`.
FROM nginxinc/nginx-unprivileged:1.27.5-alpine@sha256:28d91bdce70ad09025ea901458fdd149259d8e05982ade79d4ef2c0d9470eb48 AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
