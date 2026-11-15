# Statements and receipts

What Finvest sends a client in writing about their money: a statement after every month, a statement for any dates the client picks in the app, and a receipt for every payment received. Each one is a PDF, sent by email through Resend and kept in the app under Documents.

The code is `backend/app/modules/documents`. It also draws and sends the yearly withholding tax certificate, from figures the tax module gives it ([tax.md](tax.md)). It also issues the note that confirms an investment started.

**Status:** statements are asked for, built from the ledger, stored, downloadable and emailed with the PDF attached, and the monthly run asks for every client's. Every confirmed payment gets a receipt, built and emailed the same way. The app's Documents screen arrives in its own pull request. The tables are migrations `0140_documents` and `0141_document_emails`, `0142_tax_certificates` lets `documents` hold a certificate, `0143_investment_notes` an investment note, and `0144_ended_investment_notes` the note that confirms an investment ended.

The designs are in `/mnt/project-files/screenshots/statements/` in the project folder: the statement PDF, the receipt PDF, the three emails at phone and wide sizes, and the app's Documents screen in every state.

## A statement

One statement is for one client and covers every investment under their account number. There is no wallet, so a client's value is what their investments are worth plus any money on its way in or out, read from the client's own ledger accounts (`client.awaiting_start`, `client.holding`, `client.interest_accrued`, `client.payout_pending`).

| Part | What it shows |
| --- | --- |
| Heading | The client's name, account number (`1001 004 827`), check code (`K7Q2-9XMA-4TPD`), the period, the issue date and the statement reference |
| Summary | Value at the start, paid in, interest earned, paid out, any correction, and value at the end. One summary per currency. |
| Your investments | Each investment: product, reference, status, start and maturity dates, amount invested, interest earned in the period, value at the end |
| What happened | Every payment in, payout and correction, and interest folded into one line per investment per calendar month |

**Every figure is after tax.** A statement never shows a tax rate, a tax amount or a rate before tax ([rules.md](rules.md)). Tax withheld belongs on the yearly tax certificate.

**The figures must add up.** For each currency, `summarise` checks that the value at the start, plus what was paid in and earned, minus what was paid out, plus corrections, equals the value at the end. The two ends are read from the ledger separately from the lines, so a line that is missing or counted twice shows up as a difference. One shilling out raises `StatementDoesNotAddUp`, and the statement is never drawn or sent.

**Periods.** A monthly statement covers the whole calendar month before the run. A client may ask for any period that ends no later than today and is at most 366 days long. A refusal carries a code for the app: `period_backwards`, `period_in_future` or `period_too_long`.

**Who gets a monthly statement.** Every client who held something at the start or end of the month, or had anything happen during it. A client with nothing at all gets none.

## A receipt

One receipt for each confirmed payment. It shows the amount, what it paid for, when it was received, how it was paid, the payer's number by its last three digits only (`number ending 456`), the provider's reference, the Finvest payment reference and the receipt number. It never shows a full mobile money or bank account number.

## A tax certificate

One certificate per client per tax year, 1 July to 30 June, keyed by the year's label (`2026-27`). It is the one document that shows a tax amount, and like every other it never shows a rate.

| Part | What it shows |
| --- | --- |
| Heading | The client's name, account number, the TIN they gave (or "Not given"), the tax year, the issue date and the certificate number (`TC-<document id>`) |
| Summary | For each currency: interest earned before tax, withholding tax deducted, and interest earned after tax |
| Interest by investment | Each investment that earned in the year, including one that matured, rolled over or was withdrawn: product, reference, start and maturity dates, and the three figures |

The figures come from `tax.service.certificate_figures`, read from the ledger. It checks that before tax less tax is after tax on every line and total, and that the totals are the sums of the lines. If not, the certificate fails with `does_not_add_up`, is never drawn or sent, and staff see the error. A line can be negative when an investment undone this year earned in the last one.

**When.** The monthly run for June, which starts only once the ledger is closed through 30 June, also asks for every client's certificate for the year that June ends. A year in which a client earned nothing is recorded as `empty` and never shown or sent. The certificate is emailed like a statement, with no figure in the email.

