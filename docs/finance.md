# Finance: the books, Finvest's income and the accounting export

The console's Finance screen has three parts. Withholding tax is in [tax.md](tax.md). This page covers the other two: the books by account code with Finvest's own income and costs, and the daily journal sent to the accounting system. Both read the ledger and move no money.

The backend is `backend/app/modules/finance`. It reads the ledger only through `app.modules.ledger.statements` and `app.modules.ledger.closing`, as every module other than the ledger does.

## The books by account code

For one currency and one month, every account code that has a line dated up to the end of the month (or today, for this month) is listed with:

| Column | Meaning |
| --- | --- |
| Opening | The balance at the start of the month's first day, in the code's normal direction |
| Debits, Credits | What the month's lines debited and credited, each zero or more |
| Closing | Opening plus the month's movement, in the normal direction |

Each client's accounts are added into their code, so the table names no client: "Paid in, awaiting start" is one line for every client together. A code whose accounts are of two kinds cannot be added up and the screen refuses with `ledger_mixed_account_kinds`; the ledger's own codes never do that.

Once the ledger is closed through the end of a month, that month's figures cannot change.

## Finvest's income and costs

Finvest charges clients no fee to open an account, to invest or to withdraw early (see [decisions.md](decisions.md)). So the design's "fee income" is shown as what Finvest earns and what it costs, read straight from the ledger's income and expense codes over the month:

| Income | Costs |
| --- | --- |
| `dealing.bill_income`, the discount earned on bills held to maturity | `investments.interest_expense`, the interest clients earned |
| `dealing.sale_gains`, gains on bills sold | `dealing.sale_losses`, `dealing.maturity_losses` |
| | `fees.provider.iotec`, ioTec's charge on each mobile money payment in, and charges ioTec kept on a sweep between pots |

The net is income less costs and can be negative. A reversal can make one code's movement negative, and it is shown as it is.

**ioTec's charge on a payment in** is booked when the deposit is confirmed, as its own entry `payment.provider_charge`: debit `fees.provider.iotec`, credit `iotec.wallet`, rounded up to a whole shilling ([payments.md](payments.md#iotec-pay)). **Not booked yet:** bank charges on the trust account have no account. The screen says so under the costs.

## The daily journal for the accounting system

`POST /staff/finance/journal.csv` with `{"currency": "UGX", "date_from": "2026-11-12", "date_to": "2026-11-12", "format": "csv"}` gives one journal per day, one line per account code that moved that day. It is a POST because every export is recorded, and a GET could be repeated by a browser or a proxy. The Finvest CSV looks like this:

```
Date,Journal,Account code,Account name,Kind,Currency,Debit,Credit
2026-11-12,FINVEST-UGX-20261112,bank.trust,Trust bank account,asset,UGX,250000,0
2026-11-12,FINVEST-UGX-20261112,client.awaiting_start,"Paid in, awaiting start",liability,UGX,0,250000
```

| Rule | Why |
| --- | --- |
| Only days the ledger is closed through | A journal never changes after it is sent. A later day gets a 409 with `day_not_closed`. |
| At most 31 days in one file, never backwards | 422 `too_many_days` or `days_backwards` |
| Each day's debits equal its credits | The ledger guarantees it. The export checks it again and fails rather than send a file that does not balance. |
| Clients are added into their code | The accounting system gets the general ledger, not client records |
| Amounts in the major unit with the currency's decimals, from integers | No floating point. UGX has none, USD has two. |
| The journal reference is `FINVEST-<currency>-<YYYYMMDD>` | The same day always has the same reference, so the accounting system can refuse a journal it already has |
| A cell that would run as a formula starts with `'` | Spreadsheets |

The export moves no money and changes no client fact, so one member of staff with `finance.view` does it. Each export is saved in `finance_journal_exports` (migration 0250) with the days, the number of lines, the debit total, who exported it and when, together with an audit event `finance.journal_exported` in the same transaction. The database lets only the signed-in member of staff add a row as themselves, and never changes or removes one.

Nothing is sent to the accounting system from Finvest: staff download the file and import it there by hand. The screen shows the last day downloaded and offers the next closed days not yet downloaded: from the day after the last one, or the last closed day if nothing was ever downloaded, up to 31 days at a time. It also lists the last five exports, each with Download again, because the server records an export as it answers and a file lost on the way would otherwise be gone from the screen. Downloading again is recorded as another export.

### Each accounting system's own file

`format` picks the file. `csv` is the Finvest CSV above. The others are the journal import files of the common accounting systems, written in `backend/app/modules/finance/domain/formats.py`:

| `format` | System | Columns | How a journal is grouped | The account is |
| --- | --- | --- | --- | --- |
| `quickbooks` | QuickBooks Online, journal entry import | `Journal No.`, `Journal Date`, `Account Name`, `Debits`, `Credits`, `Description` | Same `Journal No.` | The account's name, a sub-account as `Parent:Child` |
| `xero` | Xero, manual journal import | Xero's template: `*Narration`, `*Date`, `Description`, `*AccountCode`, `*TaxRate`, `*Amount` and the tracking columns | Same narration and date | Xero's account code. `*Amount` is positive for a debit, negative for a credit. `*TaxRate` comes from the map. |
| `sage` | Sage Accounting, journal import | `Reference`, `Date`, `Description`, `Nominal Code`, `Details`, `Include on VAT/Tax Return?`, `Debit`, `Credit` | A new `Reference` starts a journal | The nominal code, a number. Never on the VAT return. |
| `zoho` | Zoho Books, manual journal import | `Journal Date`, `Journal Number`, `Reference Number`, `Notes`, `Status`, `Currency`, `Account`, `Description`, `Debit`, `Credit` | Same `Journal Number`. Zoho Books matches the columns on its Map Fields step. | The account's name. Each journal comes in as a `Draft` for the accountant to publish. |

