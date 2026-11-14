# Reconciliation

How Finvest proves each day that the ledger agrees with the money that really moved. The pure rules are in `backend/app/modules/reconciliation/domain`. The scheduled run and the console screens arrive in later pull requests, and this document grows with them.

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

Three kinds of movement carry a reference the statement does not show, and another module already recorded how they pair. They are paired first, as `recorded` matches:

- A confirmed bank transfer. The ledger carries the deposit's ID, and the bank shows its own reference on the day the money arrived. The transfer's approved review (one person matched it, a second approved) names the statement line, so the movement pairs with that line.
- A dealing settlement or a bill's maturity. The ledger carries the order's ID, and the bank shows the custodian's reference, which `dealing.holdings.expected_bank_movements` gives. The movement pairs with the one open line under that reference.
- A payment of withholding tax to URA. The ledger carries the remittance's ID, and the bank shows its own reference, which the person who recorded the payment entered and a second person checked against the statement. `tax.service.expected_bank_movements` gives it for each approved payment, and the movement pairs with the one open line under that reference, going out, for the same amount. A payment whose line has not arrived stays an open exception, as any other.

A recorded pair is made only for the same direction and amount, and only with a line still open. When two lines carry the same reference, a person decides.

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

### How matches are kept

`reconciliation.day_close.refresh` copies the ledger's movements on each pot into `reconciliation_movements`, read through `ledger.statements.account_movements`, and pairs what was recorded, then what the references pair. A movement's reference is the `source_id` of its journal entry. Pairs are rows in `reconciliation_matches` (migration `0041_reconciliation_day_close`). Exceptions are not stored: an exception is whatever is still unpaired on either side, worked out each time, so it can never disagree with the matches.

| Rule | How the database holds it |
| --- | --- |
| One line, one movement | A line is matched once and a movement is matched once |
| Same money | A pair from different pots, for different amounts or going different ways is refused |
| By reference means by reference | A match marked `reference` must carry the same reference on both sides |
| A recorded match says why | A `recorded` match carries the reason it was made: the bank transfer's review, or the dealing desk's bank reference (migration 0043) |
| A person gives a reason | Any other match is `staff`, in the name of the signed-in member of staff, with a reason |
| Nothing changes afterwards | Movements and matches are insert only |

`match_by_hand` is the person's pairing. An exception that is a real difference (a bank fee, a deposit nobody claimed) is put right by a ledger entry made by the owning module. That entry is a new movement, and it is then matched like any other.

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

### How the close is kept

| Step | Command | What happens |
| --- | --- | --- |
| First signature | `day_close.prepare` | Refreshes the matches, works out every pot, refuses with the first blocker, and saves the close as `prepared` with each pot's figures |
| Second signature | `day_close.approve` | A different person. Closes the ledger through the day first (`ledger.closing.close_through`), then works the figures out again. If they are the figures that were prepared, the close becomes `closed`. If not, it raises `close_changed_since_prepared` and the ledger close is undone with it |
| Send back | `day_close.send_back` | Declines or withdraws a prepared close, with a reason, so it can be prepared again |

The ledger is closed before the figures are read at approval, so no entry can slip into the day behind the check. A pot's first statement sets its own opening balance, but the close compares the closing balance with the ledger, so a first statement that opens away from the books shows as an unexplained difference and stops the close.

| Rule | How the database holds it |
| --- | --- |
| Two people | A close cannot be closed by the person who prepared it, nor by anyone who imported one of the day's statements, and only in the name of the signed-in member of staff |
| Every shilling explained | A pot's figures cannot be saved unless statement minus ledger minus open exceptions is zero |
| The figures are the ones signed | Positions are saved with the close and never added to or changed |
| Days in order | Once a day is closed, the next close is for the next day |
| Never reopened | A closed or sent back close never changes, and nothing is deleted |
| Who | Only staff prepare and decide. The worker reads. Clients see nothing |

The pots closed today are the two ioTec wallets and the trust bank account (`domain/pots.py`). The custodian joins when the dealing desk can report holdings. A bill's purchase and its maturity are posted to `bank.trust` without the bank's reference, which is not known when the entry is posted, so they pair by the reference the dealing desk gives for them. A maturity on a weekend or public holiday is paid on the next working day, so its bank line can come a day or more after the entry.

