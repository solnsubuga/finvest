# Ledger and audit trail

How the money rules in [rules.md](rules.md) are built. The code is in `backend/app/modules/ledger`, `backend/app/modules/audit`, `backend/app/modules/account_numbers` and `backend/app/shared/money.py`. The database side is migrations 0010 to 0016.

## Money

`Money` is an integer count of the smallest unit plus a currency: whole shillings for UGX, cents for USD.

- It refuses floats and booleans, and stays inside the range of a PostgreSQL `bigint`.
- Two currencies never combine or compare.
- `Money.from_major("12.34", Currency.USD)` parses a decimal string exactly. Too many decimal places is an error, not a rounding.
- A rate is applied with `mul_ratio(numerator, denominator, rounding)`, in integer arithmetic, and the caller must choose the rounding. Interest at 12.5% a year for 91 days is `principal.mul_ratio(125 * 91, 1000 * 365, rounding)`.

## The ledger

| Table | Holds |
| --- | --- |
| `ledger_accounts` | The chart of accounts. An account is identified by its code, currency, owning client and reference. |
| `ledger_journal_entries` | One row per movement: type, value date, idempotency key, the business record that caused it. |
| `ledger_lines` | The sides of each entry. A debit is a positive amount, a credit is negative. |
| `ledger_balances` | One cached balance per account, kept by the database. |

An account has a kind: asset, liability, equity, income or expense. Its balance is kept in its normal direction (debits raise assets and expenses, credits raise the others), so a healthy balance is always zero or more.

### What the database enforces

These hold even if application code is wrong, because PostgreSQL checks them.

| Rule | How |
| --- | --- |
| Insert only | `finvest_app` and `finvest_worker` hold only SELECT and INSERT on accounts, entries and lines, and only SELECT on balances. Triggers refuse UPDATE, DELETE and TRUNCATE for every role, the table owner included. |
| Every entry balances | Deferred constraint triggers check at commit that the entry has at least two lines and that they sum to zero. The check is queued by the entry and again by every line, so forcing it to run early does not let a later line through. |
| History is closed | Lines can be added only by the transaction that created their entry. |
| No negative balances | Each line updates its account's balance in the same statement, under a row lock, and a CHECK constraint refuses a balance below zero. Two payouts racing for the same money cannot both succeed. |
| One currency per entry | A line must match the currency of both its entry and its account. A currency exchange is two entries through exchange accounts. |
| One use per idempotency key | The key is unique. The service returns the saved entry for the same request, and refuses the key for a different one. |
| Reversals are exact | A reversing entry must mirror the original line for line. An entry is reversed at most once, and a reversal is not itself reversed. |
| Clients are fenced | Row policies show a client transaction only that client's accounts, balances and lines. Staff transactions and the worker see everything. Any other API transaction sees nothing and cannot post. |

### What the database does not enforce

