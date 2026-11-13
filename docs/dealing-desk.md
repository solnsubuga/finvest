# Dealing desk

How pooled client money becomes real Treasury bills and bonds at the custodian, how those bills are allocated back to client investments, and how the desk keeps enough cash to pay early withdrawals. The pure rules are in `backend/app/modules/dealing/domain`. Tables, commands, ledger entries and the console screen arrive in later pull requests, and this document grows with them.

The dealing desk is its own module, `dealing`. It reads what clients are owed from the investments module and never writes to its tables.

## The position

A client's investment starts when the payment is confirmed. The bill behind it is bought later, at the next auction or on the secondary market. The position shows, for each product, how much client money has no bill behind it yet.

| Figure | Meaning |
| --- | --- |
| Clients owed | The principal of every running investment in the product |
| Bills held | What was paid for the bills held for the product |
| To buy | Clients owed minus bills held, never below zero |
| On order | The face value of orders to buy that are not finished. The cost is not known until the result is. |

Both sides leave interest out. Clients are owed principal and bills are carried at cost, so a bill bought for exactly the money paid in covers it on every day of the term.

| Status | When |
| --- | --- |
| Covered | Nothing to buy and nothing left over |
| Bid needed | There is money to buy with and the orders prepared do not cover it |
| On order | The orders already prepared cover what is to buy |
| Surplus | More bills are held than clients are owed. This follows an early withdrawal paid from the buffer. New client money is covered by these bills before any more are bought. |

A surplus in one product never offsets a gap in another.

## Orders

No bill is bought or sold on one person's word.

| Step | Who | Status after |
| --- | --- | --- |
| Prepare an auction bid, a secondary market purchase or a sale | One member of staff | Prepared |
| Approve it, or send it back with a note | A different member of staff | Approved, or sent back |
| Place it with the primary dealer | Outside the system | Approved |
| Record the result from the custodian's note | Any member of staff | Result recorded, or unsuccessful if nothing was dealt |
| Confirm the result against the note, or reject it with a note | Someone other than the person who recorded it | Settled, or back to approved |

An order needs two people in all, not four. Each check needs a different person from the step it checks: the approver is not the preparer, and whoever confirms a result is not the one who recorded it. The same two people can do both pairs between them.

The preparer can withdraw an order while it waits for approval. An order is for a whole number of lots, and a sale cannot exceed the face value held.

A result carries the face value dealt, the cash paid or received, the settlement date, the maturity date and the custodian's reference. A bid can be filled in part and never above the order. A Treasury bill never costs or fetches more than its face value. A bond may. A result cannot be recorded before its settlement date.

Only a confirmed result moves money in the ledger and allocates bills.

A purchase runs no longer than the product's term, so a 182 day bill never stands behind 91 day investments. A sale is checked against the face value held twice: when it is prepared and again when its result is recorded, because holdings can change in between.

### What the database enforces

Migration 0080 adds `dealing_orders`. These hold even if application code is wrong.

| Rule | How |
| --- | --- |
| Only staff see or change orders | A row policy for the API role, and no grant at all for the worker |
| An order starts as prepared, in the name of the person signed in | A trigger compares the row with the actor of the transaction |
| Nobody approves or sends back their own order, and only the preparer withdraws it | Check constraints, and the trigger requires the decision to be signed by the person signed in |
| Whoever recorded a result cannot reject it | The same trigger |
| Steps happen in order, and a finished order never changes | The trigger allows only the listed moves from one status to the next |
| What an order is for never changes | The API role can update only the decision and result columns |
| A result is whole, no larger than the order, and matures after it settles | Check constraints |
| No order is removed | The trigger refuses DELETE and TRUNCATE for every role |
| An order is settled by someone other than whoever recorded its result | The trigger, and a check constraint (migration 0081) |
| A settled order has exactly the ledger entry and the bills its result shows | The trigger checks the entry's source, date and `bank.trust` and `dealing.bills` lines, and the lot or sale lines, before the order may be marked settled |

Every step writes an audit event in the same transaction.

The smallest step an order accepts is UGX 100,000 or $1,000, a working value until the primary dealer confirms it. A sale is refused for more face value than the desk holds.

