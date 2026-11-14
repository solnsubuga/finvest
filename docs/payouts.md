# Payouts

How money leaves Finvest, and the rules in [rules.md](rules.md) that guard it. The pure rules are in `backend/app/modules/payouts/domain`. Tables, commands, the provider adapter and the screens arrive in later pull requests, and this document grows with them.

## Payout destinations

A client is paid only to their own mobile money number or bank account. A new destination goes through four steps, in order, and no step can be skipped:

| Step | Who | Status after |
| --- | --- | --- |
| Add it and confirm the SMS code | Client | Awaiting proof |
| Upload proof of ownership | Client | In review |
| Check the proof against the name on the ID | One member of staff | Still in review, with the check recorded, or rejected with a reason |
| Approve the check | A different member of staff | Approved |
| Wait 24 hours from the approval | Nobody | Payable |

Approving takes two people, from the owner's rule that a staff step that can have a maker and a checker has one: the first records what they read on the proof, and a second approves that check as it was recorded. Whoever recorded it cannot approve it, and a check made again replaces the one before. Refusing takes one person, because it lets no money out, and anyone may refuse, the checker included.

A rejected destination can be proved again with a better document, unless it was rejected for suspected fraud, which is final. A removed one is never used again. Staff who remove a destination must give a reason, which is recorded as an operator control.

### Proof

| Destination | Proof accepted |
| --- | --- |
| Bank account | A statement dated within the last three calendar months, a letter from the bank, or a photo of the bank card showing the name |
| Mobile money number | The name the network holds for the number, read through the payment provider |

### The name lock

The name on the proof is compared with the name on the client's ID. Case, accents, punctuation and the order of the names are ignored.

| Result | Meaning | Can staff approve? |
| --- | --- | --- |
| Exact | The same names | Yes |
| Partial | One has a name the other lacks, such as a middle name, and at least two names agree | Yes, if they judge it is the same person |
| Different | Anything else | Never |

The comparison is made on the server from the ID name and the name the staff member read on the proof. It is never taken from the request, and it never approves a destination by itself. A different name is refused outright, for a bank account and for a mobile money number alike. No member of staff and no pair of staff can override it.

### What the database enforces

Migration 0030 adds `payout_destinations`. These hold even if application code is wrong.

| Rule | How |
| --- | --- |
| Who may take which step | A trigger allows a client transaction only the client's steps, a staff transaction only check, approve, reject and remove, and the worker only the note of the first payout |
| A client cannot approve their own destination | The same trigger, and an insert policy that admits only a new row at the first step |
| A different name is never approved | An approved row must carry an exact or partial match, the staff member who checked it, and the approval time |
| A check is signed by who made it | `checked_by` must be the staff member of the transaction, and `checked_at` within five minutes of the database clock |
| Two people approve | An approval needs a recorded check, is signed `approved_by` the staff member of the transaction, who must not be `checked_by`, and cannot change the check (migration 0035) |
| New proof, new check | A client who sends new proof after a refusal must clear every field of the last check, so an old check is never approved against a new document |
| The account never changes | Owner, kind, number and holder name are fixed at creation |
| Nothing is deleted, and removed is final | Triggers refuse DELETE and TRUNCATE for every role, and any change to a removed row |
| One account, one client | An account approved for one client cannot be approved for another (migration 0032). The check is made at approval, so a client learns nothing about anyone else's accounts. Staff see `account_in_use` and look at both. |
| The approval time is real | An approval must be stamped within five minutes of the database clock, so the 24 hour wait cannot be shortened by a wrong time |
| Suspected fraud is final | A destination rejected for suspected fraud cannot be proved again, only removed |
| Clients are fenced | Row policies show a client only their own destinations |

Every step writes an audit event in the same transaction. Account numbers never enter the audit trail.

The SMS code belongs to the identity module and is checked before the code step. The ID name is read by `check` itself from the identity checks module (`kyc.verified_name`), never from the caller. A client with no approved identity check cannot have an account checked (`client_not_verified`).

### When a destination can be paid