- **Which accounts a command may touch.** Any client or staff transaction can insert lines against any account. The database keeps every entry balanced, every balance at zero or above and every client's view fenced, but it does not stop a faulty command from debiting the wrong client's account. That is the job of the command, and of its tests.
- **That a reversal is always possible.** A reversal is refused if it would take a balance below zero. A deposit credited by mistake and then invested cannot simply be reversed. It is put right with an approved correction, described under [Corrections](#corrections).

Migrations 0010 to 0014 refuse to downgrade while their tables hold any row, so `alembic downgrade` cannot delete the books, the issued numbers or the audit trail, or reopen a closed day.

There is no overdraft switch. If an account ever needs to run below zero, that is a change to the shape of the ledger and goes to the owner first.

### Using it

Everything takes the caller's open session, so an entry commits or rolls back with the business change that caused it.

```python
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, credit, debit

async with worker_database.actor_transaction(SYSTEM) as session:
    await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="payment.deposit_confirmed",
            effective_date=value_date,
            lines=(debit(collections_id, amount), credit(awaiting_start_id, amount)),
            idempotency_key=payment_id,
            source_type="payment",
            source_id=payment_id,
        ),
        clock,
    )
```

- `open_account` returns the account for a code, currency, owner and reference, creating it once.
- `system_account_id` gives the ID of one of Finvest's own accounts by its code. A client transaction cannot read or open those accounts, so a client command uses this to post against them. It returns the ID and nothing else. System accounts are opened by staff or by the worker before a client command needs them.
- `get_entry` returns what the transaction may see. In a client transaction that is only the client's own lines, not the whole entry.
- `post_entry` raises `InsufficientFunds`, `IdempotencyKeyReused` or `UnknownAccount`. After any of them the transaction is still usable and holds no part of the entry.
- `reverse_entry` posts the mirror of an entry. Nothing is ever edited.
- `find_balance_drift` lists accounts whose cached balance is not the sum of their lines. It must always be empty, and is meant for the daily close.

No other module writes to these tables. A module that needs money moved calls the ledger.

The chart of accounts is not seeded here. Each module that moves money opens the accounts it needs.

### Corrections

A mistake in the books is fixed by a new entry, and a person choosing that entry is a risk of its own. So a correction takes two members of staff. One proposes the entry with a reason. A different one approves or rejects it. Only an approval posts anything, and what it posts is exactly what was proposed.

There are two kinds, and both go through the same two steps:

- **A reversal**, when the money has not moved. The proposal carries no lines of its own. The entry is the exact mirror of the original.
- **A forward correction**, when the money has moved on and a reversal would overdraw an account. The proposal carries the lines that take the money back from where it sits now. For a deposit credited to the wrong client and then invested, the payments module debits a receivable held against that client and credits the account the money should have reached. The client's investment is not touched. Recovering the receivable, or writing it off, is a later entry. The account codes, the entry types and the recovery policy belong to the payments module.

```python
from app.modules.ledger import corrections

async with database.actor_transaction(maker) as session:
    proposal = await corrections.propose_correction(
        session,
        maker,
        clock,
        corrects_entry_id=mistaken_entry_id,
        effective_date=today,
        reason="Credited to the wrong client, bank reference 4471",
        entry_type="payment.deposit_correction",
        lines=(debit(receivable_id, amount), credit(unidentified_receipts_id, amount)),
    )

async with database.actor_transaction(checker) as session:
    entry = await corrections.approve_correction(session, checker, proposal.id, clock)
```

`reject_correction` closes a proposal with a reason and posts nothing. `pending_corrections` lists the proposals nobody has decided, oldest first, for the console's review queue. A proposal is decided once. A second decision raises `CorrectionAlreadyDecided`, and deciding your own proposal raises `SecondPersonRequired`. Each step writes an audit event against the resource type `ledger_correction`.

What the database enforces, for every role including the table owner:

| Rule | How |
| --- | --- |
| Two different people | A decision whose `decided_by` is the proposer is refused by a trigger. Row policies let only a staff transaction write, and only under its own ID, so one member of staff cannot write the other's row. The worker can read both tables and write neither. |
| An approval posts what was proposed | An entry with the source type `ledger.correction` is checked at commit against its proposal: the same type, date, currency and lines, an approved decision that points at it, and a reversal only where the proposal said so. Changing a line after the fact is impossible because lines are insert-only. |
| One decision per proposal | The decision's primary key is the proposal. Two checkers deciding at the same moment produce one decision and one entry. |
| Nothing is edited | Proposals and decisions are insert-only, like the rest of the ledger. A rejected proposal stays on record. To try again, make a new proposal. |
| Still within the books | The correction is an ordinary entry. It balances, uses one currency and cannot take any account below zero. If the money has moved again since the proposal, approval fails with `InsufficientFunds`, nothing is decided, and the proposal stays open to be rejected. |

A staff command cannot get around this by calling `post_entry` with some other source type, because staff may post only the source types listed below.

### What staff may post

A staff transaction can post an entry only if its `source_type` is in `ledger_staff_source_types`. Anything else, including an entry with no source type and a plain reversal (`ledger.entry`), is refused by a trigger and `post_entry` raises `StaffSourceNotAllowed`. Staff undo an entry with a correction.

| Source type | Owner | What it is |
| --- | --- | --- |
| `ledger.correction` | ledger | A correction a second staff member approved |
| `payout` | payouts | Releasing a held payout after a staff review |
| `dealing.order` | dealing | An order result a second staff member confirmed |

The worker and client transactions are not limited by this list. Their entries come from jobs and from a client's own requests, each with its own checks.

The list changes only by migration: the module that needs a new way for staff to move money adds its row with an `INSERT` in its own migration, so the change is reviewed. No application role can read or change the list. The test database also lists `test.fixture`, which the test helpers use to set up books as staff.

### Reading an account's history

`app.modules.ledger.statements` is read-only and is what reconciliation uses, since no other module reads the ledger's tables.

- `account_movements(session, account_id, date_from=..., date_to=...)` lists every line on the account dated in the range, both days included. Each has the entry ID and line number, the date, the signed amount (a debit is positive), the entry type and the entry's `source_type` and `source_id`. A statement line is matched to a movement on `source_id`.
- `client_lines(session, client_id, date_from=..., date_to=...)` lists every line on the client's own accounts in the range, with its entry, the account's code and reference, and the change to what Finvest owes the client (positive when the client is owed more). Statements are built from it.
- `client_owed_as_of(session, client_id, on)` gives what Finvest owed the client at the end of `on`, per account code and reference, counting only entries dated up to then.
- `clients_with_accounts(session, after=..., limit=...)` pages through the clients that own a ledger account, for the monthly statement run.
- `balance_as_of(session, account_id, day)` is the balance counting only entries dated on or before that day, in the account's normal direction.
- `account_owner(session, account_id)` is the client the account belongs to, or nothing for one of Finvest's own accounts.
- `client_month_totals(session, client_id=..., code=..., currency=..., date_from=..., date_to=..., entry_type=...)` sums debits and credits by calendar month of effective date, across every account of one client with one code. Insights uses it. With `entry_type` set, it counts entries of that type and the exact reversals of them (an entry whose `reverses_entry_id` points at one), in the reversal's own month. That covers both `reverse_entry` and two-person corrections that reverse. A forward correction, such as a deposit correction to a receivable, has its own entry type and does not count against the original. It is not a balance, and it ignores closed days.

- `total_balance(session, code, currency)` adds up the balances of every account with that code in the currency, each in its normal direction, and is zero when there is none. It refuses a code whose accounts are of more than one kind. It is for staff and the worker: a client transaction would add up only that client's own accounts. The dealing desk reads the buffer with it.

Row policies apply as everywhere else. A client transaction can read only that client's accounts. The first three raise `UnknownAccount` for an account the transaction cannot see, and `client_month_totals` returns nothing for another client.

### Closing a day

Once a day has been reconciled and signed off, the ledger is closed through it. From then on nothing can be dated on or before that day, so the balance that was agreed cannot change.

```python
from app.modules.ledger import closing, statements

async with database.actor_transaction(second_signer) as session:
    await closing.close_through(session, second_signer, day, clock)
    agreed = await statements.balance_as_of(session, collections_id, day)
```

Close first and read the balances after, in the same transaction, as above. The close waits for any entry still being posted, so what is read after it is final. If the figures do not match the statement, raise and the transaction rolls back, close included.

What the database enforces, for every role including the table owner:

| Rule | How |
| --- | --- |
| A closed day takes no entries | A trigger on journal entries refuses an `effective_date` on or before the closed day. `post_entry` raises `DayClosed`. This covers every entry type, reversals and corrections included. |
| Closed days only move forward | The list of closed days is insert-only and each new date must be later than the last. A closed day is never reopened. Closing a day that is already closed changes nothing. |
| Only a day that has ended | Today and future days cannot be closed, by PostgreSQL's own clock in Kampala time, because payments and payouts are still being dated today. `close_through` raises `DayNotEnded`. |
| People close days | Only a staff transaction can close, under its own ID. The worker can read the closed day and cannot set it. Each close writes an audit event. |
| Nothing slips in behind a close | Posting and closing take turns on one lock. A close waits for transactions that have already posted an entry, and an entry posted during a close waits and is then checked against the new date. This relies on the READ COMMITTED isolation level, which the application always uses, so the database refuses to post or close in any other. |

A mistake found in a closed day is fixed with a correction dated in an open day. The closed day keeps showing what was agreed, and the fix shows on the day it was made.

How many people must sign a day off is the reconciliation module's rule. The ledger records who made the call and does not count signatures.

Two things to know when using it. A close briefly pauses posting while it waits for entries in flight, so it should be the first thing its transaction does and that transaction should be short. And an entry may still carry any open date, past or future. If back-dating into open days needs a limit, that is a rule for the command that posts.

## Account numbers

The format is in [account-numbers.md](account-numbers.md). `issue_account_number(session, account_type, clock)` draws seven digits from the operating system's secure random source, adds the type and the check digit, and claims the number in the `account_numbers` table inside the caller's transaction. If the number is taken it draws again.

That table is insert only for every role, so a number can never be issued twice or freed when an account closes. The database checks the format and the check digit itself. Only staff transactions and the worker can issue or look up numbers.

## Audit trail

`audit.record(session, event, clock)` writes one row to `audit_events` inside the caller's transaction, so a change and its audit event are saved together or not at all.

A denied or failed action usually ends with its transaction rolling back, which would take the event with it. For those, call `audit.record_separately(database, actor, event, clock)` after the transaction has ended. It writes the event in a transaction of its own.

An event says who (`actor`), what (`action`, `resource_type`, `resource_id`), the outcome, why (`reason`), and which request or job (`correlation_id`). Approvals and operator controls must give a reason.

| Rule | How |
| --- | --- |
| Cannot be edited | The application roles can only INSERT. A trigger refuses UPDATE, DELETE and TRUNCATE for every role. |
| Cannot be forged | Row policies let an API transaction record only the signed-in client or staff member, or "anonymous" when nobody is signed in. A worker can record only the system or a provider. |
| Only staff read it | A row policy shows the trail to staff transactions only. Which staff may read it is a permission, checked in the application. |
| No personal data | The trail stores identifiers. Before saving, sensitive keys are masked and phone numbers, account numbers, email addresses and credentials inside text are replaced. |

Record retention is not built yet. Removing old events will need a deliberate migration once the regulator's period is confirmed.
