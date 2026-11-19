# The public entry point: Caddy with the dashboard built in. Build from the repository root:
#   docker build -f deploy/caddy.Dockerfile .
FROM node:22-alpine AS web
WORKDIR /app
COPY proto proto
COPY web/package.json web/package-lock.json web/
RUN cd web && npm ci
COPY web web
# No VITE_GATEWAY_URL: the build talks to whichever host serves it.
RUN cd web && npm run build

FROM caddy:2.8.4-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=web /app/web/dist /srv
