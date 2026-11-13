# Finvest

Finvest lets people in Uganda invest in Treasury bills and fixed income products from a phone or a laptop, pay by mobile money or bank transfer, and watch interest accrue daily. This repository is Finvest 2.0, a rebuild from scratch around a double-entry ledger.

The repository was restarted on 13 November 2026. It currently holds the plan, the working agreements and the directory layout. Application code lands through pull requests.

## What we are building

| Surface | Address | Stack |
| --- | --- | --- |
| Public website | finvestug.com | Astro, static, reads live rates from a public read-only endpoint |
| Client app (Android, iPhone, web) | app.finvestug.com | React Native, Expo, TypeScript |
| Back office console | admin.finvestug.com | React, Vite, TypeScript, Tailwind, shadcn |
| Backend API | api.finvestug.com (proposed) | Python, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL |

One backend application, a modular monolith, serves every surface. Background jobs live in PostgreSQL. There is no Celery and no Redis. ioTec handles mobile money payments, SMS and phone number verification, each behind an adapter. Email goes through Resend.

Two product rules shape everything else:

1. **No wallet.** Every payment in is tied to a specific investment or goal, and every payment out goes straight to the client's mobile money number or bank account.
2. **The ledger is the only place money moves.** Every movement is a balanced journal entry that is never edited or deleted.

## Repository layout

```
backend/              FastAPI application: API, worker, scheduler and migration entry points
apps/client/          Client app, React Native with Expo (Android, iPhone, web)
apps/console/         Back office console, React with Vite
apps/site/            Public website, Astro
packages/tokens/      Shared design tokens (colour, type, spacing)
packages/api-client/  TypeScript types and validators generated from the backend's OpenAPI description
infra/                Terraform for DigitalOcean and Cloudflare, plus deployment files
docs/                 Architecture, rules, decisions and roadmap
```

Each directory has a README that says what belongs there and what state it is in.

## Documentation

Start with [docs/README.md](docs/README.md). The short list:

- [Architecture](docs/architecture.md): modules, how money moves, working without a wallet
- [Technology choices](docs/technology.md): what we use, why, and what we deliberately leave out
- [Engineering patterns](docs/engineering-patterns.md): jobs in PostgreSQL, outbox, idempotency, database roles
- [Money, security and compliance rules](docs/rules.md): the rules no change may break
- [Account numbers](docs/account-numbers.md)
- [Roadmap](docs/roadmap.md): build phases and the feature checklist
- [Decisions and open questions](docs/decisions.md)
- [Design](docs/design.md): the design canvas and brand

## Contributing

All work reaches `main` through pull requests. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening one. Coding agents must also read [AGENTS.md](AGENTS.md).

## Status

Phase 0, foundations. See the [roadmap](docs/roadmap.md).