### How much to bid for

The desk bids in face value, and what it must cover is cost. `face_for_cost` turns the money to place into the face value to bid for at an expected price, rounded up to a whole lot, because a bid one lot short leaves client money without a bill.

## Allocation

Bills are bought for the pool. After a purchase settles, its cost is shared out to the investments waiting for a bill, oldest payment first. Each is covered in full before the next gets anything, so only the last one served can be part covered. Two payments at the same instant are ordered by investment ID. Cost with no investment behind it stays with the desk and covers the next client money that comes in.

An allocation records which bills stand behind which investment. It never changes what a client is owed. That is the ledger's job.

Every 15 minutes the worker runs `dealing.allocate` (`dealing.allocations`, migration 0085). For each product it:

1. Releases what stands behind an investment that is no longer running, or beyond what the investment is owed, newest first.
2. Releases what a lot no longer holds after a sale, newest first, so the oldest payments keep their bills.
3. Shares out what each lot still holds, oldest purchase first, with the allocation rule above.

A lot counts only once its purchase is settled, and until it matures. A run repeated, or two at once, ends with the same allocations. `allocations.bills_behind(session, investment_id)` lists the lots behind an investment, for the allocation report.

The database holds that only the worker allocates, that a lot never has more allocated than the cost it still holds, and that an allocation is only ever released, once, and never removed.

## Bills held

A lot is one purchase: face value, cost and maturity date. Nothing is revalued day to day.

| Event | What happens |
| --- | --- |
| Sale of part of a lot | Cost leaves in proportion to the face sold, rounded down. The result is the proceeds minus that cost, and can be a loss. |
| Sale of all that is left | Every remaining shilling of cost leaves, so the parts always add up to what was paid |
| Maturity | The issuer pays the face value. The income is face value minus cost. Refused before the maturity date. |

The maturity of a client's investment, with rollover and payout, belongs to the investments module. This module handles only the maturity of the bills at the custodian.

### Settling an order

Confirming a recorded result settles the order in one transaction: the journal entry, the lot bought or the parts of lots a sale took, and the order marked settled. Migration 0081 adds `dealing_lots` and `dealing_lot_sales`.

| Order | Debit | Credit |
| --- | --- | --- |
| Purchase | `dealing.bills`, the cost | `bank.trust`, the cost |
| Sale | `bank.trust`, the proceeds | `dealing.bills`, the cost released |
| | `dealing.sale_losses`, any shortfall | `dealing.sale_gains`, any excess |

The entry is dated on the settlement date, points back at the order (`dealing.order`), and has a fixed idempotency key made from the order's ID. A purchase the trust bank account cannot pay for is refused, and so is a settlement date in a closed day.

A sale names the maturity date of the bills sold, on the custodian's note. It takes face value from the lots of that product and maturity, oldest purchase first. A loss on a sale is booked as Finvest's own expense until the owner decides who bears it (see the open question in [decisions.md](decisions.md)).

What the database holds for bills:

| Rule | How |
| --- | --- |
| Lots and sale lines are written only while a second person settles their order | A trigger on both tables checks the order and the person signed in |
| A lot is exactly what its purchase recorded | The same trigger compares every figure |
| A sale takes bills of the instrument it recorded, and never more than a lot still holds | The same trigger, under a two-key advisory lock on the lot, so two sales at once are checked one after the other |
| Nothing about bills held is changed or removed | The trigger refuses UPDATE, DELETE and TRUNCATE for every role |

### Bills paid at maturity

Every hour the worker runs `dealing.record_maturities`. For each lot whose maturity date has come and that still holds bills, it posts the face value left into the trust bank account and records the maturity in `dealing_lot_maturities` (migration 0082).

| Debit | Credit |
| --- | --- |
| `bank.trust`, the face value left | `dealing.bills`, the cost left |
| `dealing.maturity_losses`, for a bond bought above face | `dealing.bill_income`, face value above cost |

