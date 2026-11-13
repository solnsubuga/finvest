# Statements and receipts

What Finvest sends a client in writing about their money: a statement after every month, a statement for any dates the client picks in the app, and a receipt for every payment received. Each one is a PDF, sent by email through Resend and kept in the app under Documents.

The code is `backend/app/modules/documents`. Tax certificates and transaction notes will join this module later.

**Status:** this first part is the pure rules, the PDF drawing and the email wording. The tables, the monthly run, the request route, sending and the app screen arrive in their own pull requests. Nothing is sent yet.

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
