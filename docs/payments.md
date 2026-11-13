# Payments in

How a client pays for an investment or a goal. The code is in `backend/app/modules/payments`, and the database side is migrations 0020 to 0022. Payouts are in `backend/app/modules/payouts`.

There is no wallet. Every deposit names the investment or goal it pays for, and the money it brings sits in a ledger account for that purpose until the investment starts.

**Status:** mobile money deposits work end to end against a sandbox provider. Bank transfers work end to end in the service layer: receipt, staff check against an imported bank statement, and posting. The client and console routes, receipt file storage and the correction commands arrive in their own pull requests.

## A mobile money deposit, step by step

1. The client asks to pay. `request_deposit` saves the deposit as `created` and a job to send it, in the client's own transaction, under the client's idempotency key. Nothing is sent yet.
2. The worker marks the deposit `sending` and commits that. Only then does it ask the provider to prompt the payer, with no transaction open.
3. The provider accepts, and the deposit is `awaiting_approval`. The payer approves on their phone.
4. The worker reads the status back from the provider. When the provider's own record says the money arrived, for exactly the amount asked, the deposit becomes `confirmed` and the ledger entry is posted in the same transaction.
5. A `payments.deposit_confirmed` event is published once, carrying the business day of the confirmation. The `investments.start_from_deposit` job starts the investment from it ([investing.md](investing.md)). A saving towards a goal is an investment of its own, so it is paid for and started the same way ([goals.md](goals.md)).

Two things are refused before anything is saved:

- **A payer that is not the account's own number.** `request_deposit` is given the number on the client's account, taken from their session and never from the request, and the payer must equal it. A payment from a number registered to someone else is refused (`not_account_phone`). This is the owner's rule.
- **A second deposit for the same investment.** An investment takes one deposit. While one is being sent, awaiting approval, uncertain or confirmed, a request with a new idempotency key raises `DepositExists`, which carries the existing deposit's ID and status so the app can take the client to it. No second prompt reaches the phone. A failed deposit moved no money and frees the investment for another try. Goals are not covered, because a goal takes many deposits over its life.

Other modules ask `has_live_deposit(session, purpose=..., purpose_id=...)` from the worker before they give up on a payment. It is true while a deposit for that purpose has not failed, so an unpaid investment is not cancelled while a mobile money payment is still pending or uncertain.

| Status | Meaning |
| --- | --- |
| `created` | Saved. Nothing has reached the provider. |
| `sending` | A worker is about to call, or is calling, the provider. |
| `awaiting_approval` | The payer has the prompt. |
| `confirmed` | Money arrived and the ledger entry is posted. Final. |
| `failed` | No money moved. Final. The code says why: `declined`, `insufficient_funds`, `approval_timed_out`, `account_not_found`, `limit_exceeded`, `provider_unavailable`, `other`. |
| `uncertain` | We cannot tell. Waits for a status read or for reconciliation. The code says why: `send_uncertain`, `unknown_to_provider`, `approval_overdue`, `amount_mismatch`. |

## The three ways a provider call can end

| Outcome | What it means | What happens |
| --- | --- | --- |
| Rejected | The provider refused. No money moved. | The deposit fails with the provider's reason. |
| Deferred | The request certainly did not reach the provider's books. | It is sent again later, up to five times within eight minutes, then fails with `provider_unavailable`. |
| Uncertain | It may or may not have been taken: a timeout, a dropped connection, a provider error, or our own worker dying mid-call. | The deposit becomes `uncertain`. **It is never sent again.** The status is read back from the provider, again and again for a day (see the sweep below). If that does not settle it, reconciliation does, by calling `settle_deposit` with what the provider's record shows. |

Two things make this safe. The `sending` mark is committed before the call, and only a deposit that is still `created` is ever sent. And the send job is registered with the queue as having an external effect, so the queue never runs it again after a crash or a lost lease. A deposit left with the mark is found by the sweep below.

