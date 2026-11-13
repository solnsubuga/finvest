# Statements and receipts

What Finvest sends a client in writing about their money: a statement after every month, a statement for any dates the client picks in the app, and a receipt for every payment received. Each one is a PDF, sent by email through Resend and kept in the app under Documents.

The code is `backend/app/modules/documents`. Tax certificates and transaction notes will join this module later.

**Status:** statements are asked for, built from the ledger, stored and downloadable, and the monthly run asks for every client's. **Nothing is emailed yet**: sending with the PDF attached, receipts for confirmed payments and the app screen arrive in their own pull requests. The table is migration `0140_documents`.

The designs are in `/mnt/project-files/screenshots/statements/` in the project folder: the statement PDF, the receipt PDF, the three emails at phone and wide sizes, and the app's Documents screen in every state.

## A statement

One statement is for one client and covers every investment under their account number. There is no wallet, so a client's value is what their investments are worth plus any money on its way in or out, read from the client's own ledger accounts (`client.awaiting_start`, `client.holding`, `client.interest_accrued`, `client.payout_pending`).

| Part | What it shows |
| --- | --- |
| Heading | The client's name, account number (`1001 004 827`), the period, the issue date and the statement reference |
| Summary | Value at the start, paid in, interest earned, paid out, any correction, and value at the end. One summary per currency. |
| Your investments | Each investment: product, reference, status, start and maturity dates, amount invested, interest earned in the period, value at the end |
| What happened | Every payment in, payout and correction, and interest folded into one line per investment per calendar month |

**Every figure is after tax.** A statement never shows a tax rate, a tax amount or a rate before tax ([rules.md](rules.md)). Tax withheld belongs on the yearly tax certificate.

**The figures must add up.** For each currency, `summarise` checks that the value at the start, plus what was paid in and earned, minus what was paid out, plus corrections, equals the value at the end. The two ends are read from the ledger separately from the lines, so a line that is missing or counted twice shows up as a difference. One shilling out raises `StatementDoesNotAddUp`, and the statement is never drawn or sent.

**Periods.** A monthly statement covers the whole calendar month before the run. A client may ask for any period that ends no later than today and is at most 366 days long. A refusal carries a code for the app: `period_backwards`, `period_in_future` or `period_too_long`.

**Who gets a monthly statement.** Every client who held something at the start or end of the month, or had anything happen during it. A client with nothing at all gets none.

## A receipt

One receipt for each confirmed payment. It shows the amount, what it paid for, when it was received, how it was paid, the payer's number by its last three digits only (`number ending 456`), the provider's reference, the Finvest payment reference and the receipt number. It never shows a full mobile money or bank account number.

## The PDFs

`app/modules/documents/pdf.py` draws A4 pages with fpdf2, in black and lime with Plus Jakarta Sans embedded (SIL Open Font License, in `fonts/OFL.txt`). It is data in and bytes out: no network, no clock and no database. The same statement always gives the same bytes, because the creation date written into the file is the issue date. Long statements run onto more pages, with the table heading repeated, and every page carries the reference, the account number and the page count.

## The emails

`domain/emails.py` writes the subject, an HTML body and a plain-text body for each email.

| Email | Subject | Notes |
| --- | --- | --- |
| Monthly statement | Your Finvest statement for October 2026 | No figures. The PDF holds them. |
| Requested statement | Your Finvest statement, 1 Aug 2026 to 13 Nov 2026 | Says when it was asked for, and what to do if the client did not ask |
| Receipt | Receipt: we received UGX 1,000,000 | Shows the amount and the receipt number |

- **No figures in a statement email**, so a message shown on a locked phone says nothing about what a client holds.
- **No images.** Nothing is fetched when an email is opened, so it cannot track anyone and no mail app blocks it.
- **Links only to Finvest:** the app's Documents screen and finvestug.com.
- Every value is escaped before it goes into the HTML.
- Every email ends with a reminder that Finvest never asks for a password, PIN or code.

## How a document is made

A document is asked for first and built later, so no request waits on the ledger or a PDF.

| Who asks | Kind | Key, unique per client and kind |
| --- | --- | --- |
| The client, in the app | `requested_statement` | The client's `Idempotency-Key` |
| The monthly run, for every client with a ledger account | `monthly_statement` | The month, `2026-10` |
| A confirmed payment (next pull request) | `receipt` | The deposit ID, also unique on its own |