The entry is dated on the maturity date even when the run is late, and points back at the lot (`dealing.lot`). It is posted by the worker, as Finvest itself, and never by staff. Reconciliation then expects the money on that day's bank statement, so a payment that never came shows up there. Each lot is posted in its own savepoint. A lot whose maturity date is in a closed day is left out and logged as `dealing_maturity_day_closed` with the lot's ID, and the other lots are still posted. Closed days never reopen, so it needs a person, and there is no tool for it yet: an engineer and finance decide how to book it, and the run keeps logging the lot every hour until then. It can only happen when a day is closed before the run posts that day's maturities, which the hourly run makes unlikely. A command for finance to record such a maturity on the first open day, with a second person approving, is an open item in `docs/decisions.md`.

The database holds that only the worker records a maturity, that it is exactly what the lot still held on its maturity date with a matching ledger entry, that a sale never takes bills that have matured, and that a maturity is never changed or removed.

`dealing.holdings` answers what is held: `lots_of` for one instrument, `face_held`, `holdings_on(session, on)` for each instrument at the end of a day, and `expected_bank_movements(session, date_from=, date_to=)` for the day close, which covers purchases, sales and maturities. A lot counts from its settlement date until the day before it matures. The worker can read all of these.

## The liquidity buffer

Bills cannot be turned into cash the same day, so the desk keeps a buffer of cash.

The buffer is not a separate bank account and it is not a wallet. It is the cash the desk controls, minus the client money waiting to be placed, minus early withdrawals already promised from it. Client money is counted first. If the cash does not even cover the money waiting to be placed, the buffer is zero and the difference is shown as short.

### Paying an early withdrawal

Both the buffer and secondary market sales fund early withdrawals. One withdrawal uses one source and is never split.

| Withdrawal | Buffer | Funded |
| --- | --- | --- |
| Up to the buffer amount for one withdrawal | On or above the floor | From the buffer, at once |
| Up to that amount | Below the floor | Queued in the order asked until the buffer is back above the floor. The desk is alerted. |
| Above that amount | Any | By a sale on the secondary market. The client is paid within the promised working days. |

When the buffer pays, the bills behind that investment stay with the desk as a surplus, and the next client money takes them over. That is how the buffer refills without a sale.

### How a withdrawal is funded

`dealing.withdrawals` (migration 0084) records how each early withdrawal is funded, once per investment.

1. Investing hands the withdrawal over: the client's money is already in their payout pending account, and it publishes `investments.withdrawn_early` with `investment_id`, `currency` and `amount_minor`, naming the consumer `dealing.events.FUND_EARLY_WITHDRAWAL`.
2. The worker reads the buffer and decides, one decision per currency at a time. The buffer it reads already counts this withdrawal as owed, so the buffer before it is that figure plus the withdrawal. Up to the buffer's limit with the buffer before it on or above the floor and nobody queued ahead, it is funded at once. Up to the limit otherwise, it queues. Above the limit, it waits for a sale.
3. Every 15 minutes the worker funds the queue in the order asked, while the buffer allows, and stops at the first it cannot pay so nobody goes ahead of an earlier request.
4. A member of staff funds the withdrawals waiting for a sale from a settled secondary sale, in the order asked, up to what the sale raised and no withdrawal has used yet.

A funded withdrawal is announced as `dealing.withdrawal_funded` with `funding_id`, `investment_id`, `source` (`buffer` or `sale`), `amount_minor` and `currency`, and investing pays the client on it.

The buffer is read from the ledger with `ledger.statements.total_balance`:

| Figure | Accounts |
| --- | --- |
| Cash | `bank.trust` and `iotec.wallet` |
| Owed from that cash | `client.awaiting_start`, `client.payout_pending`, `client.payout_in_flight`, `tax.withholding_payable` |
| To place | For each product, the principal of running investments less the cost of the bills held, never below zero |

`dealing.withdrawals.buffer_now` gives these figures with the level, what waits for the buffer and for a sale, and what to raise by selling.

The database holds these rules whatever the code does. Only the worker records a funding and funds one from the buffer. A withdrawal waiting for a sale is funded by the person signed in, from a settled sale in its currency, and the withdrawals funded from one sale never add up to more than it raised. A funding is never removed, and once funded never changes.

### The rules

| Rule | Starting value |
| --- | --- |
| Target | UGX 150,000,000 |
| Floor | UGX 100,000,000 |
| From the buffer, one withdrawal | Up to UGX 5,000,000 |
| A client waiting for a sale is paid within | 2 working days |