## Callbacks are hints

The provider calls `POST /provider-callbacks/collections/{provider}`. The route checks the signature, records the callback once, and queues a job that reads the real status from the provider. A callback never changes a deposit, and the route cannot read deposits at all. A callback that says "paid" while the provider's record says "pending" changes nothing.

## What stops a deposit getting stuck

A sweep runs every minute and picks up deposits whose own job was lost:

| Found | Action |
| --- | --- |
| `created` for ten minutes | Fails with `provider_unavailable`. It was never marked `sending`, so it was never sent. |
| `sending` for two minutes | Becomes `uncertain`, and a status read is queued. |
| `awaiting_approval` and quiet for two minutes | A status read is queued. After fifteen minutes with no result it becomes `uncertain` with `approval_overdue`. |
| `uncertain`, for up to 24 hours | The next status read is queued: every minute for the first fifteen minutes, every ten minutes for two hours, then hourly. Each read is numbered and due at a fixed time, so repeated sweeps queue it once. The sweep pages through every uncertain deposit in the window, oldest first, so none waits behind newer ones. A read only asks the provider. It never sends. After 24 hours the reads stop and the deposit waits for reconciliation. A deposit held for `amount_mismatch` is not read again, because it needs staff. |

## A bank transfer, step by step

The client sends money from their own bank account, then tells us about it. The code is in `bank_transfers.py` and `domain/bank_transfers.py`.

1. **Receipt.** The client sends the receipt file's type, size and SHA-256, the amount, the day they sent it, their bank and the bank's transaction number (`submit_bank_receipt`, with an idempotency key). The deposit is saved as `in_review` in the same table as mobile money deposits, with method `bank_transfer`, provider `bank` and no payer number, so the one live deposit per investment rule covers both ways to pay. The API stores the file in private storage under `payments/receipts/<deposit ID>` before the transaction opens. The database keeps its key, type, size and digest. Nothing the client says moves money.
2. **Staff check.** A staff member compares the receipt with the bank statement that reconciliation imported (`reconciliation.statements`). `candidate_lines` lists the lines that could be this transfer: money in, for exactly the amount owed, valued from the day the client gave to seven days later, and not already used. The staff member records either the line (`Matched`) or a reason (`NotMatched`: `amount_differs`, `not_on_statement`, `unreadable`, `name_differs`, `other`). The review is checked again against the statement when it is saved, is audited, and changes no deposit and no balance. A refusal queues the job that settles it. A match waits for a second member of staff, who approves it (`approve_bank_match`). The line is checked again, the person who matched it cannot approve it, and the approval is audited and queues the job.
3. **Settling.** The worker confirms an approved match and posts it, or fails an unmatched one with the review's reason. A failed transfer frees the investment, so the client can send a new receipt as a new deposit.

Choices in this design:

- **Two people confirm a transfer, one can refuse it.** Sensitive actions need two people (`docs/rules.md`). One person alone could match a transfer to another client's payment of the same amount, so a match needs a second member of staff. Refusing moves no money and needs one. The owner decided this (see [decisions.md](decisions.md)).
- **Only an exact amount is confirmed.** A transfer for any other amount is refused as `amount_differs` and the money is returned or matched by staff, as with a mobile money amount mismatch. No part payment is posted.
- **The statement reference is not required to match.** Banks often cut or change the reference, so staff decide from the receipt, the amount, the date and the sender's name. A transfer from an account in another name is refused as `name_differs`.

## Routes

Client routes, signed in as the client. Each `POST` needs an `Idempotency-Key`.

