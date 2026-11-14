# syntax=docker/dockerfile:1
# The client app's web version (apps/client, Expo) as a static image. Built from the repository root:
#   docker build -f infra/deployment/app.Dockerfile \
#     --build-arg EXPO_PUBLIC_API_URL=https://api.finvestug.com .
# Expo exports the same React Native screens the phones run, through react-native-web, as one
# page and its scripts (`expo export --platform web`). Base images are pinned by digest. Update
# the tag and the digest together.
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
# The API this build talks to, read at build time (apps/client/README.md). Public.
ARG EXPO_PUBLIC_API_URL
# Regula's Face service for the live selfie check, https only. Empty leaves the guided camera
# selfie alone (docs/identity-checks.md#regula-liveness).
ARG EXPO_PUBLIC_REGULA_FACE_URL=
RUN echo "$EXPO_PUBLIC_API_URL" | grep -Eq '^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || \
      { echo "Not an https origin: '$EXPO_PUBLIC_API_URL'" >&2; exit 1; }; \
    test -z "$EXPO_PUBLIC_REGULA_FACE_URL" || echo "$EXPO_PUBLIC_REGULA_FACE_URL" | \
      grep -Eq '^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$' || \
      { echo "Not an https address: '$EXPO_PUBLIC_REGULA_FACE_URL'" >&2; exit 1; }
# EXPO_PUBLIC_DEMO_AUTH is never set here: a deployed build has no demo (CI checks the bundle).
ENV EXPO_PUBLIC_API_URL=$EXPO_PUBLIC_API_URL EXPO_PUBLIC_REGULA_FACE_URL=$EXPO_PUBLIC_REGULA_FACE_URL \
    EXPO_NO_TELEMETRY=1 CI=true NODE_ENV=production
RUN corepack enable
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/client/package.json apps/client/
COPY packages/tokens/package.json packages/tokens/
COPY packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile --filter "@finvest/client..."
COPY packages/tokens packages/tokens
COPY packages/api-client packages/api-client
COPY apps/client apps/client
RUN pnpm --filter @finvest/client build:web
# Refuse a build that carries the demo, whatever the settings above said.
RUN if grep -rIl -e "correcthorse1" -e "finvestDemoFace" -e "regula-demo-finish" apps/client/dist; then \
      echo "The demo was bundled into a deployed build" >&2; exit 1; \
    fi
# The Content-Security-Policy for exactly this build (app-csp.mjs).
COPY infra/deployment/app-csp.mjs /tmp/app-csp.mjs
RUN node /tmp/app-csp.mjs apps/client/dist "$EXPO_PUBLIC_API_URL" "$EXPO_PUBLIC_REGULA_FACE_URL" > /tmp/csp.caddy

FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY infra/deployment/app.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /tmp/csp.caddy /etc/caddy/csp.caddy
COPY --from=build /src/apps/client/dist /srv
# Runs without root on 8080 with every capability dropped (compose.yaml), so the binary must not
# ask for the bind capability the base image gives it. Caddy keeps nothing between restarts.
RUN setcap -r /usr/bin/caddy
ENV XDG_DATA_HOME=/tmp/data XDG_CONFIG_HOME=/tmp/config
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -q --spider http://127.0.0.1:8080/health/live || exit 1
