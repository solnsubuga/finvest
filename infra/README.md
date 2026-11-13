# infra

Infrastructure as code for Finvest: Terraform for DigitalOcean and Cloudflare, and the deployment files that go with it.

**Status:** empty. This directory is planned and owned by the infrastructure thread. Feature PRs do not change it.

## Planned shape

Containers on one host behind Caddy, managed PostgreSQL with point-in-time recovery, and private object storage on DigitalOcean, with Cloudflare in front for DNS and the edge. Environments are staging and production.

| Address | Serves |
| --- | --- |
| finvestug.com | Public website |
| app.finvestug.com | Client app on the web |
| admin.finvestug.com | Back office console, staff on allowed networks only |
| api.finvestug.com | Backend API (proposed) |

## Rules

- No tokens, keys or state files in the repository. Provider tokens are supplied at run time.
- The hosting region must be confirmed against Uganda's data protection rules before production data is stored.