**The TIN.** The certificate prints the TIN, which only the identity data key opens. A worker started without `FINVEST_IDENTITY_DATA_KEY` leaves certificates pending and retries them with `identity_key_missing`, logged as an error, instead of drawing one without the TIN.

## An investment note

The note that confirms an investment started, one for each investment. When an investment starts, `investments.started` names the consumer `documents.note_from_investment`, which asks for a document of kind `investment_note` keyed `started:<investment id>` with the investment as its source. The database holds each note to its investment, so an investment gets one note however often the event is delivered. It is built, kept in the app under Investment notes and emailed like a receipt, with no figure in the email.

| Part | What it shows |
| --- | --- |
| Heading | The client's name, account number, the product, the investment number, the start and maturity dates (or "Open term"), the issue date and the note number (`IN-` and the [short reference](#short-references) made from the document ID) |
| Figures | The amount invested, the yearly rate after tax and, for a fixed term, the term, the interest at maturity after tax and what the client gets back |
| At maturity | What the client chose: roll over or pay out. Rolling over is the default |

The figures are the client's own view of the investment (`investments.client_investment`), so like every client figure they are after tax and the note never shows a tax rate or a tax amount. The note says the interest at maturity assumes the investment is held to maturity. A note for an investment that was never started fails with `source_conflict`. An investment that staff undid because it started by mistake (an investment correction) gets no note: its note is recorded as `empty` and never shown or sent, even when the job runs or retries after the undo. Investments carried over from Finvest 1.0 do not publish `investments.started`, so they get no note. Nor does the new investment a rollover opens at maturity: it publishes `investments.rolled_over`, not `investments.started`, so a note is issued only when a client's own payment starts an investment.

**When it ends.** An investment gets a second note when it ends, keyed `ended:<investment id>`: when it matures and is paid out, when it matures into a new investment (`investments.rolled_over`), when it matures with nowhere yet to send the money, and when it is withdrawn early (`investments.withdrawn_early`). Each of those events names the consumer `documents.note_from_ended_investment`. An investment ends once, so it has at most these two notes, and the database allows no other key (`0144_ended_investment_notes`). The end note shows the amount invested, the interest earned after tax and what it came to, the day it ended (the maturity date, or the day the client asked to withdraw, when interest stopped) and what happened to the money: paid out, rolled over into the new investment it names, waiting for the client to choose where to send it, or paid out once the withdrawal is funded. Its file is `Finvest investment end note IN-XXXXX-XXXXX.pdf`, which is how the app tells the two apart. A note asked for before an investment has ended fails with `source_conflict`.

**Sending notes again.** Staff can email notes again when a client lost the email or a day's emails went wrong (`documents/reissues.py`, tables `note_reissues` and `document_email_copies`, migration 0335). The owner chose this on 2026-11-14: the same notes are sent again, not new ones. One member of staff asks with a reason, naming one client or every client and the days the notes were issued on, at most 31 days and none still to come. A different member of staff approves it, or declines it with a note, and only the person who asked withdraws it. Asking and withdrawing need `client_statements.send`. Approving and declining need the same permission and a second step from the last few minutes. Each waiting request shows in the approvals inbox as `note_reissue`. A request that would send nothing is refused with `no_notes` on `issued_from`.

Approving queues `documents.reissue_notes`, keyed `note-reissue:<request id>`. The worker reads the ready notes in scope a page at a time. For each one it records a row in `document_email_copies` and queues `documents.email_copy` in the same transaction. The last page marks the request `done` with the number sent. Each copy is the note's own stored PDF, with the same number and figures, locked with the client's date of birth as before. It goes under the same rules as the first email: a proved address, not suppressed, a date of birth on record. A copy that cannot go is recorded as `skipped` or `failed` with the same codes. Each copy has its own row and its own Resend key (`document-copy:<copy id>`), so the first email's record never changes. One copy of each note is made per request, so a page run twice sends nothing more. Copies still queued after a week are closed as `failed` with `expired` by the same daily schedule as first emails. The database refuses a decision by the person who asked, any change after a decision except the worker finishing an approved request, and a copy of anything but a ready investment note of that client, issued in the approved request's days. Nothing is deleted. Notes whose build failed are not built again by this. Audit events on resource `note_reissue`: `documents.note_reissue.proposed`, `.approved`, `.declined`, `.withdrawn` and `.done`.

