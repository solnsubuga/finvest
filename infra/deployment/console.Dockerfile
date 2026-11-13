# syntax=docker/dockerfile:1
# The back office console as a static image. Built from the repository root:
#   docker build -f infra/deployment/console.Dockerfile --build-arg VITE_DEPLOY_ENV=production .
# Base images are pinned by digest. Update the tag and the digest together.
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
# staging or production. A production build refuses the sample sign-in (apps/console/vite.config.ts).
ARG VITE_DEPLOY_ENV
RUN test "$VITE_DEPLOY_ENV" = staging || test "$VITE_DEPLOY_ENV" = production
ENV VITE_DEPLOY_ENV=$VITE_DEPLOY_ENV CI=true
RUN corepack enable
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/console/package.json apps/console/
COPY packages/tokens/package.json packages/tokens/
RUN pnpm install --frozen-lockfile --filter "@finvest/console..."
COPY packages/tokens packages/tokens
COPY apps/console apps/console
RUN pnpm --filter @finvest/console build

FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY infra/deployment/console.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/apps/console/dist /srv
# Runs without root on 8080 with every capability dropped (compose.yaml), so the binary must not
# ask for the bind capability the base image gives it. Caddy keeps nothing between restarts.
RUN setcap -r /usr/bin/caddy
ENV XDG_DATA_HOME=/tmp/data XDG_CONFIG_HOME=/tmp/config
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -q --spider http://127.0.0.1:8080/health/live || exit 1