The values are from the design canvas and are awaiting the owner's confirmation. The rules are versioned by effective date, and each version is proposed by one member of staff and approved by another. One withdrawal from the buffer can never be set above the floor, so a buffer on its floor always holds enough for one. No version in force is an error, never "no buffer needed".

### Changing the rules

A change is a new version, never an edit. Migration 0083 seeds the shilling rules above from 1 January 2026, and `dealing.buffer` saves every later version.

1. A member of staff proposes the four values, the day they take effect and a reason. The day is later than today, so the rules in force today never change under the desk. A version that repeats the one before it, or starts on a day another version already starts, is refused.
2. A second member of staff approves it or sends it back with a note. The proposer can withdraw it while nobody has decided. Approval checks the day and the clashes again, because days may pass and other versions may be approved in between.
3. Approval saves the version in the same transaction. It is never changed or removed.

Only one change per currency waits at a time, so two people never approve conflicting versions side by side. Every step is in the audit trail under `dealing_buffer_rules_proposal`.

The database holds these rules whatever the code does. Nobody approves or sends back their own change, a decided proposal never changes, and a version is saved only for an approved proposal, by the person who approved it, with exactly its values. The worker can read the rules, because it funds early withdrawals, and clients cannot.

## How it joins the other modules

Agreed with the investing, and the payouts and reconciliation, threads on 13 November 2026. The reconciliation part is built. The rest is not yet.

- **Investments.** The desk reads `investments.service.owed_by_product`, `running_investments` and `maturing`. It keeps allocations in its own tables, keyed by investment ID, and follows `investments.started`, `investments.matured`, `investments.rolled_over` and `investments.withdrawn_early`.
- **Early withdrawal.** Investments owns the request, the pricing and the client's ledger entries. It names `dealing.events.FUND_EARLY_WITHDRAWAL` as a consumer of `investments.withdrawn_early`, and creates the payout when the desk reports `dealing.withdrawal_funded`, which for a withdrawal the buffer pays at once follows within the same job.
- **Payouts.** Payouts knows nothing about the buffer. The desk's cash is in the same trust bank account that payouts leave from.
- **Reconciliation.** Every purchase, sale and maturity is a ledger entry on `bank.trust`, effective on its value date. The desk exposes `dealing.holdings.holdings_on(session, on)`, with face value, cost and custodian references for each instrument in minor units, and `dealing.holdings.expected_bank_movements(session, date_from=, date_to=)` so the day close can match the bank statement and the custodian statement.

## Over HTTP

`app/modules/dealing/api.py`. Seeing the desk needs `dealing_desk.view`, and every command needs `dealing_orders.change` as well, since a command can settle an order into the ledger. A reader without it sees no actions on any order. Every command takes an `Idempotency-Key`: a repeat of the same request is answered from its saved outcome, so a retry never prepares, approves or settles twice, and the same key for a different request is refused. Every command answers with the desk as it now stands.

| Route | What it does |
| --- | --- |
| `GET /staff/dealing/desk?currency=UGX` | The position of each product, the buffer, the early withdrawals waiting, and the open orders with the steps the reader may take on each (`allowed`) |
| `POST /staff/dealing/orders` | Prepare an order |
| `POST /staff/dealing/orders/{id}/approve` | Approve, as the second person |
| `POST /staff/dealing/orders/{id}/send-back` | Send back with a note |
| `POST /staff/dealing/orders/{id}/withdraw` | Withdraw, by whoever prepared it |
| `POST /staff/dealing/orders/{id}/result` | Record the custodian's note, or a fill of null for nothing dealt |
| `POST /staff/dealing/orders/{id}/confirm-result` | Confirm, as a person other than the recorder, which settles the order |
| `POST /staff/dealing/orders/{id}/reject-result` | Reject a recorded result with a note |

The audit event for a recorded result carries its figures, so a result later rejected can still be read.

## Not built yet

HTTP routes for allocations, the buffer rules changes, funding withdrawals from a sale, the custodian statement comparison and the allocation report. The console screen runs on sample data until the routes exist.
