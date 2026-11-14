# Observability and alerts

What the running system says about itself, what raises an alert, and what the person who gets the alert does first. Everything here follows the logging rule in [rules.md](rules.md): request IDs and error codes, never personal data, account details or amounts tied to a person.

## Logs

Every process writes one JSON object per line to standard output (`app/infrastructure/logging.py`). Each line has `event`, `level`, `timestamp` and `process` (`api`, `worker`, `scheduler`), and a line written while serving a request also has `request_id`, the same ID the caller gets in `X-Request-ID`.

| Event | Written by | Fields |
| --- | --- | --- |
| `request_served` | API, once per request | `method`, `route` (the route's template, such as `/client/passkeys/{passkey_id}`, or `unmatched`), `status`, `duration_ms` |
| `request_refused` | API | `error_code` |
| `unhandled_error` | API | `error_code`, `error_type`, `where` (file and line) |
| `job_succeeded`, `job_retry_scheduled`, `job_failed`, `job_lease_lost` | Worker | `job_id`, `kind`, `attempt`, `worker`, `queue`, and `error_code` on failure |
| `operations_signal` | API, when the operations check finds stopped work | `signal`, `total` |

`request_served` is not written for `/health/live`, `/health/ready` and `/health/operations` unless they fail, because the uptime checker and the container health checks call them all day. The path as sent and the query string are never logged, so an identifier or phone number in a URL cannot reach the logs.

To find everything about one failed request, search the logs for its request ID. Every error envelope carries it as `request_id`. The client app and console do not show it to people yet; they should, as a short reference on error screens, so support can ask for it.

## Health checks

| Route | Answers 503 when | Watched by |
| --- | --- | --- |
| `GET /health/live` | Never, while the process runs | Docker |
| `GET /health/ready` | PostgreSQL cannot be reached | Docker, the deploy script and the DigitalOcean uptime alert |
| `GET /health/operations` | Background work has stopped: see below | A second DigitalOcean uptime alert |

All three are public, so their body is only `{"status": "ok"}` or the error envelope with `unavailable`. Which check failed is in the logs.

`/health/operations` reads `finvest_operations_signals`, a database function that returns these counts and nothing else (migrations `0130_operations_signals`, `0270_payout_wallet_short` and `0271_interest_behind_signal`):

| Signal | Counts | Usually means |
| --- | --- | --- |
| `jobs_overdue` | Jobs due more than 10 minutes ago that no worker has claimed | The worker is down or far behind |
| `jobs_stalled` | Jobs whose lease ran out more than 5 minutes ago and were never reclaimed | The worker died mid-job and has not restarted |
| `jobs_failed` | Jobs that failed in the last 24 hours, including uncertain provider outcomes | A payment, payout or check needs a person |
| `schedules_late` | A recurring run more than 10 minutes past its fire time | The scheduler is down or has lost its lease |
| `interest_behind` | Running investments still not paid interest for the last finished daily run of their currency | One investment could not be paid, or the run stopped after a run of failures |
| `payouts_waiting_on_wallet` | Queued mobile money payouts whose last send waited because Finvest's ioTec wallet does not hold enough | The wallet needs topping up from the trust account |

Each API process keeps one reading for 15 seconds and answers every call in that window from it (`OPERATIONS_CACHE_FOR`), so a caller cannot load the database by calling the public route often. A check that could not run is never kept. A count that goes up shows within 15 seconds, well inside the uptime checker's interval.

A failed job keeps the check red for 24 hours. That is on purpose: a failed money job is never retried by itself, and somebody has to look at it.

## Alerts

| Alert | Fires when | Goes to |
| --- | --- | --- |
| API down | `/health/ready` fails from the uptime checker | `ALERT_EMAILS` |
| Background work stopped | `/health/operations` fails from the uptime checker | `ALERT_EMAILS` |
| Host CPU, host disk | Over the thresholds in `infra/terraform/main.tf` | `ALERT_EMAILS` |
| Database CPU, memory and disk | CPU over 85%, memory over 90% or disk over 80% for 10 minutes | `ALERT_EMAILS` |

The uptime, host and database alerts are made by Terraform (owned by the infrastructure thread). `ALERT_EMAILS` must hold at least two people before launch, so an alert never depends on one person's inbox.

## First steps when an alert fires

1. **API down.** Check `docker compose ps` on the host and the last API log lines. If the database is the cause, check the managed database's status page in DigitalOcean.
2. **Background work stopped.** Search the logs for `operations_signal` to see which count is raised.
   - `jobs_overdue` or `jobs_stalled`: check that the worker container is running and healthy, and restart it. Jobs resume from where they were. Nothing runs twice, because results are applied once by idempotency key and fencing token.
   - `jobs_failed`: search for `job_failed` to get the kind and error code. An `uncertain_outcome` is never retried blindly: reconciliation asks the provider what happened first ([payments.md](payments.md)). Anything else is requeued by an operator only after its cause is understood.
   - `schedules_late`: check the scheduler container. Only one scheduler holds the lease at a time, and a restarted one takes over when the old lease expires.
   - `interest_behind`: search for `interest_not_posted` to find the investments and the error type, and `interest_run_stopped` for a run that stopped because 25 investments in a row failed (usually a closed ledger day or a missing account). Put the cause right, then pay each investment with `accrual.accrue_investment`, or let the next run catch up: a run pays everything earned so far, so no day is skipped. The count drops as each one is paid.
   - `payouts_waiting_on_wallet`: Finvest's ioTec wallet cannot pay the payouts waiting. `GET /staff/payouts/held-up` lists them with `waiting_for` = `wallet_short`. Finance tops the wallet up with a pot transfer from the trust account ([reconciliation.md](reconciliation.md)). The sweep sends each payout again within 10 minutes, and the count drops to zero as they go. No client's payout fails while it waits.
3. Write what happened and what was done in the incident log, with the request IDs involved.