In every system's file:

* The journal reference is the same `FINVEST-<currency>-<YYYYMMDD>`, so the system can refuse a day it already has.
* Each Finvest code's movement that day is one line, net: a debit when it was debited more than credited, a credit otherwise. A code whose debits and credits that day were equal is left out, since the systems refuse a line of nothing. Each day still balances.
* Dates are day first, `12/11/2026`, as the systems use for an organisation in Uganda.
* The line's description is the Finvest account's name and code, such as `Trust bank account (bank.trust)`.
* Only shillings. Each system keeps its books in one base currency and takes another only with an exchange rate Finvest does not have. A dollar journal for a system is 422 `currency_not_supported`; download it as the Finvest CSV.

The file headers follow each system's published import template. The first import into each system should still be checked by the accountant, as the import screens let the columns be matched by hand where a system names them differently.

### Account code maps

A system's file names the system's accounts, so each system has an account code map: for each Finvest ledger code, the account it is posted to there. A map lives in the console's Settings under Accounting system codes and is kept in `finance_code_maps` and `finance_code_map_lines` (migration 0290).

* One member of staff proposes a whole map for one system, with a reason, and a different one approves or rejects it with a note. Only the proposer can withdraw it. PostgreSQL refuses the proposer deciding their own, a decided map changing, accounts being added by anyone but the proposer, or outside the transaction that proposed the map (so the approver decides on the whole map they read), and any row being removed.
* One proposal waits per system at a time. Exports keep using the map in use until a new one is approved.
* Seeing the maps needs `settings.view`. Proposing and deciding need `account_codes.change`, held by the admin role only for now. Deciding also needs a passkey or app code from the last ten minutes.
* Each step is an audit event: `finance.code_map_proposed` with how many codes it adds, changes and removes, then `finance.code_map_approved`, `_rejected` or `_withdrawn`. The accounts themselves are in the map, kept for good.
* A code must be one the ledger has. A Xero code is up to 10 letters or numbers, a Sage nominal code up to 8 digits, and a QuickBooks or Zoho Books account name up to 100 characters on one line. Xero also needs the name of the tax rate written on every line, `Tax Exempt` to start with.
* A map need not name every code. An export for a system that meets a code its approved map does not name is refused with 409 `conflict` and one field per code, `["codes", "<code>"]`, `not_mapped`, so the journal never lands on a guessed account. The Finance screen names the codes and sends the person to Settings.
* Each export records its format and the map it used, in `finance_journal_exports`. The database refuses an export that names a map not approved for its own system.

## Routes

| Route | What it does |
| --- | --- |
| `GET /staff/finance?currency=UGX&month=2026-11` | The month's books, income and costs, `closed_through`, `sent_through`, `next_export`, the recent exports with their format, and `formats`: each file the journal can be downloaded as, whether its system has an approved map, the ledger codes the map does not name, and its currencies. `month` defaults to this month; a future month is 422 `month_in_future`. |
| `POST /staff/finance/journal.csv` | The daily journal file in the chosen format, recorded as an export |
| `GET /staff/finance/account-codes` | Every ledger code, and each system's map in use, the proposal waiting and the last five decisions. `settings.view`. |
| `POST /staff/finance/account-codes/{system}/proposals` | Proposes a whole map, `{accounts, tax_rate, reason}`. A blank account leaves a code without one. `account_codes.change`. |
| `POST /staff/finance/account-codes/proposals/{id}/decision` | `approved` or `rejected` by someone else, `withdrawn` by the proposer, with a reason. `account_codes.change`, confirmed in the last ten minutes. |

The first two need `finance.view` (finance and admin).
Both need `finance.view` (finance and admin).

## Closing the month

The ledger closes day by day. Closing the month is Finance's sign-off that a month is finished. It moves nothing and locks nothing the day closes have not already locked. It records that two people checked the month, and it keeps that month's books exactly as they were signed.

One person proposes closing a month, and a different person approves it. The approval needs a passkey or app code from the last few minutes. The month waits in the approvals inbox as `month_close`, and the proposer may withdraw it while it waits. A proposal is refused with 409 for any of these reasons:

| Code | Why |
| --- | --- |
| `month_not_ended` | the month has not ended |
| `month_already_closed` | the month is already closed |
| `close_months_in_order` | it is not the month after the last one closed (the first close may be any month) |
| `days_not_closed` | a day in it is still open in the ledger |
| `books_do_not_balance` | its debits and credits differ in a currency |
| `close_already_proposed` | another close is already waiting |

The proposal keeps every account code's opening balance, debits, credits and closing balance for the month in every currency, in minor units, with clients folded into their codes. It also keeps a SHA-256 digest of those figures. Approving reads the books again and refuses with `books_changed` if the digest differs, so the month that is signed is the month that was shown. Every step is on the audit trail on resource `finance_month_close`.

| Route | What it does |
| --- | --- |
| `GET /staff/finance/month-closes` | The last month closed, the month to close next and whether it can be proposed now (`refusal` is null when it can), and the latest twelve closes with their books |
| `POST /staff/finance/month-closes` | Propose `{month: "YYYY-MM", reason}`. Takes an idempotency key |
| `POST /staff/finance/month-closes/{id}/approve` | A different person closes the month. Takes an idempotency key and a recent second step. Refused with 403 `proposed_by_you` |
| `POST /staff/finance/month-closes/{id}/decline` | A different person sends it back |
| `POST /staff/finance/month-closes/{id}/withdraw` | The proposer takes it back |

Migration `0321_finance_month_closes` owns `finance_month_closes`. The database allows one approved close per month and one waiting close at a time. It refuses an approval or decline by the proposer, any change to the books kept, and any delete.
