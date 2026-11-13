# Engineering patterns

The backend follows patterns that already work in the Fidiba backend (FastAPI, SQLAlchemy 2, Alembic, asyncpg, PostgreSQL). This document lists each one and how it applies to money. When you implement one of these, read the Fidiba implementation first if you have access to it.

| Pattern | What it is | Why it suits Finvest |
| --- | --- | --- |
| Jobs in PostgreSQL | A jobs table. Enqueueing happens inside the business transaction. Workers claim jobs with a lease, and every later step checks a fencing token, so a worker that lost its lease cannot finish a job twice. | A payout and its "send to provider" job are saved together or not at all. There is no second system to keep in step with the ledger. |
| Transactional outbox | An event and its consumer jobs are written in the same transaction as the change. Each consumer has its own retry budget and a receipt, so a repeat is replayed, not applied again. | "Payment confirmed" can start the investment, feed goals and send a notification, each exactly once. |
| Uncertain outcomes are never retried blindly | Failures are sorted into deferred, rejected and uncertain. Uncertain ones go to reconciliation. A job handler that calls a provider is registered as having an external effect, so an error it did not sort, or a worker that died mid-attempt, also ends as uncertain and the job is not run again. | If the connection drops after sending a payout, we ask ioTec what happened before trying again. A callback from ioTec is only a hint, and the status is always read back from ioTec before the ledger changes. |
| One scheduler with a leadership lease | APScheduler only keeps time. PostgreSQL owns each scheduled run, unique by name and fire time, with a rule for runs missed during downtime. | Daily interest runs once per day, never twice, and catches up correctly after an outage. |
| Idempotency keys on commands | A key plus a digest of the request. A retry returns the saved result. The same key with a different request is refused. | A client on a weak connection tapping "Invest" twice invests once. |
| Database roles and row policies | Migrations run under one role. The API and worker run under roles that cannot own tables or bypass row security. | Ledger tables are insert only, with no update or delete, for the application roles. Client rows are fenced by row policy, so a bug cannot show one client another's money. |
| Pure domain rules with enforced boundaries | Core rules do not import FastAPI, SQLAlchemy or the network, and import-linter fails the build if they do. The clock is injected. | Interest, tax, goal plans and ledger posting rules become pure functions that are easy to test exhaustively. |
| Contracts generated from Pydantic | Pydantic models are the source. OpenAPI, TypeScript types and Zod validators are generated, and the build fails if they drift. | The app and console can never disagree with the backend about an amount field. |
| Separate processes from one codebase | API, worker, scheduler and migration entry points, each with health checks. | Each can be restarted or scaled alone without splitting the code. |
| Push IDs | 20-character time-ordered IDs, never auto-increment numbers. | Client and transaction references cannot be guessed or counted. |
| Typed errors and safe logs | One error envelope with codes. Logs carry request IDs and codes, never values. | Support can trace a failed payment without account details appearing in logs. |
| Sealed secrets | Provider credentials encrypted in the database with a master key held outside it, scoped by purpose. | ioTec and bank credentials are protected even if a backup leaks. |
| Consumers named in an `events` module | A module that consumes another's event names its consumer in its own `events.py`, which imports only `app.infrastructure.jobs`. The publisher imports that file to list the consumer. Nothing else crosses in that direction. | `investments` names `goals.follow_rollover` and `payments` names `investments.start_from_deposit` without an import cycle, and each consumer's kind is written in one place. |
| Email through an outbox | A request only queues email. The worker sends through Resend with the job's idempotency key. | No duplicate emails. |
| Job diagnostics and audited recovery | Staff can see failed jobs and requeue them with a reason, and the action is audited. | Becomes part of the back office, next to reconciliation. |
| Tests against the real thing | Integration tests use real PostgreSQL, committed migrations and the real roles. Race tests use separate transactions. Browser tests use the real API. A pass on retry does not count. | Money bugs are usually concurrency bugs, and only this kind of test finds them. |
| Hardened edge | Caddy with strict security headers, bounded request sizes, and throttled sign-in. | Needed for any public financial app. |

## What we do not copy from Fidiba

Fidiba's multi-organisation tenancy and its analytics engine. Finvest is one organisation with many clients, so the row policies fence clients and staff roles instead of organisations.
