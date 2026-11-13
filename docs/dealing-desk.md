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
| On order | The expected cost of orders to buy that are prepared or approved and not yet settled |

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

### How much to bid for

The desk bids in face value, and what it must cover is cost. `face_for_cost` turns the money to place into the face value to bid for at an expected price, rounded up to a whole lot, because a bid one lot short leaves client money without a bill.

## Allocation

Bills are bought for the pool. After a purchase settles, its cost is shared out to the investments waiting for a bill, oldest payment first. Each is covered in full before the next gets anything, so only the last one served can be part covered. Two payments at the same instant are ordered by investment ID. Cost with no investment behind it stays with the desk and covers the next client money that comes in.

An allocation records which bills stand behind which investment. It never changes what a client is owed. That is the ledger's job.

## Bills held

A lot is one purchase: face value, cost and maturity date. Nothing is revalued day to day.

| Event | What happens |
| --- | --- |
| Sale of part of a lot | Cost leaves in proportion to the face sold, rounded down. The result is the proceeds minus that cost, and can be a loss. |
| Sale of all that is left | Every remaining shilling of cost leaves, so the parts always add up to what was paid |
| Maturity | The issuer pays the face value. The income is face value minus cost. Refused before the maturity date. |

The maturity of a client's investment, with rollover and payout, belongs to the investments module. This module handles only the maturity of the bills at the custodian.

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

### The rules

| Rule | Starting value |
| --- | --- |
| Target | UGX 150,000,000 |
| Floor | UGX 100,000,000 |
| From the buffer, one withdrawal | Up to UGX 5,000,000 |
| A client waiting for a sale is paid within | 2 working days |

The values are from the design canvas and are awaiting the owner's confirmation. The rules are versioned by effective date, and each version is proposed by one member of staff and approved by another. One withdrawal from the buffer can never be set above the floor, so a buffer on its floor always holds enough for one. No version in force is an error, never "no buffer needed".

## How it joins the other modules

Agreed with the investing, and the payouts and reconciliation, threads on 13 November 2026. None of it is built yet.

- **Investments.** The desk reads `investments.service.owed_by_product`, `running_investments` and `maturing`. It keeps allocations in its own tables, keyed by investment ID, and follows `investments.started`, `investments.matured`, `investments.rolled_over` and `investments.withdrawn_early`.
- **Early withdrawal.** Investments owns the request, the pricing and the client's ledger entries. It calls `dealing.service.fund_early_withdrawal`, and creates the payout at once or when the desk reports `dealing.withdrawal_funded`.
- **Payouts.** Payouts knows nothing about the buffer. The desk's cash is in the same trust bank account that payouts leave from.
- **Reconciliation.** Every purchase, sale and maturity is a ledger entry on `bank.trust`, effective on its value date. The desk exposes `dealing.service.holdings_on(date)`, with face value and custodian references for each instrument, and `dealing.service.expected_bank_movements(date_from, date_to)` so the day close can match the bank statement and the custodian statement.

## Not built yet

Tables, the order commands, the ledger entries for purchases, sales and maturities, the two-person change to the buffer rules, the custodian statement comparison, the allocation report, and the console screen.
