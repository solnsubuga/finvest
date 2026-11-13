# syntax=docker/dockerfile:1
# The public website as a static image. Built from the repository root:
#   docker build -f infra/deployment/site.Dockerfile \
#     --build-arg PUBLIC_SITE_URL=https://finvestug.com --build-arg PUBLIC_APP_URL=https://app.finvestug.com \
#     --build-arg PUBLIC_API_URL=https://api.finvestug.com .
# Base images are pinned by digest. Update the tag and the digest together.
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
# The addresses of this environment. All public, read at build time (apps/site/README.md).
ARG PUBLIC_SITE_URL
ARG PUBLIC_APP_URL
ARG PUBLIC_API_URL
# true lets search engines in. Off until launch (docs/decisions.md).
ARG PUBLIC_INDEXABLE=false
RUN for url in "$PUBLIC_SITE_URL" "$PUBLIC_APP_URL" "$PUBLIC_API_URL"; do \
      echo "$url" | grep -Eq '^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || { echo "Not an https origin: '$url'" >&2; exit 1; }; \
    done; \
    test "$PUBLIC_INDEXABLE" = true || test "$PUBLIC_INDEXABLE" = false
ENV PUBLIC_SITE_URL=$PUBLIC_SITE_URL PUBLIC_APP_URL=$PUBLIC_APP_URL PUBLIC_API_URL=$PUBLIC_API_URL \
    PUBLIC_INDEXABLE=$PUBLIC_INDEXABLE ASTRO_TELEMETRY_DISABLED=1 CI=true
RUN corepack enable
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/site/package.json apps/site/
COPY packages/tokens/package.json packages/tokens/
RUN pnpm install --frozen-lockfile --filter "@finvest/site..."
COPY packages/tokens packages/tokens
COPY apps/site apps/site
RUN pnpm --filter @finvest/site build
# The Content-Security-Policy for exactly these pages (site-csp.mjs).
COPY infra/deployment/site-csp.mjs /tmp/site-csp.mjs
RUN node /tmp/site-csp.mjs apps/site/dist "$PUBLIC_API_URL" > /tmp/csp.caddy

FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY infra/deployment/site.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /tmp/csp.caddy /etc/caddy/csp.caddy
COPY --from=build /src/apps/site/dist /srv
# Runs without root on 8080 with every capability dropped (compose.yaml), so the binary must not
# ask for the bind capability the base image gives it. Caddy keeps nothing between restarts.
RUN setcap -r /usr/bin/caddy
ENV XDG_DATA_HOME=/tmp/data XDG_CONFIG_HOME=/tmp/config
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -q --spider http://127.0.0.1:8080/health/live || exit 1
