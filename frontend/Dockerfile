FROM node:22-bookworm-slim AS base

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
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
RUN npm run build

# Production image: nginx serves the static build with SPA fallback (nginx.conf).
# TLS termination and /api routing are handled by Caddy in front.
FROM nginx:alpine AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