- Not before the staff approval plus 24 hours, to the second.
- If the client signed in with an SMS or email code in place of a passkey, or reset their password, in the last 24 hours, only a destination that had already received money before that sign-in can be paid.

## Withdrawal limits

Limits are versioned by effective date. The version in force on the business day of the request applies. The business day is the Kampala calendar day (`app/shared/business_day.py`). No version in force is an error, never "no limit".

| Limit | Value at launch |
| --- | --- |
| Mobile money, one payout | UGX 5,000,000. No version may set it higher. |
| Mobile money, per day | Not set |
| Bank, one payout and per day | Not set |
| Held for staff review above | UGX 10,000,000. A working default until the owner confirms it. |

A new version is inserted by the staff member who approves it, names a different staff member as proposer, and is never edited afterwards. The launch version is seeded by migration 0031. The console screen for proposing and approving a change arrives with Settings.

An amount over the mobile money limit is refused with a code that tells the app to offer a bank account instead. Daily limits count every payout of the day that was not cancelled and did not fail. Amounts are never converted: a payout in another currency than the limits is refused.

## The life of a payout

The money leaves the client's holding when the payout is created and sits in a "payout in flight" ledger account for that client until the provider's answer is known.

| Status | Meaning |
| --- | --- |
| Held | Waiting for staff to approve a large withdrawal |
| Queued | Ready for the worker. Nothing has reached the provider. |
| Sent | The provider accepted it and has not said how it ended |
| Uncertain | We do not know whether the provider took it |
| Paid | The provider confirmed the money arrived |
| Failed | The provider confirmed it did not go |
| Cancelled | Stopped before anything was sent |

Each step into a final status carries exactly one ledger movement, posted in the same transaction:

| Into | Movement of the in-flight money |
| --- | --- |
| Paid | To the wallet or bank account it left from |
| Failed | To the client's "payout pending" account, and staff are alerted |
| Cancelled | Back where it came from |

The "payout pending" account is not a wallet. The client cannot spend it, invest from it or see it as a balance. It only holds a failed payout until staff send it again or return it to the investment, and it should be empty within a day.

Three rules are fixed in the transition table and tested for every combination of status and event:

- Only a queued payout may be sent. A send that certainly did not leave stays queued and may be tried again.
- A payout that is sent or uncertain is never sent again. Only reading its status back from the provider moves it.
- If the provider has no record of a payout and cannot answer, two staff members may settle it from the statement as paid or failed. It takes two different people, a reason, and an audit event. While the provider can answer, its answer is the one that counts.
- A finished payout never changes. A payout can be cancelled only while it is held.

## Payouts in the database

Migration 0031 adds `withdrawal_limits` and `payouts`. The commands are in `backend/app/modules/payouts/payouts.py`.

### Creating a payout

`create_payout` is called for a signed-in client, or by the worker on a client's behalf when an investment matures. Staff cannot create one. In one transaction it:

1. Takes a lock on the client, so two requests cannot both fit under the same allowance or spend the same money.
2. Returns the first payout if this `request_key` was already used for the same request, and refuses the key for a different one.
3. Checks the destination is the client's, approved, past its 24 hours and allowed under a restricted session.
4. Checks the limits in force on the business day against what already went that day.
5. Finds the client's handover account for `source_reference`, inserts the payout and posts the reserve entry.

A refusal leaves nothing behind. The codes are `destination_not_approved`, `destination_in_safety_wait`, `destination_not_known_yet`, `amount_not_positive`, `currency_not_supported`, `over_mobile_money_limit`, `over_bank_limit`, `over_daily_limit`, `source_not_allowed` and `insufficient_funds`.

A payout is funded from one place only: the client's own `client.payout_pending` account for the `source_reference` the caller names, which is the investment whose money was handed over. The caller never names a ledger account, so a payout cannot be funded from another payout's in-flight money, from the money parked after a failed payout, or from another client. `source_not_allowed` is the refusal for an empty reference.

### The entries