| Route | What it does |
| --- | --- |
| `POST /client/investments/{id}/payments/mobile-money` | `{"network": "mtn" or "airtel"}`. Prompts the number on the client's own account for exactly the investment's amount. The request cannot name a number or an amount. |
| `POST /client/investments/{id}/payments/bank-transfer` | A `multipart/form-data` body: the `receipt` file (PDF, JPEG or PNG, at most 10 MiB), `amount_minor`, `sent_on`, `bank_name` and `transaction_number`. The amount must be the investment's, or it is refused with `amount_differs_from_investment`. First, `preflight_bank_receipt` runs the whole submission in a savepoint it rolls back. That covers the details, an investment already being paid, and a reused key. A replayed key gets the earlier transfer back without storing anything. The file's first bytes must match its claimed type (PDF, JPEG or PNG), or it is refused as `content_does_not_match_type`. Only then is the file stored, outside any transaction, under `payments/receipts/<deposit ID>`, and the transfer is saved pointing at it. So a saved transfer always has its file. If saving still fails, or a simultaneous identical request saved first, the stored file is deleted. If the store cannot be reached to delete it, `receipt_orphaned` is logged with the deposit ID. If storage is down when storing, the answer is `503` and nothing is saved. The body limit for this path is 11 MiB, and the edge allows 12 MiB. |
| `GET /client/investments/{id}/payment` | The investment's payment that has not failed. A `409` from either `POST` means one exists: show this one instead of asking the client to pay again. |
| `GET /client/payments/{id}` | One payment, the client's own only. |

Every payment leaves a trail in the audit log. A client asking to pay, by mobile money or by bank receipt, writes `payments.deposit_requested` as the client, with the method. When the deposit settles, the system writes `payments.deposit_confirmed` or `payments.deposit_failed`, with the method, what settled it (such as `status_read` or `staff_review`) and, for a failure, the failure code. Amounts and phone numbers are not put in these events: the deposit holds them.


Seeing transfers needs `reconciliation.view`. Matching, refusing and approving need `bank_transfers.change`, because they confirm money. Admin, operations and finance hold both for now. Opening a transfer writes a `payments.bank_transfer_opened` access event to the audit trail, because the receipt and the bank's narrative can name the client and the sender.

| Route | What it does |
| --- | --- |
| `GET /staff/payments/bank-transfers` | Transfers in review, newest first, 50 a page (`?before=`). |
| `GET /staff/payments/bank-transfers/{id}` | The transfer, its receipt details, its review, and the statement lines that could be it. Once matched, `matched_line` is the chosen line (amount, value date, reference and narrative), so the second person checks it beside the receipt rather than approving blind. `receipt_link` opens the receipt file inline for 5 minutes. It is never logged. The link is the main reason opening a transfer writes `payments.bank_transfer_opened`: whoever saw the receipt is on the audit trail. |
| `POST /staff/payments/bank-transfers/{id}/review` (`bank_transfers.change`) | `{"outcome": "matched", "statement_line_id": …}` or `{"outcome": "not_matched", "reason": …}`. |
| `POST /staff/payments/bank-transfers/{id}/approve` (`bank_transfers.change`) | A second member of staff approves a match. The matcher gets `403`. |

## Ledger entries

| Event | Debit | Credit |
| --- | --- | --- |
| Deposit confirmed (`payment.deposit_confirmed`) | Asset `<provider>.wallet`, reference `mtn` or `airtel`. With ioTec this is `iotec.wallet`. | Liability `client.awaiting_start`, owned by the client, reference the investment or goal ID |
| Bank transfer confirmed (`payment.deposit_confirmed`) | Asset `bank.trust`. For the trust account (statement account reference `trust`) it is the reconciled pot's own account, with no reference, the one the day close checks and dealing pays from. A statement for any other bank account gets `bank.trust` with that account reference. | Liability `client.awaiting_start`, as above |

The entry's idempotency key is `deposit:<deposit ID>` and its source is the deposit, so a deposit can only ever be posted once. Payouts leave from the same wallet account, which lets reconciliation tie one ledger account to one provider statement.

If the provider confirms a different amount from the one asked for, nothing is posted. The deposit is held as `uncertain` with `amount_mismatch` for staff, because an overpayment or an unmatched deposit is refunded or matched by staff and never kept as credit.

