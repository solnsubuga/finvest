# The edge: Caddy with the Cloudflare DNS module, so the back office can get a public certificate
# while it is served only on the tailnet (admin-access.caddy). Every other host keeps the
# Cloudflare Origin CA certificate. Built by deploy.yml and rehearse.sh, context infra/deployment.
FROM caddy:2.11.4-builder-alpine@sha256:0aa610043dab5da82ad0a0268e46bb852785e6f5160f12f1c6fe3f42903d7e1b AS build
RUN xcaddy build v2.11.4 --with github.com/caddy-dns/cloudflare@v0.2.4

FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY --from=build /usr/bin/caddy /usr/bin/caddy
# Refuse an image that lacks the module the tailnet configuration needs.
RUN caddy list-modules | grep -qx 'dns.providers.cloudflare'