| Entry | Debit | Credit | Key |
| --- | --- | --- | --- |
| `payout.reserve` | `client.payout_pending`, reference the investment | `client.payout_in_flight`, reference the payout | `payout:<id>:reserve` |
| `payout.settle` | Payout in flight | `iotec.wallet` (reference the network) for mobile money, `bank.trust` for a bank account | `payout:<id>:settle` |
| `payout.park` | Payout in flight | `client.payout_pending` | `payout:<id>:park` |
| `payout.release` | Payout in flight | `client.payout_pending`, reference the investment | `payout:<id>:release` |

Every account but the wallet and the bank is a liability to the same client, so what Finvest owes the client does not change until the provider confirms. The fixed keys mean no step can post twice.

Settle is refused by the ledger if the pot does not hold the money, because an asset account cannot go below zero. That is a reconciliation problem to be looked at, never something to force.

### What the database enforces

| Rule | How |
| --- | --- |
| Status follows the table above | A trigger repeats the transition table per actor: a client may only cancel a held payout, staff may only approve or decline one, the worker reports the provider |
| A payout that may have left is never queued again | No actor has a step from sent or uncertain back to queued |
| A finished payout never changes, and none is deleted | The same trigger, for every role including the table owner |
| The terms never change | Client, destination, amount, currency, source and request key are fixed at creation |
| A large payout starts held | A new payout above the review threshold in force cannot be inserted as queued |
| A review is signed by who made it | `reviewed_by` must be the staff member of the transaction |
| Mobile money carries at most UGX 5,000,000 | A CHECK on `payouts` and on `withdrawal_limits` |
| A limits version takes two people | CHECK that proposer and approver differ, and a policy that the approver is the staff member inserting it |
| Clients are fenced | Row policies show a client only their own payouts |

## Client routes

In `backend/app/modules/payouts/api.py`. Every route needs a signed-in client and shows only that client's own records.

| Route | What it does |
| --- | --- |
| `GET /client/payout-destinations` | The client's payout accounts that are not removed, newest first |
| `POST /client/payout-destinations` | Add one: `{kind: "mobile_money", phone, holder_name}` or `{kind: "bank", bank_code, account_number, holder_name}`. 201, awaiting the SMS code. 409 if it is already saved, or `too_many_unconfirmed_accounts` when the client already has five awaiting their code |
| `GET /client/payout-destinations/{id}` | One account |
| `POST /client/payout-destinations/{id}/code` | Text the code for an account that is `awaiting_code`. 204. A new code replaces the one before. 429 past three in 15 minutes for the account, five for the client, three to the number texted, or ten for the client in a day |
| `POST /client/payout-destinations/{id}/code/verify` | `{code}`. Moves the account to `awaiting_proof`. 401 `invalid_code` for a wrong code, 410 `expired` when none is left |
| `DELETE /client/payout-destinations/{id}` | Remove it for good. 204 |
| `POST /client/payout-destinations/{id}/proof` | Bank accounts only. A form with `proof_kind` (`bank_statement`, `bank_letter` or `bank_card`) and `file` (PDF, JPEG or PNG, at most 10 MiB). Sends it to staff |
| `GET /client/payouts` | The client's payouts, newest first, at most 50 |
| `GET /client/payouts/{id}` | One payout |
| `GET /client/withdrawal-limits` | The limits in force today, without the review threshold |

An account shows only its last digits. A payout's status is `in_review` (held for staff), `processing` (queued, sent or uncertain), `paid`, `failed` or `cancelled`. The client is never shown whether we are still finding out what the provider did.

A proof file is checked by its first bytes, stored under `payouts/<destination id>/<document id>` before the account is saved pointing at it, and deleted again if the account refuses it.

No route creates a payout. The module whose money is paid out creates it: an investment that matures, or an early withdrawal.

A new account moves from `awaiting_code` to `awaiting_proof` when the client enters a code texted for it: to the mobile money number itself, or for a bank account to the client's own number. The identity module issues and spends the code (purpose `payout_account`, migration 0034), and the account moves on in the same transaction that spends it. A wrong code still counts as one of the five tries. A mobile money account then waits at `awaiting_proof` until Finvest can read the name the network holds for the number, which is not built yet.

## Client screens

