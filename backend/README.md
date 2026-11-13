# backend

The Finvest backend: one FastAPI application, a modular monolith, on PostgreSQL.

**Status:** scaffolded. The four entry points, the database roles, the test harness, CI, the ledger, the job queue and idempotency keys are in place. Sign-in arrives in its own pull request.

## Stack

Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 with asyncpg, Alembic, PostgreSQL 17. Tooling is uv, Ruff, strict mypy, import-linter, pip-audit and pytest against real PostgreSQL.

No Celery and no Redis. Background jobs live in PostgreSQL.

## Commands

Run these from the repository root. They need [uv](https://docs.astral.sh/uv/), Docker and `psql`.

| Command | What it does |
| --- | --- |
| `make bootstrap` | Installs the locked dependencies into `backend/.venv` |
| `make dev` | Builds the image and starts PostgreSQL, migrations, the API on port 8000, the worker and the scheduler |
| `make dev-stop`, `make dev-down`, `make dev-logs`, `make dev-status` | Control the local stack. Data is kept. |
| `make check-backend` | Ruff format and lint, mypy, import-linter, and the tests that need no database |
| `make test-integration` | The integration tests against real PostgreSQL. Starts the Compose database, or uses `FINVEST_TEST_ADMIN_URL` if it is set. |
| `make test-stack` | Builds the image, starts all four processes and waits for every health check |
| `make audit` | pip-audit over the locked dependencies |

With the stack running, the API documentation is at http://localhost:8000/docs. It is off in deployed environments unless `FINVEST_API_DOCS` is set.

## Entry points

One image, four processes. Each connects to PostgreSQL as its own role, given in `FINVEST_DATABASE_URL`.

| Process | Command | Database role | Health check |
| --- | --- | --- | --- |
| API | `uvicorn app.api_main:app` | `finvest_app` | `GET /health/live`, `GET /health/ready` |
| Worker | `python -m app.worker_main` | `finvest_worker` | `python -m app.infrastructure.process worker` |
| Scheduler | `python -m app.scheduler_main` | `finvest_worker` | `python -m app.infrastructure.process scheduler` |
| Migrate | `python -m app.migrate_main` | `finvest_migrator` | Exits 0 when migrations are applied |

TLS to the database is verified by default (`FINVEST_DATABASE_SSL_MODE=verify-full`). Set `FINVEST_DATABASE_SSL_ROOT_CERT` to the provider's CA file if it signs with its own CA. Only the local stack and the tests set the mode to `disable`.

The worker runs the handlers registered in `app/worker_main.py`. Register a handler that calls a provider with `external_effect(handler)`: an error it did not sort, or a worker that dies mid-attempt, then ends the job as `uncertain_outcome` for reconciliation, and the job is never run a second time on a guess.

## Layout

```
app/
  shared/            Pure building blocks: clock, push IDs, actors, contracts, error envelope
  infrastructure/    Settings, logging, database engine and roles, process plumbing
  api/               The FastAPI application: request IDs, error handling, health, CORS
  modules/<name>/    One package per business module
    domain/          Pure rules. No FastAPI, SQLAlchemy, network or infrastructure imports.
    service.py       Commands and queries that open transactions and call the domain
    api.py           Routes and Pydantic contracts
  api_main.py, worker_main.py, scheduler_main.py, migrate_main.py
db/init-roles.sql    Creates the three database roles. Run by an administrator, not by migrations.
migrations/versions/ Alembic revisions, hand-written SQL, named NNNN_description.py
tests/
  unit/              No database
  integration/       Real PostgreSQL. Every test here carries the `integration` marker.
  support/           Shared fixtures: fake clock, barrier, database harness
```

import-linter enforces the purity of `app/shared` and of every `app/modules/*/domain`, and keeps `app/infrastructure` free of modules and HTTP. A module never writes to another module's tables.

## Building on the scaffold

- **IDs.** Use `push_id()` from `app.shared.push_ids` and the `push_id` column type created by migration 0001. No auto-increment and no UUIDs.
- **Money.** Use `Money` from `app.shared.money`. Amounts are integers in the smallest unit, and a rate is applied with `mul_ratio`. To move money, call `app.modules.ledger.service`; see [docs/ledger.md](../docs/ledger.md).
- **Identity checks.** `app.modules.kyc` holds the check lifecycle and the identity provider port. See [docs/identity-checks.md](../docs/identity-checks.md).
- **Sealed fields.** `app.infrastructure.sealing.Sealer` encrypts one personal value at a time and makes keyed fingerprints for uniqueness rules. Give it a key and a purpose, and pass `associated_data(table, column, row_id)` so a value opens only where it was saved. Identity checks use it with `FINVEST_IDENTITY_DATA_KEY`.
- **Investments.** `app.modules.investments.domain` holds the product catalogue, rate versions and their approval rules, and the interest and tax arithmetic. See [docs/investing.md](../docs/investing.md).
- **Providers.** A provider call happens only in a job handler, with no transaction open, and its result is sorted into rejected, deferred or uncertain. `app.modules.payments` shows the pattern; see [docs/payments.md](../docs/payments.md). `FINVEST_PAYMENTS_PROVIDER` is `disabled` unless set, and the local stack sets `sandbox`.
- **Audit.** Record who did what and why with `app.modules.audit.service.record`, in the same transaction as the change.
- **Time.** Take a `Clock` from `app.shared.clock`. Never call `datetime.now()` in a rule. Tests use `FakeClock`.
- **Transactions.** `Database.actor_transaction(actor)` opens a transaction acting for a client, a staff member or the system. Row policies read the actor with `finvest_actor_kind()` and `finvest_actor_id()`. `Database.transaction()` has no actor, so a policy shows it nothing. The actor is a fence against bugs, not a credential, because a connection sets it for itself. A policy that admits the `system` actor must be `TO finvest_worker` only, never `finvest_app`.
- **Contracts.** Request and response bodies extend `Contract`. It is strict, so `"1000"` and `1000.0` are refused where an integer amount is expected.
- **Errors.** Raise `ApplicationError(code)`. Every failure leaves the API as the same envelope with a request ID and a code, and never echoes a submitted value.
- **Migrations.** Hand-write SQL with `op.execute`. Grant each table and each function to `finvest_app` and `finvest_worker` explicitly, with only the privileges they need. Nothing is granted by default, including `EXECUTE` on new functions. A test fails if the history has two heads. A deploy runs migrations while the previous release is still serving, and nothing checks that yet, so every migration must leave a schema the previous release can use: add in one release, stop using in the next, remove in the one after. A migration that cannot do that needs `STOP_BEFORE_MIGRATE=true` set for its deploy ([infra/README.md](../infra/README.md)); say so in the PR.
- **Logs.** Log an event name, the request ID (bound automatically) and codes. Never personal data, account details or amounts.

## Sign-in and who is calling

The identity module (`app/modules/identity`) owns client and staff accounts, sessions and staff permissions.

- **Two steps.** `POST /client/login/start` and `POST /staff/login/start` check the mobile number or work email and the password, and return a `login_id` with the second steps on offer. They never open a session. The second step (a passkey, or a code for clients) turns the `login_id` into a session through `IdentityService.open_session`. Staff sessions can only come from a passkey, and the database enforces it.
- **Sessions.** A browser gets an HttpOnly, SameSite=Strict cookie. The phone app gets a token and sends `Authorization: Bearer`. Only a SHA-256 digest of the token is stored. Every session has an idle limit and an absolute limit, set per audience in the settings.
- **Knowing the caller.** Depend on `CurrentClient`, `CurrentStaff` or `require_permission("clients.view")` from `app.modules.identity.api`. Every staff route declares a permission. Pass `session.actor` to `Database.actor_transaction`.
- **Origins.** Browsers may call only from `FINVEST_CLIENT_ALLOWED_ORIGINS` and `FINVEST_STAFF_ALLOWED_ORIGINS` (JSON lists of exact origins). A cookie on an unsafe request is honoured only with one of those origins.
- **Attempt limits.** Wrong guesses are counted in PostgreSQL per account, per account and address, and per address, over 15 minutes. The caller's address is read from `X-Forwarded-For` only when the request comes from a proxy listed in `FINVEST_TRUSTED_PROXIES`.
- **Passwords.** Argon2id, at least 10 characters, refused when common or built from the account's own number or address. Every sign-in failure is the same `invalid_credentials` after the same work.
- **Roles.** `domain/permissions.py` is the one catalogue of roles and permissions. The mapping is a default that the owner has not confirmed yet.

See [docs/architecture.md](../docs/architecture.md), [docs/engineering-patterns.md](../docs/engineering-patterns.md) and [docs/rules.md](../docs/rules.md).
