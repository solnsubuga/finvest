# backend

The Finvest backend: one FastAPI application, a modular monolith, on PostgreSQL.

**Status:** not scaffolded yet. The first backend PR adds the project files, the four entry points and the test harness.

## Stack

Python, FastAPI, Pydantic, SQLAlchemy 2 with asyncpg, Alembic, PostgreSQL. Tooling is uv, Ruff, strict mypy, import-linter, pip-audit and pytest against real PostgreSQL.

No Celery and no Redis. Background jobs live in PostgreSQL.

## Entry points

| Process | Job |
| --- | --- |
| API | Serves HTTP |
| Worker | Claims and runs jobs from the jobs table |
| Scheduler | Keeps time and creates scheduled runs |
| Migrate | Runs Alembic migrations under the migration role |

## Intended layout

To be settled in the scaffolding PR. The layering is fixed, the names are not.

```
app/
  domain/          Pure rules. No FastAPI, SQLAlchemy or network imports. Clock injected.
  application/     Commands and queries that use the domain and call infrastructure through interfaces
  infrastructure/  Database, jobs, outbox, provider adapters (ioTec, bank, identity, Resend, push)
  api/             FastAPI routes and Pydantic contracts
migrations/        Alembic
tests/
```

Modules: identity and auth, clients and KYC, ledger, payments, investments, goals, reconciliation, notifications, console and audit. A module never writes to another module's tables.

See [docs/architecture.md](../docs/architecture.md), [docs/engineering-patterns.md](../docs/engineering-patterns.md) and [docs/rules.md](../docs/rules.md).