## Terms you accepted

The sign-up screen and the screen that claims an account moved over from paper records both have the tick box "I agree to the terms and the privacy notice", with links to read each first. `POST /client/signup/complete` and `POST /client/claim/complete` refuse a body without `accepted_terms: true` (422 `validation_failed`), before anything changes. When the account is created or claimed, the identity module records, in the same transaction, which version of each document the client agreed to and when (`identity/terms.py`, table `identity_terms_acceptances`, migration `0430_identity_terms_acceptances`). The version is the day the wording took effect, `CURRENT_VERSIONS` in `identity/domain/terms.py`. The database allows no change or deletion, and only the unsigned-in sign-up or claim writes a row. A client reads their own with `GET /client/terms-acceptances`; staff read every client's.

The website shows each version on the legal page ("Version of 14 November 2026") and keeps a PDF of every version at `/legal/<document>-<version>.pdf` (`apps/site/src/data/legal.ts`, with old versions in `earlierVersions`). Documents lists them under "Terms you accepted", each opening the PDF of the version the client agreed to. A backend unit test checks the API and the website name the same versions, so a change to the wording changes both in one pull request.

Not built yet: asking clients who agreed to an older version to agree again when the wording changes. Clients moved over from paper records who have not claimed yet have no row, because they have not agreed to the app's terms.

## Checking a statement

Banks and embassies accept a statement as proof of funds, so they need to know it is real. Every statement built since migration `0432_document_checks` carries a **check code**: twelve characters from Crockford's base 32 (no I, L, O or U), shown as `K7Q2-9XMA-4TPD`, under the account number and in the closing note, which says "go to finvestug.com/check and enter the check code". The code is 60 bits from the operating system's random source (`documents/domain/checks.py`). A statement from before has no code and still says to contact Finvest with its reference.

The worker draws the code in the same transaction that builds the statement, and records it in `document_checks` with what the check page shows: the last four digits of the account number and the value at the end in each currency (`documents/checks.py`). The row is insert only, written only by the worker for a statement, and never changed or deleted. The client's name is not kept there: it is personal data, and whoever checks already holds the statement.

On the website, [finvestug.com/check](../apps/site/src/pages/check.astro) asks `GET /public/statement-checks/{code}` from the browser. The API reads one exact code through the `documents_check` database function, so no role can list codes, and only a `ready` statement answers. It reads what was typed as Crockford's base 32 does: case, spaces and dashes are ignored, and O, I and L count as 0, 1 and 1. It always answers 200 with `Access-Control-Allow-Origin: *` and `Cache-Control: no-store`, so the page can show every outcome:

| `result` | Meaning |
| --- | --- |
| `genuine` | `statement` gives `kind` (`monthly` or `requested`), `period_start`, `period_end`, `issued_at`, `account_ending` and `closing` (`[{minor, currency}]`). The page tells the reader to compare each with the paper. |
| `not_found` | No statement has this code. The page says not to rely on the statement and to tell support. |
| `too_many_tries` | One caller may try 20 codes in 15 minutes (`CHECKS_PER_CALLER`). `retry_after_seconds` says how long to wait. |

Receipts, tax certificates and investment notes do not carry a code yet. They still say to contact Finvest with their reference.

## Excel copies

Every statement built since migration `0433_document_spreadsheets` also has an Excel copy, for the client's own records. The worker writes it with openpyxl (`documents/spreadsheet.py`) from the same drawn statement as the PDF, in the same transaction, into `document_spreadsheets`, so the two always show the same figures. The row is insert only, written only by the worker for a statement, and read by the client it belongs to and by staff through the `documents` policy.

