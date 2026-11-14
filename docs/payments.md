# Payments in

How a client pays for an investment or a goal. The code is in `backend/app/modules/payments`, and the database side is migrations 0020 to 0022. Payouts are in `backend/app/modules/payouts`.

There is no wallet. Every deposit names the investment or goal it pays for, and the money it brings sits in a ledger account for that purpose until the investment starts.

**Status:** mobile money deposits work end to end against a sandbox provider. Bank transfers work end to end in the service layer: receipt, staff check against an imported bank statement, and posting. The client and console routes, receipt file storage and the correction commands arrive in their own pull requests.

## A mobile money deposit, step by step

1. The client asks to pay. `request_deposit` saves the deposit as `created` and a job to send it, in the client's own transaction, under the client's idempotency key. Nothing is sent yet.
2. The worker marks the deposit `sending` and commits that. Only then does it ask the provider to prompt the payer, with no transaction open.
3. The provider accepts, and the deposit is `awaiting_approval`. The payer approves on their phone.
4. The worker reads the status back from the provider. When the provider's own record says the money arrived, for exactly the amount asked, the deposit becomes `confirmed` and the ledger entry is posted in the same transaction.
5. A `payments.deposit_confirmed` event is published once, carrying the business day of the confirmation. The `investments.start_from_deposit` job starts the investment from it ([investing.md](investing.md)). A saving towards a goal is an investment of its own, so it is paid for and started the same way ([goals.md](goals.md)). Screening's transaction monitoring reads it too (`screening.monitor_deposit`, [screening.md](screening.md#transaction-monitoring)).

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
| `GET /client/investments/{id}/bank-transfer-details` | Where to send a bank transfer: `bank_name`, `account_name`, `account_number`, `branch` (may be null), `reference` and `amount`. The `reference` is the client's Finvest account number, for the client to write on the transfer so staff can find the line on the statement. The same refusals as paying: `404` if it is not the client's, `409` if it can no longer be paid or already has a payment (fetch that one). `503` if no account is set for the investment's currency. The account is the one staff last approved on the console's Settings screen (see [The account clients pay into](#the-account-clients-pay-into)). |
| `GET /client/investments/{id}/payment` | The investment's payment that has not failed. A `409` from either `POST` means one exists: show this one instead of asking the client to pay again. |
| `GET /client/payments` | The client's own payments, newest first, 50 a page, for the Activity list. Each is the same shape as below without its `receipt`. Pass `next_before` back as `before` for the next page; it is null on the last. |
| `GET /client/payments/{id}` | One payment, the client's own only. |

Every payment leaves a trail in the audit log. A client asking to pay, by mobile money or by bank receipt, writes `payments.deposit_requested` as the client, with the method. When the deposit settles, the system writes `payments.deposit_confirmed` or `payments.deposit_failed`, with the method, what settled it (such as `status_read` or `staff_review`) and, for a failure, the failure code. Amounts and phone numbers are not put in these events: the deposit holds them.


Seeing transfers needs `reconciliation.view`. Matching, refusing and approving need `bank_transfers.change`, because they confirm money. Admin, operations and finance hold both for now. Opening a transfer writes a `payments.bank_transfer_opened` access event to the audit trail, because the receipt and the bank's narrative can name the client and the sender.

| Route | What it does |
| --- | --- |
| `GET /staff/payments/bank-transfers` | Transfers in review, newest first, 50 a page (`?before=`). Each item says what it is `awaiting`: `check` (not reviewed yet), `second_person` (matched, waiting for a different person to approve) or `posting` (approved or refused, the worker is settling it). The list is not audited: it shows no receipt link or statement narrative. |
| `GET /staff/payments/bank-transfers/{id}` | The transfer, its receipt details, its review, and the statement lines that could be it. Once matched, `matched_line` is the chosen line (amount, value date, reference and narrative), so the second person checks it beside the receipt rather than approving blind. `receipt_link` opens the receipt file inline for 5 minutes. It is never logged. The link is the main reason opening a transfer writes `payments.bank_transfer_opened`: whoever saw the receipt is on the audit trail. |
| `POST /staff/payments/bank-transfers/{id}/review` (`bank_transfers.change`) | `{"outcome": "matched", "statement_line_id": …}` or `{"outcome": "not_matched", "reason": …}`. |
| `POST /staff/payments/bank-transfers/{id}/approve` (`bank_transfers.change`) | A second member of staff approves a match. The matcher gets `403`. |

Moving a transfer credited to the wrong client (see [below](#a-transfer-credited-to-the-wrong-client)). Seeing needs `reconciliation.view`. The `POST`s need `deposit_reassignments.change`, held by admin, operations and finance, and an `Idempotency-Key`. Opening one writes `payments.reassignment_opened` to the audit trail, because both clients are named and the narrative can name the sender.

| Route | What it does |
| --- | --- |
| `GET /staff/payments/reassignments` | Newest first, at most 200. `?status=` and `?deposit_id=`, which finds those from or to a payment. |
| `GET /staff/payments/reassignments/{id}` | The reassignment, both clients, the statement line, whether its reference names either client, `refusal` (why approving would be refused now, for an open one), `can_decide` and `yours`. |
| `POST /staff/payments/reassignments` | `{"deposit_id", "to_investment_id", "reason"}`. `422` with a code if the move could not happen now: `mobile_money_refund_instead`, `not_confirmed`, `not_for_an_investment`, `not_matched_to_a_line`, `money_moved_on`, `same_client`, `not_waiting_for_payment`, `amount_differs_from_investment`, `already_has_a_payment` or `not_found`. `409` if either already has an open proposal. |
| `POST /staff/payments/reassignments/{id}/approve` | A second member of staff approves, and the money moves. Needs a second step from the last ten minutes (`403 confirm_it_is_you`). The proposer gets `403 forbidden`. `422` with a code if anything changed. |
| `POST /staff/payments/reassignments/{id}/send-back` | `{"note"}`. A second member of staff declines it. |
| `POST /staff/payments/reassignments/{id}/withdraw` | The proposer takes it back. |

## Ledger entries

| Event | Debit | Credit |
| --- | --- | --- |
| Deposit confirmed (`payment.deposit_confirmed`) | Asset `<provider>.wallet`, reference `mtn` or `airtel`. With ioTec this is `iotec.wallet`. | Liability `client.awaiting_start`, owned by the client, reference the investment or goal ID |
| Transfer moved to the right client (`payment.deposit_reassigned`) | Liability `client.awaiting_start`, first client, reference the first investment | Liability `client.awaiting_start`, right client, reference their investment |
| Bank transfer confirmed (`payment.deposit_confirmed`) | Asset `bank.trust`. For the trust account (statement account reference `trust`) it is the reconciled pot's own account, with no reference, the one the day close checks and dealing pays from. A statement for any other bank account gets `bank.trust` with that account reference. | Liability `client.awaiting_start`, as above |

The entry's idempotency key is `deposit:<deposit ID>` and its source is the deposit, so a deposit can only ever be posted once. Payouts leave from the same wallet account, which lets reconciliation tie one ledger account to one provider statement.

If the provider confirms a different amount from the one asked for, nothing is posted. The deposit is held as `uncertain` with `amount_mismatch` for staff, because an overpayment or an unmatched deposit is refunded or matched by staff and never kept as credit.

## The account clients pay into

Staff set the bank account clients send transfers to on the console's Settings screen, one per currency (`bank_accounts.py`, migration 0260). It used to be the `FINVEST_BANK_TRANSFER_ACCOUNTS` setting of each deploy, which nothing reads any more. An account changed by one person alone could send every client's transfer somewhere else, so a change takes two people: one proposes the details with a reason, a different one approves or rejects them, and only the proposer can withdraw. Until a proposal is approved, clients keep being shown the account in use, or none: with no account approved for a currency, `bank-transfer-details` answers `503` and the app offers mobile money. The account must be the bank pot whose statement reconciliation imports, or a transfer can never be matched; the person approving checks that.

Seeing the account needs `settings.view`. Proposing and deciding need `bank_accounts.change`, held by admin and finance for now (finance also gains `settings.view` to open the screen). Deciding also needs a second step from the last few minutes. Each proposal and decision writes `payments.bank_account_proposed`, `_approved`, `_rejected` or `_withdrawn` to the audit log, naming the currency only: the details are on the row, which never changes. A waiting change shows in the approvals inbox as `bank_account_change`.

| Route | What it does |
| --- | --- |
| `GET /staff/payments/bank-accounts` | Each currency with its account `in_use` (null if none), `changed_at` and any `proposal` waiting, marked `yours` for the proposer. |
| `POST /staff/payments/bank-accounts/{currency}/proposals` (`bank_accounts.change`) | `{"account": {"bank_name", "account_name", "account_number", "branch"}, "reason"}`. The number is 6 to 20 digits; spaces and dashes are taken out. `409` if a change for that currency is already waiting. |
| `POST /staff/payments/bank-accounts/proposals/{id}/decision` (`bank_accounts.change`) | `{"decision": "approved", "rejected" or "withdrawn", "reason"}`. The proposer can only withdraw, and anyone else can only approve or reject (`403`). |

The database enforces the same rules: only staff write the table and only as themselves, at most one proposal waits per currency, a decided row never changes, the two-person rule is a check constraint, and no role can delete a row. A client reads only approved rows.

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

**The money is still in `client.awaiting_start`.** The deposit is reversed, by two people (`reversals.py`, `domain/reversals.py`, `reversal_routes.py`, migration 0261). If the investment started, it is unwound first through investments corrections, which puts the principal back there.

1. **Propose.** One member of staff names the confirmed deposit and a reason (`POST /staff/payments/reversals`). The whole amount must still wait for the investment, or it is refused with `money_moved_on`. A deposit saved by a reassignment is refused with `not_a_payment`: it is put right by reassigning it. Nothing moves.
2. **Approve.** A different member of staff approves, with a passkey or app code from the last ten minutes, and the worker's job is queued in that transaction. Or the second person sends it back with a note, or the proposer withdraws it. It shows in the approvals inbox as `deposit_reversal`.
3. **The worker** takes the investment's row lock, the one the start of an investment holds while it reads the money waiting, so a start and a reversal never interleave. It checks everything again and posts the exact reversal of the deposit's entry:

| Debit | Credit |
| --- | --- |
| Liability `client.awaiting_start`, the client, reference the investment | The wallet or bank account the deposit's entry debited |

The entry is `payment.deposit_reversed`, source `payment.deposit_reversal` and the reversal's ID, key `deposit-reversal:<ID>`, and it points at the deposit's entry as the one it reverses. In the same transaction the deposit's `reversal_id` is set. The deposit stays `confirmed`, because it was, but it no longer counts as the investment's payment: `has_live_deposit` is false and the one-payment-per-investment index leaves it out. So the client can pay for the investment again, or the sweep cancels it as unpaid at its pay-by time. If the money moved in between, the reversal is marked `failed` with a code and nothing moves; staff propose again once it is back.

Seeing reversals needs `reconciliation.view`. Proposing and deciding need `deposit_reassignments.change`, the permission for putting a wrongly credited payment right. The database holds each person to their part: only the proposer withdraws, nobody decides their own, only the worker records the outcome, a done reversal must carry its own exact reversing entry, and a deposit's `reversal_id` is set once, by the worker, to a done reversal of it. Payments never writes to investments.

The client is not told by message yet, and a start job queued by the original confirmation, if it runs after the reversal, fails for want of money and shows among the failed jobs.

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

### A transfer credited to the wrong client

Sometimes the money is real and on the statement, but it belongs to someone else: a staff member matched one client's receipt to a statement line that was really another client's payment of the same amount, and a second person approved it. Nothing needs to be recovered. The money is moved to the client whose money it was (`reassignments.py`, `domain/reassignments.py`, migration 0171).

1. **Undo first, if needed.** The whole amount must still be waiting in the first client's `client.awaiting_start` for the first investment. If that investment started, it is undone first, by two people, through investments corrections ([investing.md](investing.md)), which puts the principal back there. If the money has gone, for example refunded or paid out, a reassignment is refused with `money_moved_on`, and the receivable above is used instead.
2. **Propose.** One member of staff names the deposit, the right client's investment and a reason. The investment must be another client's, waiting for payment, for exactly the deposit's amount, with no payment of its own. The evidence is the statement line the original review matched, recorded with the proposal. Nothing moves.
3. **Approve.** A different member of staff checks the detail: both clients' names and Finvest account numbers, the statement line (amount, value date, reference, narrative) and whether the line's reference carries either client's account number, which clients are asked to write on a transfer. Approving needs a passkey or app code from the last ten minutes. In that same transaction every check is made again and the money moves:

| Debit | Credit |
| --- | --- |
| Liability `client.awaiting_start`, first client, reference the first investment | Liability `client.awaiting_start`, right client, reference their investment |

The entry is `payment.deposit_reassigned`, source `payment.reassignment` and the reassignment's ID, key `deposit-reassignment:<ID>`. A new confirmed bank transfer deposit is saved for the right client's investment, pointing at that entry, with no receipt of its own. `payments.deposit_confirmed` is published for it, so the investment starts and the client gets a receipt, exactly as for any other payment. The audit trail has `payments.deposit_confirmed` for it in the approver's name, with the cause `reassignment`. If anything changed since the proposal, approving is refused with a code and the proposal stays open.

4. **Or not.** The second person can send it back with a note, or the proposer can withdraw it. Nothing moves either way.

Choices in this design:

- **Mobile money is never reassigned.** The provider took it from the first client's own number. Crediting it to anyone else would accept a payment from a number registered to someone else, which the owner's rule refuses. It is refunded to the number it came from.
- **The whole deposit, to an investment waiting for exactly that amount.** No part moves and no money is kept as credit, because there is no wallet.
- **Moved in the approval's transaction.** So there is never a reassignment that was approved and has not moved, and the day close needs nothing new: the move is between two client accounts, and the bank pot is untouched.
- **The original is never changed.** The first deposit stays confirmed, its review keeps its line, and its entry stands. A reassignment that was itself a mistake is put right by reassigning the new deposit, on the same evidence.
- **On the client's statement**, the right client sees "Payment received, Bank transfer". The first client sees a correction. Neither sees the other.

The database enforces:

- A reassignment starts as proposed, by the staff member signed in, for a confirmed bank transfer of exactly its amount, for another client, citing the line its approved review matched (or, for a deposit saved by a reassignment, the line that reassignment cited).
- Only the proposer withdraws it, and only a different member of staff applies or sends it back.
- It is applied only with its own `payment.deposit_reassigned` entry, which moves exactly the amount between exactly those two accounts. When the transaction commits, the new deposit exists.
- A deposit, and a target investment, have at most one open or applied reassignment.
- A deposit saved in a staff transaction is only ever the new deposit of an applied reassignment, as that reassignment says.
- Nothing is deleted, and a decided reassignment never changes. Clients cannot read reassignments.

`reassignments.reassignments_for_line(session, statement_line_id)` lists the reassignments citing a line, for reconciliation's screens.

## The provider

ioTec Pay is the chosen provider, and there is a sandbox that moves no money for development. Any adapter implements `CollectionsProvider` in `domain/provider.py`: request a collection, read its status, and check and parse a callback. An adapter sorts every failure into rejected, deferred or uncertain and never raises. If one does raise, the deposit is treated as uncertain.

`FINVEST_PAYMENTS_PROVIDER` chooses the adapter: `disabled`, `sandbox` or `iotec`. The default is `disabled`: no deposit can be sent and the callback address answers 404. `sandbox` is set by the local Compose stack. The sandbox confirms payments that never happened, so the API, the worker and the scheduler refuse to start with it unless `FINVEST_ENVIRONMENT` is unset (local development and tests), `local` or `staging`. Any other value, including a name we do not know, is treated as production. In the sandbox, the last two digits of the payer's number choose the result:

| Number ends in | Result |
| --- | --- |
| 01 | The payer declines |
| 02 | Not enough money |
| 03 | The payer never answers |
| 04 | No answer to the request, and no record of it (uncertain) |
| 05 | The provider is busy (deferred) |
| 06 | Confirmed for a different amount |
| Anything else | Approved after six seconds |

### ioTec Pay

`providers/iotec.py` follows ioTec's published API (https://iotec.io/api-docs/pay).

- **Token.** It authenticates with a client credentials token from `id.iotec.io`. The token is cached in `app.infrastructure.iotec`, shared with payouts, and refreshed 30 seconds before it expires.
- **Collecting.** A collection is `POST /api/collections/collect` with:
  - `category` `MobileMoney`
  - the payer's number as `256…`
  - the amount in whole shillings
  - the deposit ID as `externalId`
  - `transactionChargesCategory` `ChargeWallet`, so the client pays exactly the investment's amount and ioTec's charge falls on our wallet. That is decided for now ([decisions.md](decisions.md)). Nothing posts the charge yet, so `iotec.wallet` runs ahead of ioTec's balance by the charges until how it is booked is decided (an open question there).
- **Reading the status.** The status is read by ioTec's ID, or by the deposit ID when the answer to the send was lost.

How a send ends:

| What happened | Result |
| --- | --- |
| No token, or the connection never opened | Deferred: nothing was sent |
| 401 or 403 (after one retry with a new token), 429 | Deferred |
| 400 or 404 | Rejected (`other`) |
| 200, `Failed`, `Rejected`, `Cancelled` or `RolledBack` | Rejected, with ioTec's ID |
| 200, any other known status | Accepted |
| A timeout or a dropped connection after sending, a 5xx, an answer we cannot read | Uncertain, never sent again |

A status read turns:

- `Success` into `Confirmed`, for the amount ioTec reports, which must be a whole number in our currency.
- `Pending`, `SentToVendor`, `AwaitingApproval` and `Scheduled` into `Pending`.
- 404 into `NotFound`.
- Anything else into `Unavailable`, which decides nothing.

ioTec's `statusCode` is matched loosely to our rejection codes, because ioTec does not publish the list.

Callbacks go to `/provider-callbacks/collections/iotec`. In the ioTec portal, the wallet's collection callback is set with the security header `Authorization: Bearer <FINVEST_IOTEC_CALLBACK_TOKEN>`. A callback without that exact header is refused. A callback is only a hint to read the status. Each status change is its own notice, keyed `<ioTec ID>:<status>`.

Settings, from `FINVEST_IOTEC_*`:

| Setting | Meaning |
| --- | --- |
| `CLIENT_ID`, `CLIENT_SECRET` | The API credentials ioTec emailed at sign-up. A secret: GitHub environment secrets only. |
| `WALLET_ID` | The ioTec Pay wallet collections are paid into. |
| `CALLBACK_TOKEN` | The token ioTec sends on callbacks. A secret. Without it, callbacks are refused and the worker's status reads carry every payment through. |
| `CURRENCY_CODE` | `UGX`, or `ITX` for an ioTec test wallet. `ITX` is refused outside local and staging, as the sandbox is. |
| `TOKEN_URL`, `PAY_URL`, `TIMEOUT_SECONDS` | Default to `https://id.iotec.io/connect/token`, `https://pay.iotec.io` and 30 seconds. Both addresses must be `https`, or the process refuses to start. |

Which process needs which:

| Process | Needs |
| --- | --- |
| Worker | `CLIENT_ID`, `CLIENT_SECRET`, `WALLET_ID`, `CURRENCY_CODE`. It sends every request and reads every status. |
| API | The same as the worker, plus `CALLBACK_TOKEN` to accept callbacks. It builds the adapter to check callbacks, but sends no request to ioTec. |
| Scheduler | Only `CURRENCY_CODE`, for the test wallet check. It holds no ioTec credentials. |

ioTec's test numbers: `011177777x` succeed, `011177799x` fail, `011177778x` stay pending and `011177779x` stay with the network. A deposit is collected from the account's own number, so trying a test wallet needs a staging client whose number is one of these, such as `+256111777771`.

## Not built yet

- Trying the adapter against ioTec's test wallet, which needs the credentials and a staging deploy.
- Paying for a goal over HTTP. The routes take investments only.
- Provider limits and fees.
- The receivable correction commands. Moving a transfer credited to the wrong client is built.