## What the database enforces

- An investment has at most one deposit that has not failed (a partial unique index, migration 0021). The service checks first, and the index is what holds when two requests race.
- A deposit's client, purpose, method, amount and payer never change.
- A deposit moves only along the allowed path. `confirmed` and `failed` are final. A test tries every pair of states against the trigger and compares the result with the rules in `domain/deposits.py`.
- A deposit is `confirmed` exactly when it points at its ledger entry.
- The status history (`payment_deposit_events`) and the callback record (`payment_provider_notices`) are insert only for every role.
- A client transaction sees and creates only that client's deposits. The API cannot change a deposit. Only the worker can, and only its status fields.
- A bank transfer starts `in_review` and moves only to `confirmed` or `failed`. A mobile money deposit is never `in_review`. A bank transfer has no payer number and its provider is `bank`; every other deposit has a payer number (migration 0022).
- A bank transfer's receipt and its staff review are insert only for every role, one of each per deposit. A client adds a receipt only to their own transfer in review. Only staff add a review, in their own name, of a transfer in review.
- A statement line is used by one review at most.
- A match is approved once, by staff other than the person who matched it, and the approval is insert only.
- The worker confirms a bank transfer only when its review matched it to a statement line and a second person approved it, and fails one only with the review's reason. No path confirms a transfer on a reason alone.

## Correcting a mistaken credit

Decided by the owner on 13 November 2026: receivable, then recover. The original entry is never touched, and no balance goes below zero. Every step is proposed by one staff member and approved by another through the ledger's corrections ([ledger.md](ledger.md)), with a reason, and is audited.

**The money is still in `client.awaiting_start`.** Staff propose an exact reversal of the deposit entry and a second person approves it.

**The money has moved on**, for example the investment has started, so a reversal would overdraw the account. Staff propose a forward entry `payment.deposit_correction`:

| Debit | Credit |
| --- | --- |
| Asset `receivables.deposit_correction`, owned by the client, reference the deposit ID | The cash account the deposit debited, because that money never arrived |

The receivable shows that the client owes the amount. It is cleared in exactly one of three ways, each with two staff:

| How | Debit | Credit |
| --- | --- | --- |
| The investment is unwound | The client's holding | The receivable |
| The money does arrive late | The cash account | The receivable |
| It is written off | Expense `losses.deposit_correction` | The receivable |

An open receivable is reported by the daily close until it is zero.

## The provider

ioTec Pay is the chosen provider. Its API details are not confirmed, so the only adapter today is a sandbox that moves no money. Any adapter implements `CollectionsProvider` in `domain/provider.py`: request a collection, read its status, and check and parse a callback. An adapter sorts every failure into rejected, deferred or uncertain and never raises. If one does raise, the deposit is treated as uncertain.

`FINVEST_PAYMENTS_PROVIDER` chooses the adapter. The default is `disabled`: no deposit can be sent and the callback address answers 404. `sandbox` is set by the local Compose stack. The sandbox confirms payments that never happened, so the API, the worker and the scheduler refuse to start with it unless `FINVEST_ENVIRONMENT` is unset (local development and tests), `local` or `staging`. Any other value, including a name we do not know, is treated as production. In the sandbox, the last two digits of the payer's number choose the result:

| Number ends in | Result |
| --- | --- |
| 01 | The payer declines |
| 02 | Not enough money |
| 03 | The payer never answers |
| 04 | No answer to the request, and no record of it (uncertain) |
| 05 | The provider is busy (deferred) |
| 06 | Confirmed for a different amount |
| Anything else | Approved after six seconds |

## Not built yet

- The ioTec adapter, which needs ioTec's API details, callback signature scheme and sandbox access.
- Paying for a goal over HTTP. The routes take investments only.
- Provider limits and fees.
- Storing and serving receipt files (the infra thread owns the shared storage adapter), the bank details the client is shown, and the correction commands.