1. **Ask.** `service.ask` writes a `pending` row and queues `documents.build` naming only the document's ID, in the same transaction. Asking again with the same key writes nothing and returns the first row, so two taps or a retried run ask once.
2. **Build.** The worker reads the ledger and the names it needs, checks the figures add up, draws the PDF and stores it in the row as `ready`, all in the one fenced transaction that completes the job. A monthly statement with nothing in it becomes `empty` and is never shown or sent. A statement that does not add up becomes `failed` with `does_not_add_up` and is logged as an error for staff. A client with no approved account fails with `not_approved`.

   **Only closed days are final.** A statement is a record the client keeps, so its figures must not change after it is issued. The ledger is closed through a day once that day is reconciled and signed off ([ledger.md](ledger.md)), and nothing can be dated on or before it after that. A monthly statement is built only over closed days: if its month is not closed, the build comes back later and the row stays `pending`. A requested statement may reach days that are not closed yet, because a client asking today wants today. It is built anyway, its row records `not_final_from` (the first day that is not closed), and the PDF says in one line under the heading that figures from that day on may still change.
3. **Tell.** A ready statement raises `statement_ready` in the notification centre, keyed by the document ID and linked to it.

**The monthly run** is the schedule `documents.monthly_statements`, at 06:00 in Kampala every day. It looks at the calendar month before, and the one before that in case the ledger closed both at once. It asks for a month only once the ledger is closed through the month's last day, and only once: a late interest posting, a deposit confirmed by reconciliation or a correction dated in the month is in the statement, because none can arrive after the close. Until the month is closed the morning's run asks for nothing, and from the 6th it logs `monthly_statements_waiting` with `ledger_not_closed` as an error each day, so staff see that statements are held up. Once a month is closed the run asks for each client with a ledger account, 200 at a time, and each page queues the next, so a run of any size finishes and a client is asked once.

**Limits.** A client may ask for 10 statements a Kampala day, because each one reads their whole period.

### Where the figures come from

Only from the ledger, through `app.modules.ledger.statements`: `client_owed_as_of` for the value at the end of the day before the period and at the end of its last day, and `client_lines` for every line on the client's own accounts in between. Lines are grouped by journal entry. An entry that only moves money between the client's own accounts, such as a payment starting its investment, changes nothing the client is owed and is not a line. Every other entry is one line:

| Entry | Line |
| --- | --- |
| `payment.deposit_confirmed` | Payment received, with the method from payments |
| `investment.interest_accrued` | Interest, with the product's name, on the investment it was posted to |
| `payout.*` taking money out | Paid out to your mobile money or bank account |
| `payout.*` bringing money back | Payout returned (a correction) |
| Anything else | Correction |

The other modules only give names: the product of an investment, how a payment was made, where a payout went, and the client's name and account number from their approved identity check (`kyc.service.account_holder`). A name that cannot be found leaves a plain description, never a wrong one. An investment's status becomes words in one place, `domain/building.py`, and a unit test fails if a status has no label.

### What the database enforces

| Rule | How |
| --- | --- |
| Asked before built | A new row must be `pending`, with no file. |
| Built once | Only a system transaction under the worker role moves a row out of `pending`, and a finished row never changes. What it was asked for never changes. |
| A file matches its record | A `ready` row has its PDF, name, size and SHA-256, and the database checks that the size and hash are the file's. Nothing else holds a file. At most 5 MB. |
| Shape | A statement has a period of at most 366 days and no deposit. A receipt has a deposit and no period, and a deposit has one receipt. Only a monthly statement can be `empty`. Only a ready requested statement can have a `not_final_from`, and it falls inside its period. |
| Clients are fenced | A client reads only their own rows and may only ask for a `requested_statement` of their own. Staff read any. |
| Nothing is deleted | No role holds DELETE, and a trigger refuses TRUNCATE. A downgrade refuses while any row exists. |

The PDF is kept in the row. A statement is a few tens of kilobytes, and keeping it beside its record means it is backed up, fenced and kept exactly like the record.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `POST /client/statements` | Client, with `Idempotency-Key` | Ask for a statement from `date_from` to `date_to`. Answers 202 with the document, `pending`. A refused period is 422 with the reason on the field. |
| `GET /client/documents` | Client | Their statements and receipts, newest first |
| `GET /client/documents/{id}` | Client | One document, to poll until it is `ready`. `not_final_from` is the first day whose figures may still change, or null. |
| `GET /client/documents/{id}/file` | Client | The PDF, as a download, never cached |
