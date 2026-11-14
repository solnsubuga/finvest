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
