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
| | `fees.provider.iotec`, charges ioTec kept on a sweep between pots |

The net is income less costs and can be negative. A reversal can make one code's movement negative, and it is shown as it is.

**Not booked yet.** The charge ioTec takes on a mobile money collection leaves the ioTec wallet without a ledger entry, so it is missing from the costs until the owner's accountant agrees how it is booked (the open row in [decisions.md](decisions.md)). Bank charges on the trust account have no account either. The screen says so under the costs.

## The daily journal for the accounting system

`GET /staff/finance/journal.csv?currency=UGX&date_from=2026-11-12&date_to=2026-11-12` gives one journal per day, one line per account code that moved that day:

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

Which accounting system Finvest uses is not decided, so the file is a plain CSV that most systems import. Mapping Finvest's codes to the accountant's chart is done on import until the system is chosen.

## Routes

| Route | What it does |
| --- | --- |
| `GET /staff/finance?currency=UGX&month=2026-11` | The month's books, income and costs, `closed_through`, `sent_through`, `next_export` and the recent exports. `month` defaults to this month; a future month is 422 `month_in_future`. |
| `GET /staff/finance/journal.csv?currency=&date_from=&date_to=` | The daily journal file, recorded as an export |

Both need `finance.view` (finance and admin).
