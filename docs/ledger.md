# Ledger and audit trail

How the money rules in [rules.md](rules.md) are built. The code is in `backend/app/modules/ledger`, `backend/app/modules/audit`, `backend/app/modules/account_numbers` and `backend/app/shared/money.py`. The database side is migrations 0010 to 0012.

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
| Every entry balances | A deferred constraint trigger checks at commit that the entry has at least two lines and that they sum to zero. |
| History is closed | Lines can be added only by the transaction that created their entry. |
| No negative balances | Each line updates its account's balance in the same statement, under a row lock, and a CHECK constraint refuses a balance below zero. Two payouts racing for the same money cannot both succeed. |
| One currency per entry | A line must match the currency of both its entry and its account. A currency exchange is two entries through exchange accounts. |
| One use per idempotency key | The key is unique. The service returns the saved entry for the same request, and refuses the key for a different one. |
| Reversals are exact | A reversing entry must mirror the original line for line. An entry is reversed at most once, and a reversal is not itself reversed. |
| Clients are fenced | Row policies show a client transaction only that client's accounts, balances and lines. Staff transactions and the worker see everything. Any other API transaction sees nothing and cannot post. |

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
- `post_entry` raises `InsufficientFunds`, `IdempotencyKeyReused` or `UnknownAccount`. After any of them the transaction is still usable and holds no part of the entry.
- `reverse_entry` posts the mirror of an entry. Nothing is ever edited.
- `find_balance_drift` lists accounts whose cached balance is not the sum of their lines. It must always be empty, and is meant for the daily close.

No other module writes to these tables. A module that needs money moved calls the ledger.

The chart of accounts is not seeded here. Each module that moves money opens the accounts it needs.

## Account numbers

The format is in [account-numbers.md](account-numbers.md). `issue_account_number(session, account_type, clock)` draws seven digits from the operating system's secure random source, adds the type and the check digit, and claims the number in the `account_numbers` table inside the caller's transaction. If the number is taken it draws again.

That table is insert only for every role, so a number can never be issued twice or freed when an account closes. The database checks the format and the check digit itself.

## Audit trail

`audit.record(session, event, clock)` writes one row to `audit_events` inside the caller's transaction, so a change and its audit event are saved together or not at all.

An event says who (`actor`), what (`action`, `resource_type`, `resource_id`), the outcome, why (`reason`), and which request or job (`correlation_id`). Approvals and operator controls must give a reason.

| Rule | How |
| --- | --- |
| Cannot be edited | The application roles can only INSERT. A trigger refuses UPDATE, DELETE and TRUNCATE for every role. |
| Cannot be forged | Row policies let an API transaction record only the signed-in client or staff member, or "anonymous" when nobody is signed in. A worker can record only the system or a provider. |
| Only staff read it | A row policy shows the trail to staff transactions only. Which staff may read it is a permission, checked in the application. |
| No personal data | The trail stores identifiers. Before saving, sensitive keys are masked and phone numbers, account numbers, email addresses and credentials inside text are replaced. |

Record retention is not built yet. Removing old events will need a deliberate migration once the regulator's period is confirmed.
