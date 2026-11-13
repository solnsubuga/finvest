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
| `make test-journeys` | The end-to-end journeys (below), in a database of their own. Same database rules as `test-integration`. |
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
  journeys/          End-to-end client journeys. Every test here carries the `journey` marker.
  support/           Shared fixtures: fake clock, barrier, database harness
```

import-linter enforces the purity of `app/shared` and of every `app/modules/*/domain`, and keeps `app/infrastructure` free of modules and HTTP. A module never writes to another module's tables.

## End-to-end journeys

`tests/journeys/` follows clients through the whole product: signing up, an authenticator app, KYC, paying by mobile money and by bank transfer, daily interest, maturity with a rollover or a payout, goals, an automatic investing plan, dealing, and the day close. Each journey runs the real API application over HTTP, the real worker with every handler `worker_main.handlers` registers, and the scheduler's own schedules, on one fake clock that starts at the real time. Only the outside world is replaced: ioTec by the sandbox collections provider, Resend and the SMS provider by transports that record what they were given, and file storage by memory. Steps that have no route yet call their module's service as the client or member of staff the route will act for.

After every journey the whole ledger must balance (each entry, and each currency overall), no account may be below zero, every kept balance must equal its lines, and every entry of a kind staff can post must have an audit event. Each journey checks its own audit trail too.

The journeys use a database of their own, migrated as production is and without the integration suite's test-only ledger source, because the day close signs off real days on the real cash pots and that can never be undone. The journeys that close a day carry `closes_a_day` and run after all the others. Steps not built yet, and gaps the journeys found, are listed in `tests/journeys/gaps.py`.

## Building on the scaffold

- **IDs.** Use `push_id()` from `app.shared.push_ids` and the `push_id` column type created by migration 0001. No auto-increment and no UUIDs.
- **Money.** Use `Money` from `app.shared.money`. Amounts are integers in the smallest unit, and a rate is applied with `mul_ratio`. To move money, call `app.modules.ledger.service`; see [docs/ledger.md](../docs/ledger.md).
- **Identity checks.** `app.modules.kyc` holds the check lifecycle and the identity provider port. See [docs/identity-checks.md](../docs/identity-checks.md).
- **Sealed fields.** `app.infrastructure.sealing.Sealer` encrypts one personal value at a time and makes keyed fingerprints for uniqueness rules. Give it a key and a purpose, and pass `associated_data(table, column, row_id)` so a value opens only where it was saved. Identity checks use it with `FINVEST_IDENTITY_DATA_KEY`.
- **Investments.** `app.modules.investments.domain` holds the product catalogue, rate versions and their approval rules, and the interest and tax arithmetic. See [docs/investing.md](../docs/investing.md).
- **Goals.** `app.modules.goals` keeps targets and links them to investments. It moves no money: a saving is an investment. See [docs/goals.md](../docs/goals.md).
- **Providers.** A provider call happens only in a job handler, with no transaction open, and its result is sorted into rejected, deferred or uncertain. `app.modules.payments` shows the pattern; see [docs/payments.md](../docs/payments.md). `FINVEST_PAYMENTS_PROVIDER` is `disabled` unless set, and the local stack sets `sandbox`.
- **Telling a client.** Call `notify` from `app.modules.notifications.contract` in the transaction of the change, with an event from the catalogue. See [docs/notifications.md](../docs/notifications.md).
- **Help chat and complaints.** `app.modules.support` holds the client's chat with staff and the complaints register. See [docs/support.md](../docs/support.md).
- **Audit.** Record who did what and why with `app.modules.audit.service.record`, in the same transaction as the change.
- **Time.** Take a `Clock` from `app.shared.clock`. Never call `datetime.now()` in a rule. Tests use `FakeClock`.
- **Transactions.** `Database.actor_transaction(actor)` opens a transaction acting for a client, a staff member or the system. Row policies read the actor with `finvest_actor_kind()` and `finvest_actor_id()`. `Database.transaction()` has no actor, so a policy shows it nothing. The actor is a fence against bugs, not a credential, because a connection sets it for itself. A policy that admits the `system` actor must be `TO finvest_worker` only, never `finvest_app`.
- **Contracts.** Request and response bodies extend `Contract`. It is strict, so `"1000"` and `1000.0` are refused where an integer amount is expected.
- **Errors.** Raise `ApplicationError(code)`. Every failure leaves the API as the same envelope with a request ID and a code, and never echoes a submitted value.
- **Migrations.** Hand-write SQL with `op.execute`. Grant each table and each function to `finvest_app` and `finvest_worker` explicitly, with only the privileges they need. Nothing is granted by default, including `EXECUTE` on new functions. A test fails if the history has two heads. A deploy runs migrations while the previous release is still serving, so every migration must leave a schema the previous release can use: add in one release, stop using in the next, remove in the one after. A migration that cannot do that sets `zero_downtime = False` at module level, and the deploy then stops the previous release before migrating (`app/release_preflight.py`, [infra/README.md](../infra/README.md)); say so in the PR. `tests/unit/test_migration_zero_downtime.py` fails a migration that drops or renames a table, column or function, or makes a column required, unless it sets `zero_downtime` either way.
- **Logs.** Log an event name, the request ID (bound automatically) and codes. Never personal data, account details or amounts.

## Sign-in and who is calling

The identity module (`app/modules/identity`) owns client and staff accounts, sessions and staff permissions.

- **Two steps.** `POST /client/login/start` and `POST /staff/login/start` check the mobile number or work email and the password, and return a `login_id` with the second steps on offer in `methods`. They never open a session. The second step turns the `login_id` into a session.
- **Second steps.** `passkey` (WebAuthn, `/login/passkey/options` then `/login/passkey/verify`) for clients and staff. `authenticator` (a six-digit code from an authenticator app, `/staff/login/authenticator/verify`) for staff only. `code` (a texted or emailed code) for clients only, added with client signup. Staff are never offered a texted or emailed code, and the database refuses a staff session opened with one.
- **Staff enrolment.** A member of staff gets a one-time link, valid for 72 hours, as `<console>/enrol#<token>`. The console sends the token in request bodies to `/staff/enrolment`, then either `/staff/enrolment/passkey/options` and `/verify` or `/staff/enrolment/authenticator/options` and `/verify`. Finishing sets the password, stores the second step and signs the person in. A new link for someone who lost their device removes their old passkeys and authenticator app and ends their sessions. `python -m app.modules.identity.bootstrap --email ... --name ...` adds the first administrator and prints their link. Run for someone who already enrolled, it issues a new link that replaces their password and second step, so it writes an audit event (`identity.staff.enrolment_link_issued`) and must only be run by someone with production access to the API's environment.
- **Passkey sites.** `FINVEST_CLIENT_PASSKEY_SITE` and `FINVEST_STAFF_PASSKEY_SITE` name the site a passkey belongs to. A response is accepted only from that audience's allowed origins, plus `FINVEST_CLIENT_PASSKEY_APP_ORIGINS` for the phone apps. Unset turns passkeys off for that audience.
- **Authenticator key.** The app's secret is stored encrypted with `FINVEST_AUTHENTICATOR_KEY` (32 random bytes, base64url), which lives only in the API's environment. Unset turns the option off. Changing it locks out everyone who uses an app, until they are sent a new enrolment link.
- **Sessions.** A browser gets an HttpOnly, SameSite=Strict cookie. The phone app gets a token and sends `Authorization: Bearer`. Only a SHA-256 digest of the token is stored. Every session has an idle limit and an absolute limit, set per audience in the settings.
- **Knowing the caller.** Depend on `CurrentClient`, `CurrentStaff` or `require_permission("clients.view")` from `app.modules.identity.api`. Every staff route declares a permission. Pass `session.actor` to `Database.actor_transaction`.
- **Origins.** Browsers may call only from `FINVEST_CLIENT_ALLOWED_ORIGINS` and `FINVEST_STAFF_ALLOWED_ORIGINS` (JSON lists of exact origins). A cookie on an unsafe request is honoured only with one of those origins.
- **Client sign-up.** Open on a developer's machine and closed anywhere `FINVEST_ENVIRONMENT` is set (staging, production), until `FINVEST_CLIENT_SIGNUP_OPEN=true`. While closed, the three `/client/signup` routes answer `unavailable` before any other work. Sign-in, the code fallback, password reset and migrated clients' claims are not affected. Production opens it on switch day ([docs/launch.md](../docs/launch.md)).
- **Early withdrawal.** Off unless `FINVEST_EARLY_WITHDRAWAL_OPEN=true`, on every machine. While off, `POST /client/investments/{id}/withdraw` answers `conflict` and `can_withdraw` is false. It stays off in production until the owner says to turn it on, and the PR that wires the dealing desk's funding proves the path with it on in tests, so no withdrawal is made that nothing pays ([docs/investing.md](../docs/investing.md)).
- **Attempt limits.** Wrong guesses are counted in PostgreSQL per account, per account and address, and per address, over 15 minutes. The caller's address is read from `X-Forwarded-For` only when the request comes from a proxy listed in `FINVEST_TRUSTED_PROXIES`.
- **Passwords.** Argon2id, at least 10 characters, refused when common or built from the account's own number or address. Every sign-in failure is the same `invalid_credentials` after the same work.
- **Accounts carried over from Finvest 1.0.** `create_unclaimed_client(session, clock, staff, phone=..., email=...)` in `identity.service` adds an account with status `unclaimed` and no password, in the caller's transaction. Nothing signs in as it and no reset is sent to it. The client claims it through the migration module, which checks the claim link and then calls `CodeService.claim_start(client_id, address=...)` (texts a code) and `claim_complete(challenge_id, code, password, transport)`. That sets the first password, makes the account active, opens a session and returns `Claimed(client_id, session)`. A code proves only the number, so a claimed account is restricted for 24 hours. The API cannot change a status itself: the claim goes through the `identity_claim_client` database function, and an account never becomes unclaimed again. Staff change a client's number with a second person approving: `POST /staff/clients/{id}/phone-changes` proposes, and someone else holding `client_phones.change` approves or rejects it at `/staff/phone-changes/{id}/approval` or `/rejection`. The number changes only through the `identity_apply_phone_change` database function, in the approving transaction. The change ends the client's sessions, voids codes, restricts the account for 24 hours and texts the old number and emails the address on file.
- **Proving an email address.** A signed-in client asks for a link with `POST /client/email/verification`. It emails `<client app>/verify-email#<token>`, which works once, for 24 hours, on any device, through `POST /client/email/verification/confirm`. The address counts as proved only while it is the one the link was sent to. `/client/session` reports `email_verified`, and `client_contact` returns it for the worker, so statements and receipts go only to a proved address. The API cannot set the flag itself: the `identity_verify_email` database function spends the link and sets it. Changing a client's email clears the flag in a trigger, and the API sees only the links of the client it acts for. The link is built on the first entry of `client_allowed_origins`, so that entry must be the client app's public address, such as `https://app.finvestug.com`.
- **Signed-in devices.** Each session records a label built from the User-Agent out of fixed words only, such as "Chrome on Android" or "Finvest app on iPhone", never the raw header. `GET /client/sessions` and `GET /staff/sessions` list live sessions with the current one marked, `DELETE .../sessions/{id}` signs out one device, and `POST .../sessions/sign-out-others` signs out every other device. A sign-in from a device label the person has not used before queues a `new_sign_in` notice. A client always gets it by SMS, since the phone is the proved contact, and by email too only at an address they proved; the worker skips a suppressed address. Staff get it by email. The first sign-in ever sends nothing.
- **Confirming it is you before an approval.** A route that decides another person's change declares `require_confirmed(permission)` instead of `require_permission`. It also needs a passkey or authenticator app code used on this session in the last 10 minutes; signing in counts. Otherwise it answers `confirm_it_is_you` (403). The console then calls `/staff/session/confirmation/passkey/options` and `/staff/session/confirmation/passkey`, or `/staff/session/confirmation/authenticator`, and sends the decision again. `/staff/session` reports `confirmed_until`. `tests/unit/test_confirmed_approvals.py` lists every route that must ask, so a new decision route has to be added there.
- **Roles.** `domain/permissions.py` is the one catalogue of roles and permissions. The mapping is a default that the owner has not confirmed yet.

## Email and text messages

All email goes through Resend, text messages go through the SMS port, and only the worker sends either.

- **Queueing.** Call `queue_message(session, channel="email" or "sms", recipient=..., template=..., variables=..., idempotency_key=..., now=..., deliver_within=...)` from `app.modules.notifications.service` inside the transaction that decided to send. The same key queues nothing a second time. The API role can add a message and can never read one back.
- **Sending.** The worker renders the template, calls the provider (Resend with the key as its `Idempotency-Key`), and records the result with the job's completion. A failure that is safe to repeat is retried. A refusal fails the job, and `failure_code` keeps the real reason. The send job is registered with `external_effect`: if a send may have gone out, it is never sent again. No transaction is open during the call.
- **Content.** `templates.py` in `app/modules/notifications/domain/` holds only the messages that carry a secret or open an account. A link in a message must point to the console or the client app, or the message is refused. Job payloads carry the key only.
- **A code waits in plain text.** A one-time code sits in `notification_messages.variables`, unencrypted, from the moment it is queued until it is sent. It is emptied the moment the message is sent or refused. If the worker dies mid-send, the hourly purge empties it after `deliver_by` (the queue time plus `deliver_within`, minutes for a code). Only the worker role can read the column. Addresses and numbers are removed after 30 days.
- **Settings, worker only.** `FINVEST_EMAIL_BACKEND=resend`, `FINVEST_EMAIL_FROM="Finvest <no-reply@finvestug.com>"` and `FINVEST_EMAIL_RESEND_API_KEY`. `FINVEST_SMS_BACKEND` is `none` or `local-console`, and `local-console` refuses to start where `FINVEST_ENVIRONMENT` is set to anything but `local`. The worker also needs `FINVEST_STAFF_ALLOWED_ORIGINS` and `FINVEST_CLIENT_ALLOWED_ORIGINS` to check links. With a backend unset, nothing is sent and queued messages wait and then expire.

## Files

Receipts, identity document photos and proofs of account ownership go to one private bucket per environment, through `app.infrastructure.storage`. Build the store once per process with `StorageSettings().storage()` and pass it in, like the clock.

- **Uploading.** The API receives the file and stores it: `await storage.put(NewFile(key, data, content_type))`. Browsers never upload to the bucket. Store the file before you open the transaction, then record the returned `key`, `content_type`, `byte_size` and `sha256` in your module's own table in the transaction that records why the file exists. A stored file whose transaction then fails is an orphan under a key nothing points to. That is the safe side: a committed row without its file would be worse. Orphans are not cleaned up today; nothing can read one without its key, and the bucket's versioning keeps every file anyway. If they ever matter, a clean-up job that lists keys no module recorded belongs to the infrastructure thread, not to each module.
- **Keys.** `<module>/<record id>/<file id>` with push IDs, for example `kyc/<check id>/<photo id>`. A key is written once. A new version of a file gets a new key and a new row. Writing the same key with the same bytes again, as a retry does, is harmless.
- **Showing a file to staff.** `storage.read_link(key, content_type=..., lifetime=timedelta(minutes=5), now=clock.now())` returns a link that opens the file inline for at most 15 minutes. Only PDF, JPEG, PNG, WebP and CSV files can be stored or linked, so nothing a browser would run as a page is ever served from the bucket. It makes no network call. Return it only to staff whose permission covers that record, and never log it: anyone holding it can open the file until it expires. The console's content policy allows images and frames from the bucket.
- **Reading a file on the server.** `await storage.read(key, sha256=recorded)` checks the bytes against the digest your table holds and raises `FileMismatch` if they differ.
- **Failures.** `StorageUnavailable` is safe to retry. `StorageRefused`, `FileNotFound` and `InvalidFile` are not. Their messages never contain keys, contents or credentials. Like any network call, none of these runs inside a database transaction.
- **Size.** The API refuses request bodies over 256 KiB by default. A route that takes a file adds its path prefix and limit to `BODY_LIMITS` in `app/api/hardening.py`, and the edge needs the same raise in `infra/deployment/Caddyfile` (ask the infrastructure thread). The store takes at most 25 MiB.
- **Tests.** Use `MemoryStorage()`, which keeps files in the process and refuses a second, different file under one key. It is refused anywhere `FINVEST_ENVIRONMENT` is set.
- **Settings, API and worker.** `FINVEST_OBJECT_STORAGE_BACKEND` (`none`, `memory` or `s3`), `_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, and `_PATH_STYLE` for a local S3-compatible server. Terraform writes them and the deploy sets the backend to `s3`. With the backend unset, every call fails closed.

See [docs/architecture.md](../docs/architecture.md), [docs/engineering-patterns.md](../docs/engineering-patterns.md) and [docs/rules.md](../docs/rules.md).
