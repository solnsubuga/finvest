# Withholding tax

Tax on interest is worked out and set aside by the investing module, in `tax.withholding_payable`, as interest is earned (see [investing.md](investing.md)). This document covers what happens to it afterwards: the monthly figures for URA, paying URA, and the figures on each client's yearly certificate. The code is `backend/app/modules/tax`. The pure rules are in `domain/`, the database side is migration 0170, and other modules import only from `tax/service.py`.

Every figure here is read from the ledger, as [rules.md](rules.md) requires of reports for a regulator, so the reports always agree with the books.

## Where the figures come from

`ledger.statements.system_totals` adds up lines on Finvest's own accounts by month, from entries of one source type, and per source when asked. The tax module asks for `interest.expense` and `tax.withholding_payable` from entries whose source is an investment, per investment:

| Figure | Read as |
| --- | --- |
| Interest before tax | The debits less credits on `interest.expense` |
| Tax withheld | The credits less debits on `tax.withholding_payable` |
| Interest after tax | The difference, which is what the client's `client.interest_accrued` was given, because each entry balances |

The investments module keeps every entry that moves a client's tax under `source_type = 'investment'` with the investment's ID (agreed with the investing thread). The daily run, carried-over investments, maturity, early withdrawal and an unwind all do. Sums are signed, so an unwind or a reversal nets out in the month it is posted in. Investments are then read in batches for the client, product and dates.

A month is the calendar month of the entries' effective dates. Entries are dated the day they are posted, so a month that has ended does not change, except through an entry dated in it after the fact, which the closed day prevents once the day is closed.

## The monthly return

URA takes withholding tax monthly: the tax withheld in a month is paid and the return filed by the 15th of the month after. This is a working default to confirm with the owner's tax adviser ([decisions.md](decisions.md)).

| Route | Who | What |
| --- | --- | --- |
| `GET /staff/tax?currency=` | `finance.view` | The screen: tax held for URA now (the balance of `tax.withholding_payable`), the last 12 months, newest first, each with withheld, paid, waiting for a second person, still to pay, the due date and whether it is overdue, and the payments recorded |
| `GET /staff/tax/months/{YYYY-MM}?currency=` | `finance.view` | One month client by client: the name on the approved identity check, the account number, the TIN the client gave, interest before tax and tax withheld |
| `GET /staff/tax/months/{YYYY-MM}/payees.csv?currency=` | `finance.view` | The same as a CSV file for URA's withholding schedule |

The month view and the file show clients' tax numbers, so each request is recorded on the audit trail (`tax.month_viewed`, `tax.month_exported`) with the month and the number of clients, never the figures. TINs are sealed in the identity check and opened with the identity key (`kyc.service.tax_numbers`); a server without the key shows the report with no TINs and says so (`tins_shown`).

"Still to pay" is withheld less paid less waiting. It can be negative: a month in which an investment withheld from in an earlier, already paid month was undone owes URA less than nothing. That is a credit against the next payment, not an error.

The CSV file writes amounts in the major unit from integers (UGX 26490, USD 7.25), and a cell that would start with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'` so a spreadsheet does not run it.

## Paying URA

A payment is recorded after it has left the trust account, by one member of staff, and approved by a different one, who confirms it is them (a passkey or app code in the last few minutes). Both need `tax_remittances.change`, held by admin and finance.

| Step | Route | What happens |
| --- | --- | --- |
| Record | `POST /staff/tax/remittances` | The month, currency, amount, URA's payment registration number (PRN), the bank's reference, the day it was paid and a note. Nothing is posted. |
| Approve | `POST /staff/tax/remittances/{id}/approve` | Checked against URA's receipt and the bank statement. Posts `tax.remitted`, dated the day it was paid: debit `tax.withholding_payable`, credit `bank.trust`. |
| Decline | `POST /staff/tax/remittances/{id}/decline` | With a reason. Nothing is posted, and the references can be recorded again. |

Every command takes an `Idempotency-Key` and answers with the screen. Each waits in the approvals inbox as `tax_remittance`, with the month and currency as its subject.

| Refused | Code |
| --- | --- |
| The month has not ended yet | `month_not_ended` |
| Paid before the month began, or after today | `paid_before_the_month`, `paid_in_the_future` |
| More than the month still owes, counting payments approved and waiting | `more_than_outstanding` |
| The trust account or the tax held cannot cover it | `not_enough_held` |
| URA's or the bank's reference already recorded | `already_recorded` |
| The person who recorded it approves it | `forbidden` |
| Decided already | `conflict` |

A lock per month and currency is taken before reading what a month owes, both when recording and when approving, so two people recording at once never pay more than the month withheld. Approval checks the month again, in case its figures moved.

What the database enforces (migration 0170), whatever the code does:

| Rule | How |
| --- | --- |
| Two people | The approver is never the person who recorded it, and each is the person signed in |
| What was recorded never changes | A trigger refuses any change to the month, amount, references, date or note, and the application role cannot even update those columns |
| Decided once | A remittance goes from proposed to approved or declined, once |
| An approval names its entry | The entry is this remittance's, on its date, in its currency, and moves exactly its amount out of `tax.withholding_payable` and `bank.trust` and nothing else |
| Nothing is deleted | The application cannot delete, and a downgrade refuses to drop rows |
| Staff may post it | `tax.remittance` is on the ledger's list of source types a staff transaction may post |

The payment leaves the trust account, so the bank statement shows it. Reconciliation should expect it by its bank reference; that is a follow-up with the reconciliation thread.

## The yearly certificate

A client's withholding tax certificate covers Uganda's tax year, 1 July to 30 June. `tax.service.certificate_figures(session, sealer, client_id, tax_year)` returns a frozen `Certificate` for the documents module, which draws, stores and emails it with the June statement (see [documents.md](documents.md)).

| Field | What |
| --- | --- |
| `holder_name`, `account_number` | As on statements: given names then surname, and the account number as issued |
| `tin` | The TIN the client gave, or `None` |
| `tax_year` | `TaxYear`, with `label` such as `2026-27`, `first_day` and `last_day` |
| `lines` | One per investment that earned interest in the year, including one that matured, rolled over or was withdrawn: the investment ID as its reference, product, start and maturity dates, interest before tax, tax withheld and interest after tax |
| `totals` | One per currency with a line |

It is built only from the ledger, writes nothing, and runs as the worker or staff. It raises `NoAccount` for a client who is not approved and `DoesNotAddUp` when a line or total does not add up (before tax less tax is after tax, and the totals are the sums of the lines), in which case nothing is issued. A line can be negative when an investment undone this year earned in the last one.

The certificate is the one place a client sees a tax amount. It never shows a rate.

## In the console

The Finance screen (`apps/console/src/screens/finance`, `finance.view`) shows the tax held for URA, each of the last twelve months with its status (to pay, overdue, waiting for approval, paid, credit), one month client by client with the CSV download, and the payments to URA. Staff with `tax_remittances.change` record a payment from an ended month's row, and a second person approves or declines it on its card. Under the sample sign-in, `?sample=empty` and `?sample=offline` show the empty and error states.
