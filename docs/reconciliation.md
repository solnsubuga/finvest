# Reconciliation

How Finvest proves each day that the ledger agrees with the money that really moved. The pure rules are in `backend/app/modules/reconciliation/domain`. The matching and day close tables, the scheduled run and the console screens arrive in later pull requests, and this document grows with them.

## Pots

A pot is one place real money sits, with one ledger asset account and one statement:

| Pot | Ledger account | Statement from |
| --- | --- | --- |
| ioTec wallet, MTN | `iotec.wallet`, reference `mtn` | ioTec |
| ioTec wallet, Airtel | `iotec.wallet`, reference `airtel` | ioTec |
| Trust bank account | `bank.trust` | The bank, as an uploaded export until there is a bank API |
| Custodian | To be added with the investments module | The custodian |

## Statements

A statement is an opening balance, lines, and a closing balance for one pot on one business day.

- It is accepted whole or refused whole. If the opening balance plus every line does not equal the closing balance, nothing is imported.
- Amounts are read from text straight into integer money. `1,500,000.00` is accepted for shillings. Half a shilling, a sign, an exponent or stray text is refused, and the refusal names the row, never its contents.
- Each provider export has a `CsvFormat` that says which column holds what. Adding a bank is adding a format.
- Every line has a fingerprint, so importing the same file twice adds nothing. Two identical honest lines on one day keep separate fingerprints.

### Importing

`reconciliation.statements.import_statement` saves a parsed statement in a staff member's transaction (migration `0040_reconciliation_statements`). Importing never moves money. The database repeats the rules, so a mistake in the service cannot save a bad statement:

| Rule | How the database holds it |
| --- | --- |
| One statement per pot and day | A second import of a day is refused: `statement_already_imported` |
| Days join up | A pot's next statement must be for the next day (`statement_day_out_of_order`) and open at the last closing balance (`statement_opening_balance_differs`) |
| A statement adds up | At commit, the opening balance plus the lines must equal the closing balance, and the line count must be the one declared |
| Nothing changes afterwards | Statements and lines are insert only, and a line can join a statement only in the transaction that imported it |
| Who | Only staff import, in their own name. Staff and the worker read. Clients see nothing |

Only a day that has ended in Kampala can be imported (`statement_day_not_over`). A line's narrative is personal data: bank narratives carry names and phone numbers. It is stored for staff to read, handed out only as `counterparty_hint`, and never written to logs or audit details. The SHA-256 of the uploaded file is kept with the statement, and each import is in the audit trail.

`reconciliation.statements.find_statement_lines(session, *, source, date_from, date_to, reference=None, amount=None)` lets another module see whether money it expects has reached a statement, such as a bank transfer waiting for its receipt.

## Matching

A statement line and a ledger movement match only when they carry the same reference, go the same way and are for the same amount. The reference is the deposit or payout ID we gave the provider.

Nothing is matched on amount and date alone. For a line with no usable reference, staff are offered the movements with the same direction and amount within three days, and a person decides.

Everything else is an exception:

| Exception | Meaning |
| --- | --- |
| Missing in ledger | On the statement, not in the books. An unmatched deposit, or a payout we believed had failed. |
| Missing in statement | In the books, not on the statement. Not settled yet, or it never happened. |
| Amount differs | Same reference and direction, different amount |
| Direction differs | Same reference, opposite directions |
| Duplicate reference | The same reference more than once on either side |

Every line and every movement comes out exactly once, in a match or in an exception. Each exception knows how far it puts the statement above or below the books, and the exceptions together always equal the whole gap. Both properties are tested over generated cases.

Reconciliation never moves money and never retries a provider call. Resolving an exception calls the owning module (payments or payouts), which reads the status back from the provider and posts through the ledger.

## The day close

For every pot, the close compares the statement's closing balance, the ledger balance at the end of the same business day, and the exceptions still open.

A day can be closed only when all of these hold:

- The previous day is closed. Days close in order with none skipped, weekends and holidays included.
- The ledger's own check finds no account whose cached balance differs from the sum of its lines.
- Every required pot is present, once, with its statement imported.
- For every pot, statement minus ledger minus open exceptions is exactly zero. One unexplained shilling stops the close.

It takes two people. One staff member prepares the close and a different one approves it. The figures are checked again at approval, and if anything changed since the first signature the close goes back to be prepared again.

A closed day is never reopened, and no ledger entry may be dated on a closed day. A later correction is a new entry on a later day.