The workbook has three sheets: Summary (the facts at the top of the PDF, the check code, and each currency's figures), Your investments, and What happened. Amounts are numbers in the major unit, written from `Money.to_major` as exact decimals and never through a float, with dates as Excel dates, so a client can sort and add them. Text from elsewhere, such as product names, is always written as text, so it can never become a formula. The workbook says that banks and embassies accept the signed PDF: it is not signed itself, and the email still carries only the PDF.

In the app, the form has a Format choice, "PDF, signed" or "Excel". Every statement is made as both, and the choice only decides which file the ready card offers first. Statement rows with an Excel copy carry an Excel button beside the PDF download. A statement from before has only the PDF.

"Include" always covers all investments and goals. A statement is one client's whole account, so its figures add up and its check code vouches for all of it. A statement of one goal only would be a new kind of document and is not offered.

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
| Two members of staff, from the client's page ([client-accounts.md](client-accounts.md#acting-for-a-client)) | `requested_statement` | `staff:` and the approved action's ID |
| The monthly run, for every client with a ledger account | `monthly_statement` | The month, `2026-10` |
| The monthly run for June, for every client with a ledger account | `tax_certificate` | The tax year, `2026-27` |
| A confirmed payment, through the `documents.receipt_from_deposit` consumer of `payments.deposit_confirmed` | `receipt` | The deposit ID, also unique on its own |

1. **Ask.** `service.ask` writes a `pending` row and queues `documents.build` naming only the document's ID, in the same transaction. Asking again with the same key writes nothing and returns the first row, so two taps or a retried run ask once.
2. **Build.** The worker reads the ledger and the names it needs, checks the figures add up, draws the PDF and stores it in the row as `ready`, all in the one fenced transaction that completes the job. A monthly statement with nothing in it becomes `empty` and is never shown or sent. A statement that does not add up becomes `failed` with `does_not_add_up` and is logged as an error for staff. A client with no approved account fails with `not_approved`.

   **Only closed days are final.** A statement is a record the client keeps, so its figures must not change after it is issued. The ledger is closed through a day once that day is reconciled and signed off ([ledger.md](ledger.md)), and nothing can be dated on or before it after that. A monthly statement is built only over closed days: if its month is not closed, the build comes back later and the row stays `pending`. A requested statement may reach days that are not closed yet, because a client asking today wants today. It is built anyway, its row records `not_final_from` (the first day that is not closed), and the PDF says in one line under the heading that figures from that day on may still change.
3. **Tell.** A ready statement raises `statement_ready` in the notification centre, keyed by the document ID and linked to it.
4. **Email.** The same transaction writes a `queued` row in `document_emails` and queues `documents.email`, naming only the document's ID. The worker looks up the client's address from identity (`client_contact`) when it sends, so the address is never stored with the document, and a client who changed it gets the email at the new one. It reads the PDF and renders the email in a read-only transaction, then sends through Resend with no transaction open, with the idempotency key `document:<id>` and a locked copy of the PDF attached. The copy opens with the client's date of birth as eight digits, day, month and year (`05031990`), read from their identity check with the identity key (`kyc.screening_subject`) and never logged or stored; the email says how to open it, and the stored file and the app's download are not locked (`locking.py`, AES-256 with a random owner password that is thrown away). A worker without the identity key sends nothing and the job waits, logging `identity_key_missing`, because an unlocked copy is never sent. The result is recorded once: `sent` with Resend's message ID, `skipped` with `no_email` for a client with no address, whose account is closed, or who has not yet claimed an account carried over from Finvest 1.0, `skipped` with `email_not_verified` for an address the client has not proved with the link identity sends (a mistyped address must not get even a locked copy; the document is still in the app), `skipped` with `no_date_of_birth` for a client with no identity check on record, `skipped` with `suppressed` for an address on the notifications suppression list (a bounce for good or a spam complaint), or `failed` with the code Resend's refusal gave. A send Resend deferred is retried, for up to 20 hours, inside the 24 hours Resend drops a repeated key. A crash in the middle of a send stops the job instead of sending again. An email still `queued` a week after it was queued will not be sent: its job expired or failed and was not put right. The schedule `documents.expire_emails`, at 04:40 in Kampala every day, closes each such row as `failed` with `expired`, so the record shows it was never delivered, and logs `document_emails_expired` with the count. A row whose job is still waiting or running is left alone, however old. The document stays in the app.

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
| Nothing is deleted | No role holds DELETE, and a trigger refuses TRUNCATE. A downgrade refuses while any row exists. Downgrading 0141 also narrows the documents key check again, so it fails if any key holds `_`, as a receipt's key can. |
| Emailed once | Only the worker writes `document_emails`, only for a ready document and its own client. A finished email never changes. Clients read only their own rows. |

The PDF is kept in the row. A statement is a few tens of kilobytes, and keeping it beside its record means it is backed up, fenced and kept exactly like the record.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `POST /client/statements` | Client, with `Idempotency-Key` | Ask for a statement from `date_from` to `date_to`. Answers 202 with the document, `pending`. A refused period is 422 with the reason on the field. |
| `GET /client/documents` | Client | Their statements, receipts, tax certificates and investment notes, newest first. An investment note carries `investment_id` and a receipt carries `deposit_id`, so the app can offer each beside the investment or payment it is about. Both are null on every other kind. |
| `GET /client/documents/{id}` | Client | One document, to poll until it is `ready`. `not_final_from` is the first day whose figures may still change, or null. |
| `GET /client/documents` | Client | Their statements and receipts, newest first |
| `GET /client/documents/{id}` | Client | One document, to poll until it is `ready`. `not_final_from` is the first day whose figures may still change, or null. `reference` is what the PDF and its email call the document (`ST-`, `RC-`, `TC-` or `IN-` and the [short reference](#short-references) made from its ID), and `note` says whether an investment note confirms the investment `started` or `ended` (null for other kinds), so the app never reads either from the file name. |
| `GET /client/documents/{id}/file` | Client | The PDF, as a download, never cached |
| `GET /client/documents/{id}/spreadsheet` | Client | A statement's Excel copy, as a download, never cached. `spreadsheet_byte_size` on the document is its size, or null when there is none. |
| `GET /public/statement-checks/{code}` | Anyone | Whether a statement with this check code was issued. See [Checking a statement](#checking-a-statement). |
| `GET /staff/note-reissues` | `investments.view` | Requests to send notes again, newest first, with how many notes each covers and how the copies went |
| `GET /staff/note-reissues/count?issued_from=&issued_to=&client_id=` | `client_statements.send` | How many notes a request would send again |
| `POST /staff/note-reissues`, `POST /staff/note-reissues/{id}/approve`, `/decline`, `/withdraw` | `client_statements.send`, `Idempotency-Key` | `{client_id?, issued_from, issued_to, reason}`: asking for notes to be sent again, and deciding it. See [Sending notes again](#an-investment-note). |

## Short references

Clients never see a raw push ID. A push ID is 20 case-sensitive characters and can start with a dash, so it cannot be read out over the phone and it ran into the next column of the statement (QA finding F-004). Every reference a client sees is made from the ID by `app/shared/references.py`: the push ID's last 8 characters (48 random bits) written in Crockford base32 as two groups of five, after a prefix. Payments, payouts and investments use `FV-` (`FV-3R0D5-3X44V`); documents keep `ST-`, `RC-`, `TC-` and `IN-`.

Nothing is stored, so older records get one too. `push_id_suffix()` turns a reference as a person reads it out (any case, spaces, with or without the prefix and dashes, O for 0 and I or L for 1) back into the ID's last 8 characters, so staff find the record with `right(id, 8) = suffix` together with the client it belongs to. A reference is a label, never a key, and proves nothing. The client app has the same rule in `apps/client/src/payments/reference.ts`; both test files hold the same known pairs.

Every statement table cell is fitted to its column: its text is made smaller, down to 6.5 points, and only then cut with an ellipsis, so it never runs into the next column.

