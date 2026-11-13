# Technology choices

| Layer | Choice | Why |
| --- | --- | --- |
| Backend language and framework | Python with FastAPI and Pydantic | Stays in Python, is fast and async, and produces the OpenAPI description automatically from typed code. |
| Data access and migrations | SQLAlchemy 2 with Alembic, using the asyncpg driver | The standard pairing with FastAPI. Explicit transactions and row locks, which the ledger needs. |
| Sign-in, permissions and audit | Our own modules, following Fidiba's identity, roles and audit modules. Passkeys (WebAuthn) for clients and staff. Passwords hashed with Argon2id. | FastAPI does not include these, and Fidiba already has a working pattern. |
| Database | PostgreSQL | Strong transactions and constraints, which the ledger depends on. Amounts are stored as whole shillings or cents in integer columns. |
| Background work | Jobs stored in PostgreSQL, run by our own worker and scheduler processes. No Celery and no Redis. | A job is saved in the same transaction as the business change, so money and its follow-up work can never drift apart. |
| API style | REST with the OpenAPI description FastAPI generates | Typed clients for the app and console are generated from it, so the three codebases cannot drift apart. |
| Client app | React Native with Expo and TypeScript | One codebase for Android, iPhone and web. Over-the-air updates help with slow app store reviews. |
| Public website | A static site built with Astro, reading live rates from a public read-only rates endpoint | Fast on slow connections, good for search, and it holds no client data, so a problem on the website cannot touch accounts. |
| Back office console | React, Vite, TypeScript, Tailwind and shadcn | Same as the Finvest 1.0 frontend, so existing skills carry over. |
| Design tokens | One shared package with the black, lime, type and spacing values | Keeps the app, console and site matching the design canvas. |
| Mobile money | ioTec Pay for now, behind our own payment interface. It collects from MTN and Airtel into one ioTec wallet and pays out to mobile money numbers and bank accounts. | One contract and one integration in place of two telecom integrations. Direct MTN and Airtel adapters can replace it later without touching the core. |
| Bank | A bank adapter with two modes: statement file import now, and a partner bank API when a partner is signed | No bank partner yet, so nothing depends on one, and adding one later needs no change to the core. |
| Identity checks | A provider such as Smile ID for ID lookup and selfie liveness (confirm coverage and pricing for Uganda) | Building document reading and liveness in-house is not worth it. |
| Email | Resend | Decided. |
| SMS | ioTec Messaging for now, with the FINVEST sender name | Sits behind the notifications interface, so the provider can change. |
| Phone number verification | ioTec Verify for now | Confirms that the person owns the number at sign up, when a number changes, and when a mobile money payout number is added. |
| Push notifications | Expo push, which uses Firebase and Apple underneath | Comes with the app framework. |
| Files | Private object storage with short-lived signed links | For ID photos, receipts and statements. Never public. |
| Hosting | Containers on one host behind Caddy, managed PostgreSQL with point-in-time recovery, private object storage, on DigitalOcean, with Cloudflare in front | Proven in Fidiba and simple to run. Confirm the region against the data protection rules. |
| Infrastructure setup | Docker Compose for local and deployed stacks, Terraform for cloud resources | One command, `make dev`, starts everything locally. |
| Delivery | GitHub Actions with tests on every change, a staging environment, and approvals for production | |
| Monitoring | Structured logs with request IDs, Prometheus metrics and alerts, Sentry for errors | With extra alerts on payment failures, job delays and reconciliation differences. |
| Code quality | uv, Ruff, strict mypy, import-linter, pip-audit | Same as Fidiba. |

## What we deliberately do not use

Celery, Redis, microservices, Kubernetes, a separate ledger database product, event streaming, or blockchain. Each adds operating burden without solving a problem Finvest has yet.

## To confirm with ioTec before building

- Fees
- The sandbox
- How fast collections settle out of the ioTec wallet to our bank account
- Payout limits
- Whether Verify sends its own code or only checks the number