In `apps/client`: the data layer in `src/payouts` (`apiPayoutsClient.ts` for the routes above, `stubPayoutsClient.ts` for the demo build and tests), the screens in `src/app/payouts`, and the investment screens that pay out in `src/app/investment`. They are reached from Profile, "Payout accounts", and from any screen that needs an approved account and has none.

| Screen | What it does |
| --- | --- |
| Payout accounts (`/payouts`) | Every account not removed, with where it is: enter the code, send proof, checking, ready soon (approved, inside the 24 hour wait), ready, or not approved. Links to Payouts |
| Add a payout account (`/payouts/add`) | Mobile money (the client's own number filled in) or a bank account from the list in `src/payouts/format.ts`, and the name on the account. Saving texts the code at once |
| One account (`/payouts/{id}`) | The four steps, then the next one: the texted code, the proof for a bank account (statement, bank letter or card photo, PDF, JPG or PNG up to 10 MB), or what we are doing. A refusal says why; new proof is offered unless the name differs or fraud is suspected, which are final. Remove asks once on the page, never in a dialog |
| Payouts (`/payouts/history`) | The client's payouts, newest first, and today's withdrawal limits. Never the review threshold |
| One payout (`/payouts/payout/{id}`) | Amount, account and status. `in_review` reads "Checking", `processing` "On its way" (never "paid" until the provider confirms), `failed` "Not sent" with the money safe with us |
| When the term ends (`/investment/maturity/{id}`) | Roll over or pay out, and to which approved account. An account in its first day can be chosen, because it will be payable by maturity. Up to the day before maturity |
| Choose where to be paid (`/investment/pay-out/{id}`) | A matured investment waiting for an account: choose one past its first day and send it. The same account asked twice is the same payout, so a lost answer is tried again as it was, with the account locked |

The backend checks only the shape of a bank code, so the app's bank list is where the codes are set. Adding an account takes no idempotency key: a second try after a lost answer gets 409 for the same account, which the screen explains as already saved. A mobile money number stays at "Checking" after its code until Finvest can read the name the network holds for it (ioTec Verify, not built).

## Staff routes

In `backend/app/modules/payouts/staff_api.py`. Seeing the queue and opening an account needs `review_queue.view`. Every decision needs `payout_accounts.change`, held by admin, operations and compliance for now.

| Route | What it does |
| --- | --- |
| `GET /staff/payout-destinations` | Accounts in review, oldest first. `check` is null until the first person checks one; with a check it waits for a second person |
| `GET /staff/payout-destinations/{id}` | Open one: the whole account number, the name on the client's verified ID (`id_name`, null without an approved identity check) and a link to the proof that works for five minutes. Writes a `payouts.destination_opened` access event |
| `POST /staff/payout-destinations/{id}/check` | `{proof_name, statement_date?, reason}`. Records the check. 422 `name_different` on `proof_name`, `statement_date_required` or `statement_too_old` on `statement_date`; 409 `client_not_verified` |
| `POST /staff/payout-destinations/{id}/approve` | `{reason}`. Approves someone else's check; the account can be paid 24 hours later. 403 `checked_by_you`; 409 `destination_not_checked` or `account_in_use` |
| `POST /staff/payout-destinations/{id}/reject` | `{rejection_reason, reason}`. One person refuses the proof |
| `POST /staff/payout-destinations/{id}/remove` | `{reason}`. Takes the account out of use for good, as an operator control |

A checked account also waits in the approvals inbox (`GET /staff/approvals`) as `payout_account`, for staff who can see the review queue. Its subject is the kind of account, `mobile_money` or `bank`, never the number or the holder's name. Staff without `payout_accounts.change`, such as finance, see it but cannot decide it.

The database guard runs with its search path pinned to `pg_catalog, pg_temp` and names the functions it calls in full (migration 0036).

### Held payouts

A payout above the review threshold starts held, with its money already reserved (`backend/app/modules/payouts/reviews.py`, routes in `staff_held_api.py`, migration 0038). Releasing it to be sent takes two people with `payout_accounts.change`, from the owner's standing rule that staff operations that can be maker and checker must be: one proposes with a reason, and a different one approves. Declining it is one person's step, as refusing a bank transfer is, and tells the client (`payout_declined`). Approving and declining need the second step in the last ten minutes (403 `confirm_it_is_you`). Every step needs a reason. Proposing, approving and declining need an `Idempotency-Key`: a retried request returns what the first one did, and a key reused for a different request is refused with `idempotency_key_reused`.

| Route | What it does |
| --- | --- |
| `GET /staff/payouts/held` | Held payouts, oldest first, at most 100, each with `release`, its open proposal or null. Needs `review_queue.view`. The amount, kind, client and destination, never the account number |
| `POST /staff/payouts/{id}/release` | `{reason}`. 201 with the proposal. 409 `payout_not_held` or `release_already_proposed` |
| `POST /staff/payout-releases/{id}/approve` | `{reason}`, by someone other than the proposer (403 `proposed_by_you`). The payout is queued; a mobile money payout is sent, a bank payout waits for staff to pay it. 409 `release_already_decided` or `payout_not_held` |
| `POST /staff/payout-releases/{id}/withdraw` | `{reason}`, by the proposer only (403 `not_proposed_by_you`). The payout stays held. 409 `release_already_decided` |
| `POST /staff/payouts/{id}/decline` | `{reason}`. The payout is cancelled, its money goes back where it came from, the client is told, and any open proposal is closed: declined, or withdrawn when its proposer declines. 409 `payout_not_held` |

Each step is written to the audit trail as an approval, with the reason. The database checks it too: a staff step from held to queued needs an approved release decided by the same person, a release is approved only by someone other than its proposer, and an approved release whose payout is still held cannot commit.

## Sending a payout

A queued mobile money payout gets a send job in the same transaction that queued it: when it is created, or when a second member of staff approves the release of a held one. The worker sends it through the disbursements provider and reads its status back. The code is in `backend/app/modules/payouts/jobs.py`, the rules in `domain/sending.py`, and migration 0033 adds `payout_send_attempts`.

A payout is sent at most once:

1. The worker saves a send attempt and commits it before it calls the provider.
2. It calls the provider with no transaction open, giving the payout ID as the reference.
3. It saves how the attempt ended and moves the payout in one transaction, with the ledger entry that goes with the step.

| The provider said | Attempt | Payout | Then |
| --- | --- | --- | --- |
| Accepted | `accepted` | Sent | Status reads |
| Refused | `rejected` | Failed, money parked | The client is told |
| Took nothing | `deferred` | Still queued | The job tries again, up to 5 times; after that the sweep queues a fresh send 10 minutes after the last try. Two hours after the first try it is held up (below) |
| No clear answer, or the adapter raised | `uncertain` | Uncertain | Status reads, never a second send |
| Nothing saved (the worker stopped) | `interrupted`, set by the sweep after 2 minutes | Uncertain | Status reads, never a second send |

A payout the provider keeps taking nothing of is never given up on, because the client's money is in flight. Two hours after its first try it is **held up**: each time the sweep sends it again it logs `payout_held_up` with the payout ID and `error_code=provider_deferred` (never an amount, number or name), and staff with `money_movement.view` (admin, operations and finance) see it on `GET /staff/payouts/held-up`, longest first, with its first and last try and how many sends were tried. It leaves the list as soon as a send is taken. Finding out why is a person's job, with the provider; nothing is cancelled or refunded automatically.

Status reads come 10 seconds after the send, then further apart, then hourly, for 3 days. Pending, no record or a failed read keeps reading. Confirmed for the payout's amount pays it and settles the money from the pot. Refused fails it and parks the money. Confirmed for another amount moves nothing, stops the reads and is logged for staff. After 3 days with no answer, two staff members settle it from the statement (below).

A sweep runs every minute. It marks attempts that never got their answer saved as `interrupted`, queues a fresh send for a queued payout left alone for 10 minutes with no attempt that may have gone, and queues a read for a sent or uncertain payout nothing has read for an hour.

The client is told `payout_processing` when the payout is sent or uncertain, `payout_sent` when it is paid and `payout_failed` when it fails. A message names the network, such as "MTN Mobile Money", never the number.

Bank payouts are not sent by the worker. They wait for staff to pay them from the trust account. Until that path records its own send attempt, a bank payout cannot leave `queued` at all, and the database refuses marking one paid by hand.

What the database enforces:

| Rule | How |
| --- | --- |
| One send that may have gone | A unique index allows one attempt per payout that is open or ended other than `deferred` |
| An attempt only for a queued payout | The attempt guard checks the payout, and numbers attempts one after another |
| How an attempt ended is written once | The guard refuses any other change, and every delete |
| Leaving the queue takes an attempt | A trigger on `payouts` refuses queued to sent, paid, failed or uncertain without such an attempt |
| Only the worker sends | Only `finvest_worker`, acting as the system, inserts or finishes attempts; staff can read them |

The sandbox provider (`FINVEST_PAYMENTS_PROVIDER=sandbox`, local and staging only) pays nobody. The last two digits of the number choose what happens: 01 refused, 02 fails after accepting, 04 uncertain with no record, 05 deferred, 06 confirmed for another amount, anything else paid after 5 seconds.

Not built yet: the two-person settlement of a stranded payout, and paying a bank payout from the trust account. Both follow.

## Settling a payout the provider never answered for

When the reads end with no answer, the payout is **stranded**: sent or uncertain, and first sent three or more days ago. The ioTec statement is then the only evidence. Two members of staff with `reconciliation.view` settle it from there (`backend/app/modules/payouts/resolutions.py`, migration 0037).

1. One proposes an outcome with a reason. The evidence is the statement of the ioTec wallet the payout left from (the destination's network, so `mtn` or `airtel`), in the payout's currency.
   - For `paid` they name the line on that wallet's statement that carries the payout's ID, going out, for its amount.
   - For `failed` they name that wallet's statement for a day at least three days after the send. The wallet must have a statement for every day from the send to that day, and the payout's ID must be on no ioTec line at all. A day nobody imported could hold the payout's line, and settling a paid payout as failed would pay the client twice.
2. A different person approves it. In the same transaction the payout becomes paid (settled from the wallet) or failed (parked in the client's "payout pending" account, `failure_code=staff_resolved`), and the client is told, as when the provider answers. They may instead decline it with a reason, or the proposer may withdraw it. Either way, it can be proposed again.

| Route | What it does |
| --- | --- |
| `GET /staff/payouts/stranded` | Stranded payouts, longest first, with any open proposal |
| `POST /staff/payouts/{id}/resolutions` | `{outcome, statement_line_id?, statement_id?, reason}`. 409 `provider_still_being_read`, `payout_not_stranded`, `statement_line_not_this_payout`, `payout_on_statement`, `statement_not_this_wallet`, `statement_too_early`, `statement_day_missing` or `resolution_already_proposed`; 422 `statement_line_required` or `statement_required` |
| `POST /staff/payout-resolutions/{id}/approve` | `{reason}`. Needs the second step in the last ten minutes (403 `confirm_it_is_you`). 403 `proposed_by_you` |
| `POST /staff/payout-resolutions/{id}/decline` | `{reason}`. 403 `proposed_by_you` |
| `POST /staff/payout-resolutions/{id}/withdraw` | `{reason}`. 403 `not_proposed_by_you` |

Proposing and approving take an `Idempotency-Key`: a retried request returns what the first one did, and a key reused for a different request is refused with `idempotency_key_reused`.

Open proposals wait in the approvals inbox as `payout_resolution`, with the outcome as the subject. The database holds the rules too:

| Rule | How |
| --- | --- |
| The provider has its three days first | A proposal is refused while the first send is less than three days before it |
| The statement is the evidence | The database checks the line or the statement itself, the wallet, the currency and that no day is missing, at the proposal and again at approval |
| Two people | A different signed-in member of staff approves or declines; only the proposer withdraws |
| The payout moves with the approval | The payouts guard admits a staff step out of sent or uncertain only with an approved resolution for that outcome by the same person, and an approved resolution whose payout has not moved cannot commit |
| One at a time | One open and one approved resolution per payout |
| Nothing changes afterwards | Only a decision is written, a decided resolution never changes, and nothing is deleted |

Only the worker writes a destination's note of its first payout, so a payout settled as paid by hand queues a job for it.