## Staff routes

In `backend/app/modules/reconciliation/api.py`. Everything needs `reconciliation.view`. Approving a close also needs the second step in the last ten minutes (403 `confirm_it_is_you`). Refusals carry their code in `fields`.

| Route | What it does |
| --- | --- |
| `GET /staff/reconciliation/pots` | Every pot closed each day, with its last statement day and closing balance, so staff know which day comes next and what it must open at |
| `GET /staff/reconciliation/statements?source&account_reference&date_from&date_to` | One pot's statements in a range, oldest first, at most 100. 422 `date_range_reversed` when `date_to` is before `date_from` |
| `POST /staff/reconciliation/statements` | Multipart: `source`, `account_reference`, `statement_date`, `opening_balance` and `closing_balance` as the statement prints them, `format`, and the CSV `file` (at most 2 MiB). 201 with the statement. 422 names the problem and, for a row, its number (`["body", "file", row]`), never its contents; 409 `statement_already_imported`, `statement_day_out_of_order`, `statement_opening_balance_differs` or `statement_day_not_over` |
| `GET /staff/reconciliation/days/{day}` | Each pot at the end of the day: statement, ledger, open exceptions with their lines and movements, and what is unexplained. A line with no pair lists the movements of the same direction and amount within three days as `suggested_movements`. While the day is open, `blockers` lists every reason it cannot be prepared yet; once prepared or closed, `close` says who signed |
| `POST /staff/reconciliation/days/{day}/refresh` | Brings in the ledger's movements up to the day and pairs what the records and references pair. Safe to run again |
| `POST /staff/reconciliation/matches` | `{statement_line_id, entry_id, line_no, reason}`: a person pairs a line with a movement. 409 `line_already_matched`, `movement_already_matched`, `match_not_found` or `match_not_same_money` |
| `POST /staff/reconciliation/days/{day}/prepare` | The first signature. 409 with the first blocker |
| `POST /staff/reconciliation/days/{day}/approve` | The second signature. 409 `close_not_prepared`, `close_needs_second_person`, `close_needs_someone_who_did_not_import` or `close_changed_since_prepared` |
| `POST /staff/reconciliation/days/{day}/send-back` | `{reason}`. Declines or withdraws a prepared close |

A statement line's narrative is shown only as `counterparty_hint`. Statements are uploaded in the `standard` layout (`domain/formats.py`): a header row `Date,Details,Reference,In,Out`, dates as `YYYY-MM-DD`, and money in and money out in separate columns. Each provider's own export gets a format once one of its files has been seen. Until then staff save the export in that layout.

The journeys import ioTec's statements and sign every day off through these routes (`tests/journeys/test_99_day_close.py`). Pot transfers have no routes yet.

## Transfers between pots

Finvest moves its own money between pots, such as sweeping collected mobile money from an ioTec wallet to the trust bank account so the dealing desk can pay for bills. `reconciliation.transfers` records it (migration `0042_pot_transfers`).

1. One member of staff records a transfer that has already been made, with the provider's reference, what was sent, what arrived and a reason. Nothing is posted.
2. A different member of staff approves it, which posts one entry: debit the receiving pot with what arrived, debit `fees.provider.iotec` with any difference, credit the sending pot with what was sent. A transfer that leaves the trust bank account cannot carry a fee yet (`transfer_fee_not_allowed`), because no bank charges account is agreed. Or they decline it with a reason.

| Rule | How it is held |
| --- | --- |
| Two people | The database refuses a decision by the person who recorded it, or in anyone's name but the signed-in member of staff's |
| Recorded once | A provider reference can be recorded once per sending pot, unless the first was declined |
| Never more than sent | What arrived is never more than what was sent. The difference is the fee |
| Never more than held | An approval that would take the sending pot below zero in the books is refused |
| Not in a closed day | A value date on or before the last closed day is refused when the transfer is recorded |
| Fixed terms | What was recorded never changes, and a decided transfer never changes |
| Reviewed staff money path | `pot_transfer` is in the ledger's list of source types staff may post |

The transfer's ID is the entry's `source_id`, so a statement line that carries it is matched by reference. Otherwise staff pair it by hand. A sweep that has left the wallet but not reached the bank stays an open exception until it arrives.
