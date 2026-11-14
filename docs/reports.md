# Reports

The console's Reports and audit screen offers each report as a CSV file for any period that fits it, beside the audit trail ([console-admin.md](console-admin.md)). The backend is `backend/app/modules/reports`. It owns no tables for downloads and moves nothing: it reads each owning module's own read functions, and the ledger only through `app.modules.ledger.statements`.

## The reports

| Key | Name | Period | Needs, beside `reports.view` | What is in it |
| --- | --- | --- | --- | --- |
| `daily_position` | Daily position | one day | `finance.view` | Every account code's balance at the end of the day, clients added into their code, with the trust account and the ioTec wallet. One currency. |
| `deposits_and_withdrawals` | Deposits and withdrawals | up to 31 days | `money_movement.view` | Every deposit and payout asked for in the days: type, ID, client ID, channel, amount, status, failure code, when it was asked for, when it ended and the minutes between. One currency. |
| `interest_and_tax` | Interest and tax withheld | one calendar month | `finance.view` | Interest each investment earned in the month before tax, the tax withheld and the interest after tax, with a total line: the tax to pay URA. One currency. |
| `maturity_ladder` | Maturity ladder | up to 92 days, may be ahead | `maturities.view` | For each maturity day: running terms, principal, principal set to pay out and principal set to roll over. One currency. |
| `identity_and_risk` | Identity and risk | today | `compliance.view` | Identity checks by status, client accounts by status (active, locked, unclaimed, closed), and screening and monitoring figures. These are how things stand now, which cannot be read for a past day. |
| `client_statements` | Client statements | one calendar month | `clients.view` | Every client's monthly statement for the month: made, empty (nothing to show), failed or still pending. The monthly run sends them; this report shows how it went. |
| `complaints_quarterly` | Complaints for the regulator | one calendar quarter | `complaints.view` | One line per complaint category and one for all: received, resolved, upheld, partly upheld, not upheld, open at the end, past their deadline (resolved late or still open past it), and the average working days to resolve, to a tenth. |

A file names clients by their Finvest ID only: no names, phone numbers or account details. Amounts are exact decimals in the major unit, written from the integers. A cell that a spreadsheet would run as a formula, which includes an ID that starts with `-`, starts with `'`. A file longer than 20,000 rows is refused with `too_many_rows`; ask for fewer days.

Each download is written to the audit trail as `reports.downloaded` on resource `report` with the report's key, the period, the currency and the number of rows. The contents are never on the trail.

## Routes

| Route | What it does |
| --- | --- |
| `GET /staff/reports` | The reports the reader may download, each with its period kind, whether it is by currency, and the period the screen offers first: yesterday for a day, the last seven days, last month, last quarter, the next four weeks for the ladder, today for identity and risk |
| `GET /staff/reports/{key}.csv?date_from=&date_to=&currency=UGX` | One report. A period that does not fit is 422 with `days_backwards`, `period_in_future`, `one_day_only`, `too_many_days`, `whole_month_only`, `whole_quarter_only` or `today_only`. A report the role may not download is 403. |

## Schedules

A report can be sent on a schedule. The email says the report is ready and links to the console's Reports page opened on that report and period; the person signs in and downloads it there, so the download is checked and audited like any other. The file is never attached and never stored (Solo, 2026-11-14: "Email a link"). The email names the report and the period and holds no figure.

One person proposes a schedule: the report, how often, its currency (for a report in one currency) and up to 20 members of staff. A different person approves it, which starts it, or declines it, and the approval needs a passkey or app code from the last few minutes. The proposer may withdraw it while it waits, and it waits in the approvals inbox as `report_schedule`. What a schedule sends never changes: to change one, stop it and propose another. Anyone who may download the report may stop a running schedule, because stopping sends less. Proposing, deciding and stopping each need the report's own permission as well as `reports.view`. Every step is on the audit trail on resource `report_schedule`.

Each recipient must be an active member of staff whose role may download the report, when it is proposed, when it is approved and when each email goes.

| Frequency | Sent | Period |
| --- | --- | --- |
| `daily` | every day | yesterday; for the ladder the next 7 days; for identity and risk today |
| `weekly` | Mondays | last Monday to Sunday; for the ladder the next 28 days; for identity and risk today |
| `monthly` | the 1st | last month; for identity and risk today |
| `quarterly` | the first day of January, April, July and October | last quarter |

A day's report goes `daily`; days `daily`, `weekly` or `monthly`; a month's `monthly`; a quarter's `quarterly`; the ladder `daily` or `weekly`; identity and risk `daily`, `weekly` or `monthly`.

The scheduler starts `reports.queue_scheduled` at 07:00 Kampala. It writes one `report_sends` row per schedule, recipient and period and queues its `reports.send_scheduled` job in the same transaction, so a run that goes round twice sends nothing twice. A morning that is missed is not caught up: the next run sends the next period, and any period can be downloaded at any time. The send job calls Resend with no transaction open and the send's ID as the idempotency key. It records `sent`, `failed` with the provider's code, or `skipped` with `schedule_stopped`, `recipient_not_active`, `recipient_may_not_download` or `suppressed`. The worker may read a staff account's ID, email, name, role and status, and nothing else of it. The console link is the one allowed staff origin, or `https://admin.finvestug.com`.

| Route | What it does |
| --- | --- |
| `GET /staff/report-schedules` | Schedules for reports the reader may download, waiting and running first; the frequencies each report takes; the active staff who may be sent each |
| `POST /staff/report-schedules` | Propose one. Idempotency key. 422 with `frequency_not_allowed`, `recipients_count`, `recipient_not_active` or `recipient_may_not_download` |
| `POST /staff/report-schedules/{id}/approve` | A different person starts it. Idempotency key, recent second step. 403 `proposed_by_you` |
| `POST /staff/report-schedules/{id}/decline` | A different person declines it |
| `POST /staff/report-schedules/{id}/withdraw` | The proposer takes it back |
| `POST /staff/report-schedules/{id}/stop` | Stops a running one. Emails already queued are skipped |

Migration `0320_report_schedules` owns `report_schedules` and `report_sends`. The database refuses an approval or a decline by the proposer, a withdrawal by anyone else, any change to what a schedule sends, and any delete.
