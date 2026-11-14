# Payouts

How money leaves Finvest, and the rules in [rules.md](rules.md) that guard it. The pure rules are in `backend/app/modules/payouts/domain`. Tables, commands, the provider adapter and the screens arrive in later pull requests, and this document grows with them.

## Payout destinations

A client is paid only to their own mobile money number or bank account. A new destination goes through four steps, in order, and no step can be skipped:

| Step | Who | Status after |
| --- | --- | --- |
| Add it and confirm the SMS code | Client | Awaiting proof |
| Upload proof of ownership. For a mobile money number, the worker reads the name its network holds instead (see [Mobile money numbers](#mobile-money-numbers)) | Client, or the worker for a number | In review, or refused for good if a number's name differs or there is none |
| Check the proof against the name on the ID | One member of staff | Still in review, with the check recorded, or rejected with a reason |
| Approve the check | A different member of staff | Approved |
| Wait 24 hours from the approval | Nobody | Payable |

Approving takes two people, from the owner's rule that a staff step that can have a maker and a checker has one: the first records what they read on the proof, and a second approves that check as it was recorded. Whoever recorded it cannot approve it, and a check made again replaces the one before. Refusing takes one person, because it lets no money out, and anyone may refuse, the checker included.

A rejected destination can be proved again with a better document, unless it was rejected for suspected fraud, which is final. A removed one is never used again. Staff who remove a destination must give a reason, which is recorded as an operator control.

### Proof

| Destination | Proof accepted |
| --- | --- |
| Bank account | A statement dated within the last three calendar months, a letter from the bank, or a photo of the bank card showing the name |
| Mobile money number | The name the network holds for the number, read through ioTec Verify. Nobody uploads or types it |

### The name lock

The name on the proof is compared with the name on the client's ID. Case, accents, punctuation and the order of the names are ignored.

| Result | Meaning | Can staff approve? |
| --- | --- | --- |
| Exact | The same names | Yes |
| Partial | One has a name the other lacks, such as a middle name, and at least two names agree | Yes, if they judge it is the same person |
| Different | Anything else | Never |

The comparison is made on the server from the ID name and the name the staff member read on the proof. It is never taken from the request, and it never approves a destination by itself. A different name is refused outright, for a bank account and for a mobile money number alike. No member of staff and no pair of staff can override it.

### Mobile money numbers

A mobile money number is proved by the name its network holds for it, read through ioTec Verify's phone number verification (`app/modules/payouts/name_lookups.py`, the worker's side in `name_lookup_jobs.py`, the adapter in `providers/iotec_names.py`, the shared ioTec Verify connection in `app/infrastructure/iotec_verify.py`). The lookup is asked for in the transaction that spends the code texted to the number, and the worker sends it after that transaction commits.

| The network's answer | What happens |
| --- | --- |
| The names on the client's ID, in any order | The number goes to staff (`in_review`), with that name recorded as the proof. Two people check and approve it as for a bank account, but nobody types a name: the check uses the network's |
| The ID's names with one left out, at least two agreeing | The same, as a partial match that only a person can accept |
| Any other name, including one spelt differently | Refused at once, `name_different`. Final |
| No name, or the number is barred | Refused at once, `network_name_unavailable`. Final |
| No answer: ioTec refused, failed or expired the request, or a send may have arrived and cannot be read back | The number waits. `name_check` shows `not_answered` and the client may ask again |

How a lookup is sent, by the rules for provider calls:

* A send that certainly never arrived (a connect failure, 401, 403 or 429) is tried once more at once, and then again by the job, up to five times.
* A send that may have arrived is never sent again by the worker. ioTec Verify cannot look a request up by our reference, so the lookup is marked unanswered. The client may ask for a new one with `POST /client/payout-destinations/{id}/name-check`. That is a person asking, not a blind retry, and a lookup moves no money.
* A number has at most three lookups, because each costs a fee. A lookup still unanswered an hour after it was asked for counts as unanswered.
* A name found while the client's identity check is not yet approved is kept, and the number waits (`waiting_for_id`). Once the ID is approved, the client asks again and the worker decides from the same answer, without asking ioTec again. From that ask until the worker has decided, `name_check` is `checking`.

The lookup's ID is the `referenceId` ioTec is sent and, once decided, the number's `proof_document_id`. Nothing is logged with the number or the name. Refusals from the network's answer are written to the audit trail as approvals made by the system, with the reason.

Set `FINVEST_PAYOUT_NAME_PROVIDER=iotec` for the worker to read names, with the identity checks' ioTec Verify credentials (`FINVEST_IOTEC_VERIFY_CLIENT_ID`, `FINVEST_IOTEC_VERIFY_API_KEY`, optional `FINVEST_IOTEC_VERIFY_BASE_URL`). With the default, `none`, the API asks for no lookups, because no worker would send them. A number waits at `awaiting_proof` with `name_check` `checking`, asking again is refused with `name_check_in_progress`, and none of its three lookups are used up. Once a provider is set, a waiting number with no lookup shows `not_answered`, so the client can ask for its first one. The API reads the same `FINVEST_PAYOUT_NAME_PROVIDER` at start-up, so set it for both processes.

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
| A number is proved by its network | The client's proof steps are for bank accounts only. Only the worker moves a number on, and only from one of its own lookups that answered: to staff with exactly the name the network gave, or refused for a different name or none (migration 0190) |
| Staff check a number with the network's name | A check of a number cannot change the name or the match the worker recorded |
| A lookup is the client's own and final once answered | A client asks only for their own number while it waits for its name, at most three times. Only the worker records an answer, which never changes. Lookups are never deleted |
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

**A client's own limits.** Two members of staff can set stricter limits for one client from their page ([client-accounts.md](client-accounts.md#acting-for-a-client)), in `payout_client_limits` (migration 0352). They only ever lower the shared ones: each payout is checked against the lower of the two for each sort (`domain/limits.for_client`), and `GET /client/withdrawal-limits` shows the client what applies to them.

An amount over the mobile money limit is refused with a code that tells the app to offer a bank account instead. Daily limits count every payout of the day that was not cancelled and did not fail. Amounts are never converted: a payout in another currency than the limits is refused.

Every payout created is also read by screening's transaction monitoring (`screening.contract.watch_payout`, [screening.md](screening.md#transaction-monitoring)). A monitoring case does not hold the payout.

## The life of a payout

The money leaves the client's holding when the payout is created and sits in a "payout in flight" ledger account for that client until the provider's answer is known.

| Status | Meaning |
| --- | --- |
| Screening | Waiting for the client to be screened against sanctions and politically exposed person lists. Moves on by itself once they are clear; staff may decline it. The client sees it as processing. See [screening.md](screening.md#before-a-payout). |
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

A refusal leaves nothing behind. The codes are `destination_not_approved`, `destination_in_safety_wait`, `destination_not_known_yet`, `amount_not_positive`, `currency_not_supported`, `over_mobile_money_limit`, `over_bank_limit`, `over_daily_limit`, `source_not_allowed`, `insufficient_funds` and, for a client's own request only, `channel_paused` while staff have paused that network or bank payouts on Settings ([platform-settings.md](platform-settings.md)). A payout the system asks for when an investment matures is still made while a channel is paused, and waits in the queue as always.

A payout is funded from one place only: the client's own `client.payout_pending` account for the `source_reference` the caller names, which is the investment whose money was handed over. The caller never names a ledger account, so a payout cannot be funded from another payout's in-flight money, from the money parked after a failed payout, or from another client. `source_not_allowed` is the refusal for an empty reference.

### The entries

| Entry | Debit | Credit | Key |
| --- | --- | --- | --- |
| `payout.reserve` | `client.payout_pending`, reference the investment | `client.payout_in_flight`, reference the payout | `payout:<id>:reserve` |
| `payout.settle` | Payout in flight | `iotec.wallet` (reference the network) for mobile money, `bank.trust` for a bank account | `payout:<id>:settle` |
| `payout.park` | Payout in flight | `client.payout_pending` | `payout:<id>:park` |
| `payout.release` | Payout in flight | `client.payout_pending`, reference the investment | `payout:<id>:release` |
| `payout.returned` | `iotec.wallet` (reference the network) or `bank.trust`, the pot it was paid from | `client.payout_pending`, reference the investment | `payout:<id>:returned` |

A bank payout settles and parks with the same entries, posted when two members of staff settle it from the trust statement (see "Paying bank payouts").

Every account but the wallet and the bank is a liability to the same client, so what Finvest owes the client does not change until the provider confirms. The fixed keys mean no step can post twice.

Settle is refused by the ledger if the pot does not hold the money, because an asset account cannot go below zero. That is a reconciliation problem to be looked at, never something to force.

### What the database enforces

| Rule | How |
| --- | --- |
| Status follows the table above | A trigger repeats the transition table per actor: a client may only cancel a held payout, staff may only approve or decline one (or decline one waiting for screening), the worker reports the provider and releases a payout from screening only for a client who is clear (migration 0181) |
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
| `POST /client/payout-destinations` | Add one: `{kind: "mobile_money", phone, holder_name}` or `{kind: "bank", bank_code, account_number, holder_name}`. 201, awaiting the SMS code. 409 if it is already saved, or `too_many_unconfirmed_accounts` when the client already has five awaiting their code. 422 `bank_unknown` on `bank_code` for a bank not in the list. On the phone app with a PIN it needs `pin_confirmation` from `POST /client/pin/confirm` with action `payout_account`, else 428 `pin_required`; the confirmation is used only if the account is saved. A browser sends none |
| `GET /client/payout-destinations/{id}` | One account |
| `POST /client/payout-destinations/{id}/code` | Text the code for an account that is `awaiting_code`. 204. A new code replaces the one before. 429 past three in 15 minutes for the account, five for the client, three to the number texted, or ten for the client in a day |
| `POST /client/payout-destinations/{id}/code/verify` | `{code}`. Moves the account to `awaiting_proof`, and for a mobile money number asks for the name its network holds in the same transaction. 401 `invalid_code` for a wrong code, 410 `expired` when none is left |
| `POST /client/payout-destinations/{id}/name-check` | A mobile money number whose `name_check` is `not_answered`, or `waiting_for_id` once the ID is approved: ask again. 409 `name_check_in_progress`, `name_check_limit_reached`, `client_not_verified`, `destination_not_awaiting_proof` or `name_check_not_for_this_account` |
| `DELETE /client/payout-destinations/{id}` | Remove it for good. 204 |
| `POST /client/payout-destinations/{id}/proof` | Bank accounts only. A form with `proof_kind` (`bank_statement`, `bank_letter` or `bank_card`) and `file` (PDF, JPEG or PNG, at most 10 MiB). Sends it to staff |
| `GET /client/payouts` | The client's payouts, newest first, at most 50 |
| `GET /client/payouts/{id}` | One payout. `returned_at` is set when a paid payout came back, and `resent_as` names the payout it was sent again as once the client sent it |
| `POST /client/payouts/{id}/resend` | `{destination_id}`. Send a payout that came back again, to an approved account: a new payout for the same amount from the same handover, 201. Asking again is the same payout. 409 `payout_not_returned`; 422 on `destination_id` with a new payout's refusal codes; 422 `idempotency_key_reused` once it was sent again to another account |
| `GET /client/withdrawal-limits` | The limits in force today, without the review threshold, and `bank_payout_day`: the day a bank payout asked for now is paid, today before the cut-off on a working day, otherwise the next working day, from the calendar on Settings ([platform-settings.md](platform-settings.md)) |
| `GET /client/banks` | The banks a client may be paid to, as `{code, name}`, from `domain/banks.py`: 22 banks licensed by the Bank of Uganda, taken from the client app's list. The app and the console name banks from this list |

An account shows only its last digits. A payout's status is `in_review` (held for staff), `processing` (queued, sent or uncertain), `paid`, `failed` or `cancelled`. The client is never shown whether we are still finding out what the provider did.

A proof file is checked by its first bytes, stored under `payouts/<destination id>/<document id>` before the account is saved pointing at it, and deleted again if the account refuses it.

No route creates a payout. The module whose money is paid out creates it: an investment that matures, or an early withdrawal.

A new account moves from `awaiting_code` to `awaiting_proof` when the client enters a code texted for it: to the mobile money number itself, or for a bank account to the client's own number. The identity module issues and spends the code (purpose `payout_account`, migration 0034), and the account moves on in the same transaction that spends it. A wrong code still counts as one of the five tries. A mobile money account then waits at `awaiting_proof` while the worker reads the name its network holds for the number (see [Mobile money numbers](#mobile-money-numbers)).

## Client screens

In `apps/client`: the data layer in `src/payouts` (`apiPayoutsClient.ts` for the routes above, `stubPayoutsClient.ts` for the demo build and tests), the screens in `src/app/payouts`, and the investment screens that pay out in `src/app/investment`. They are reached from Profile, "Payout accounts", and from any screen that needs an approved account and has none.

| Screen | What it does |
| --- | --- |
| Payout accounts (`/payouts`) | Every account not removed, with where it is: enter the code, send proof, checking, ready soon (approved, inside the 24 hour wait), ready, or not approved. Links to Payouts |
| Add a payout account (`/payouts/add`) | Mobile money (the client's own number filled in) or a bank account from the backend's list (`GET /client/banks`), and the name on the account. Saving texts the code at once |
| One account (`/payouts/{id}`) | The four steps, then the next one: the texted code, the proof for a bank account (statement, bank letter or card photo, PDF, JPG or PNG up to 10 MB), or what we are doing. A refusal says why; new proof is offered unless the name differs or fraud is suspected, which are final. Remove asks once on the page, never in a dialog |
| Payouts (`/payouts/history`) | The client's payouts, newest first, and today's withdrawal limits. Never the review threshold |
| One payout (`/payouts/payout/{id}`) | Amount, account and status. `in_review` reads "Checking", `processing` "On its way" (never "paid" until the provider confirms), `failed` "Not sent" with the money safe with us |
| When the term ends (`/investment/maturity/{id}`) | Roll over or pay out, and to which approved account. An account in its first day can be chosen, because it will be payable by maturity. Up to the day before maturity |
| Choose where to be paid (`/investment/pay-out/{id}`) | A matured investment waiting for an account: choose one past its first day and send it. The same account asked twice is the same payout, so a lost answer is tried again as it was, with the account locked |

The app names banks from `GET /client/banks`, read once per session. If the list cannot be read, an account still shows as "Bank" and the add screen offers to try again. Adding an account takes no idempotency key: a second try after a lost answer gets 409 for the same account, which the screen explains as already saved. A mobile money number stays at "Checking" after its code while the worker reads the name its network holds. The app does not yet show `name_check` or offer to ask again; that is a follow-up.

## Staff routes

In `backend/app/modules/payouts/staff_api.py`. Seeing the queue and opening an account needs `review_queue.view`. Every decision needs `payout_accounts.change`, held by admin, operations and compliance for now.

| Route | What it does |
| --- | --- |
| `GET /staff/payout-destinations` | Accounts in review, oldest first. `check` is null until the first person checks one; with a check it waits for a second person |
| `GET /staff/payout-destinations/{id}` | Open one: the whole account number, the name on the client's verified ID (`id_name`, null without an approved identity check) and a link to the proof that works for five minutes. Writes a `payouts.destination_opened` access event |
| `POST /staff/payout-destinations/{id}/check` | `{proof_name, statement_date?, reason}`. Records the check. For a mobile money number leave `proof_name` out: the check uses `network_name`, the name the network gave. 422 `name_different`, `proof_name_required` or `proof_name_from_network` on `proof_name`, `statement_date_required` or `statement_too_old` on `statement_date`; 409 `client_not_verified` |
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

Bank payouts are not sent by the worker. They wait for staff to pay them from the trust account, in batches (below). The worker takes no step for a bank payout, and the database refuses one.

What the database enforces:

| Rule | How |
| --- | --- |
| One send that may have gone | A unique index allows one attempt per payout that is open or ended other than `deferred` |
| An attempt only for a queued payout | The attempt guard checks the payout, and numbers attempts one after another |
| How an attempt ended is written once | The guard refuses any other change, and every delete |
| Leaving the queue takes an attempt | A trigger on `payouts` refuses queued to sent, paid, failed or uncertain without such an attempt |
| Only the worker sends | Only `finvest_worker`, acting as the system, inserts or finishes attempts; staff can read them |

The sandbox provider (`FINVEST_PAYMENTS_PROVIDER=sandbox`, local and staging only) pays nobody. The last two digits of the number choose what happens: 01 refused, 02 fails after accepting, 04 uncertain with no record, 05 deferred, 06 confirmed for another amount, anything else paid after 5 seconds.

### ioTec Pay

With `FINVEST_PAYMENTS_PROVIDER=iotec`, mobile money payouts are sent through ioTec Pay from Finvest's ioTec wallet (`backend/app/modules/payouts/providers/iotec.py`). The credentials, wallet and addresses are the shared `FINVEST_IOTEC_*` settings in `app/infrastructure/iotec`.

- A send is `POST /api/disbursements/disburse` with category `MobileMoney`, the payout's ID as `externalId`, the number as `256…`, the amount in whole shillings, and the note "Finvest payout". No name and no amount go in the note.
- A status read uses ioTec's own ID when the send was answered, and `GET /api/disbursements/external-id/{payout id}` when it was not. A record whose `externalId` is not the payout's says nothing about it.
- ioTec does not refuse a second request with the same `externalId`. A payout is still sent at most once, because of the send attempt rule above, not because of ioTec.
- Amounts are read as decimals, never floats. A paid amount that is not whole, or not in the configured currency, is not believed and the status is read again.

How each answer is sorted:

| ioTec answered | Send | Status read |
| --- | --- | --- |
| No token, the connection was never made, 401 after a new token, 404, 429 | Deferred, tried again | Unavailable, read again |
| Before the send, the wallet's `actualBalance` is below the payout | Deferred for `wallet_short`, nothing sent | |
| 400 about the wallet's balance or funds | Deferred for `wallet_short` | |
| Any other 400 about Finvest's wallet, such as "Invalid wallet" | Deferred, so the client's payout waits and staff see it held up after two hours | |
| Any other 400 | Rejected | |
| No answer after sending, a dropped connection, a 5xx, or an answer that cannot be read | Uncertain, read and never sent again | Unavailable |
| `Pending`, `SentToVendor`, `AwaitingApproval`, `Scheduled` or a status we do not know | Accepted | Pending |
| `Success` | Accepted | Paid, if the amount is the payout's |
| `Failed` with a `statusCode` or `statusMessage` about the wallet's balance or funds | Deferred for `wallet_short` | Failed |
| `Failed`, `Rejected`, `Cancelled`, `RolledBack` | Rejected | Failed |

**When Finvest's wallet is short.** A payout the wallet cannot pay is never failed back to the client. Before each send the adapter reads the wallet's balance. If the balance is below the payout, nothing is sent and the attempt ends `deferred` with `deferred_for = 'wallet_short'` (migration `0270_payout_wallet_short`). A refusal or a `Failed` answer to the send that is about the balance ends the same way. A balance that cannot be read does not stop the send, because ioTec refuses one it cannot pay. The payout stays queued with its money in flight, and the sweep sends it again every 10 minutes. It shows on `GET /staff/payouts/held-up` straight away with `waiting_for: "wallet_short"`, not after two hours, and `/health/operations` fails with `payouts_waiting_on_wallet` until the wallet is topped up ([observability.md](observability.md)). A `Failed` status read after ioTec accepted a payout still fails it as usual: by then the provider took the request, and the client can send it again.

ioTec's statuses and refusal texts are not codes we have seen from the live service yet, so a refusal reads as `other`. ioTec's test numbers (`011177777x` paid, `011177799x` failed, `011177778x` pending) start with no network's prefix, so a payout destination cannot hold one. Against a test wallet (`FINVEST_IOTEC_CURRENCY_CODE=ITX`) they are tried by hand, not by the worker.

Bank payouts do not go through ioTec. They are paid from the trust account (below).

Both follow below: the two-person settlement of a stranded payout, and paying a bank payout from the trust account.

## Settling a payout the provider never answered for

When the reads end with no answer, the payout is **stranded**: sent or uncertain, and first sent three or more days ago. The ioTec statement is then the only evidence. Two members of staff with `reconciliation.change` settle it from there (`backend/app/modules/payouts/resolutions.py`, migration 0037).

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

Open proposals wait in the approvals inbox as `payout_resolution`, with the outcome as the subject. An open proposal to release a held payout waits there as `payout_release`, with the payout's ID as the subject, for whoever may approve it. The database holds the rules too:

| Rule | How |
| --- | --- |
| The provider has its three days first | A proposal is refused while the first send is less than three days before it |
| The statement is the evidence | The database checks the line or the statement itself, the wallet, the currency and that no day is missing, at the proposal and again at approval |
| Two people | A different signed-in member of staff approves or declines; only the proposer withdraws |
| The payout moves with the approval | The payouts guard admits a staff step out of sent or uncertain only with an approved resolution for that outcome by the same person, and an approved resolution whose payout has not moved cannot commit |
| One at a time | One open and one approved resolution per payout |
| Nothing changes afterwards | Only a decision is written, a decided resolution never changes, and nothing is deleted |

Only the worker writes a destination's note of its first payout, so a payout settled as paid by hand queues a job for it.

## Paying bank payouts

The worker sends mobile money only. A queued bank payout is paid by staff from the trust bank account, by uploading a transfer file in the bank's portal (`backend/app/modules/payouts/bank_payouts.py`, routes in `staff_bank_api.py`, migration 0039). Every step that moves money takes two people, from the owner's standing rule.

1. **Prepare.** One member of staff chooses queued bank payouts in one currency, at most 200, and puts them in a batch with a reason. Nothing leaves.
2. **Approve.** A different member of staff approves the batch. In the same transaction every payout in it moves from queued to sent, and each client is told `payout_processing`, naming the bank, never the number. The preparer may withdraw the batch instead, and anyone else may decline it; either way its payouts stay queued and are free for another batch.
3. **Upload.** The approved batch's transfer file is downloaded and uploaded in the bank's portal. It is a CSV with `Reference` (the payout ID, which the bank is asked to carry), `Beneficiary name`, `Bank`, `Account number`, `Amount` (whole shillings, no separators), `Currency` and `Narrative`. It holds whole account numbers, so it exists only for an approved batch, is never stored, and every download writes a `payouts.bank_batch_file_downloaded` access event.
4. **Settle.** Once the trust statement is imported, one member of staff proposes how each payout ended and a different one approves it. Paid settles the money from `bank.trust` (`payout.settle`), and the client is told `payout_sent`. Failed parks it in the client's "payout pending" account (`payout.park`, `failure_code=bank_refused`), and the client is told `payout_failed`. The proposer may withdraw, and anyone else may decline; the payout stays sent, to be proposed again.

| Outcome | Evidence |
| --- | --- |
| Paid | The trust statement line that took the money: going out, for the payout's amount and currency, dated on or after the day the batch was approved, and named by no other open or approved settlement. When the bank took the whole file in one debit, see "One bulk debit for many payouts" below |
| Failed | A trust statement dated at least two days after the batch was approved. Every day from the approval to it must be imported, and none may hold a line going out for the payout's amount that no approved settlement accounts for. A payout that did leave is never parked, so it can never be paid twice |

Seeing needs `money_movement.view` (admin, operations and finance). Every step needs `bank_payouts.change` (admin and finance). Approving and declining need the second step in the last ten minutes (403 `confirm_it_is_you`). Every step needs a reason. Preparing, approving and declining a batch, and proposing and approving a settlement, take an `Idempotency-Key`: a retried request returns what the first one did, and a key reused for a different request is refused with `idempotency_key_reused`.

| Route | What it does |
| --- | --- |
| `GET /staff/payouts/bank/waiting?currency=UGX` | Queued bank payouts in no prepared or approved batch, oldest first, at most 200 |
| `GET /staff/payout-bank-batches?status=prepared&status=approved` | Batches in those statuses, newest first, at most 100, each with its payouts and total. A prepared batch also carries `items`: each payout's amount, client, bank, account holder and masked account, for the second person to check |
| `GET /staff/payout-bank-batches/{id}` | One batch, with its `items` |
| `POST /staff/payout-bank-batches` | `{currency, payout_ids, reason}`. 201, prepared. 422 `batch_payouts_invalid` for a payout named twice; 409 `payout_not_bank`, `payout_not_queued` or `payout_in_another_batch` |
| `POST /staff/payout-bank-batches/{id}/approve` | `{reason}`. Sends every payout in it. 403 `prepared_by_you`; 409 `batch_already_decided` |
| `POST /staff/payout-bank-batches/{id}/decline` | `{reason}`. 403 `prepared_by_you`; 409 `batch_already_decided` |
| `POST /staff/payout-bank-batches/{id}/withdraw` | `{reason}`, by the preparer only (403 `not_prepared_by_you`) |
| `GET /staff/payout-bank-batches/{id}/file` | The transfer file, `text/csv`. Needs `bank_payouts.change`. 409 `batch_not_approved` |
| `GET /staff/payouts/bank/sent` | Bank payouts sent and not yet settled, oldest first, at most 100, each with its batch, when it was sent and any open settlement |
| `POST /staff/payouts/{id}/bank-settlements` | `{outcome, statement_line_id?, statement_id?, reason}`. 201. 422 `statement_line_required` or `statement_required`; 409 `payout_not_bank`, `payout_not_sent`, `settlement_already_proposed`, `statement_line_in_use`, `statement_line_not_this_payout` or `statement_not_enough` |
| `POST /staff/payout-bank-settlements/{id}/approve` | `{reason}`. The payout is paid or failed. 403 `proposed_by_you`; 409 `settlement_already_decided` or `payout_not_sent` |
| `POST /staff/payout-bank-settlements/{id}/decline` | `{reason}`. 403 `proposed_by_you` |
| `POST /staff/payout-bank-settlements/{id}/withdraw` | `{reason}`, by the proposer only (403 `not_proposed_by_you`) |

What the database enforces:

| Rule | How |
| --- | --- |
| Two people send a batch | A batch is decided by the signed-in member of staff, now; approving or declining by someone other than its preparer, withdrawing only by its preparer |
| The batch moves with the approval | The payouts guard admits a staff step from queued to sent for a bank payout only in a batch the same person approved, and an approved batch with a payout still queued cannot commit |
| An approver approves what was prepared | Items are added only in the transaction that prepared their batch, by its preparer, and are never changed or deleted |
| One batch at a time | A payout joins a batch only while queued, a bank payout in the batch's currency, and in no other prepared or approved batch. Its row is locked, so two batches cannot both take it |
| Settled from the statement | A settlement's evidence is checked at proposal and again at approval, and the payouts guard admits sent to paid or failed for a bank payout only with an approved settlement for that outcome by the same person |
| One line, one settlement | A statement line is named by at most one open or approved settlement: one payout's, or one bulk's |
| The worker never moves a bank payout | The payouts guard refuses every worker step for one |
| Nothing changes afterwards | A decided batch or settlement never changes, and nothing is deleted. Only staff and the worker can read them |

### One bulk debit for many payouts

A bank often takes the money for an uploaded file in one debit rather than one line per payout. Those payouts are settled together from that line (`bulk_settlements.py`, routes in `staff_bulk_api.py`, migration 0280), two people as every money step:

1. One member of staff opens the approved batch's bulk debit: its sent payouts that no open settlement names, and the trust statement lines going out since it was approved that no settlement names and that are no more than those payouts together. They pick the line and the payouts it paid, with a reason. The payouts' amounts must add up to the line exactly. A payout the bank did not pay is left out and settled on its own, usually as failed.
2. A different member of staff approves it. In the same transaction every payout in it becomes paid (`payout.settle` for each), and each client is told `payout_sent`. They may instead decline it, or the proposer may withdraw it; its payouts stay sent.

A paid settlement is written for each payout as the bulk is proposed, naming the bulk and its line. It shows as the payout's open settlement in `GET /staff/payouts/bank/sent` (with `bulk_id`), and is decided only with its bulk (409 `settlement_in_bulk` on the single settlement routes).

| Route | What it does |
| --- | --- |
| `GET /staff/payout-bank-batches/{id}/bulk-debit` | `{batch, payouts, lines}`: the payouts still to settle and the lines that could be their debit. 409 `batch_not_approved` |
| `POST /staff/payout-bank-batches/{id}/bulk-settlements` | `{statement_line_id, payout_ids, reason}`. 201. 422 `bulk_payouts_invalid`; 409 `batch_not_approved`, `payout_not_in_batch`, `payout_not_to_settle`, `statement_line_in_use`, `statement_line_not_this_batch` or `amounts_do_not_add_up` |
| `GET /staff/payout-bank-bulk-settlements` | Bulk settlements waiting for a second person, oldest first, at most 100 |
| `GET /staff/payout-bank-bulk-settlements/{id}` | One bulk settlement |
| `POST /staff/payout-bank-bulk-settlements/{id}/approve` | `{reason}`, with an `Idempotency-Key` and a recent second step. Every payout in it is paid. 403 `proposed_by_you`; 409 `settlement_already_decided` or `payout_not_sent` |
| `POST /staff/payout-bank-bulk-settlements/{id}/decline` | `{reason}`, with a recent second step. 403 `proposed_by_you` |
| `POST /staff/payout-bank-bulk-settlements/{id}/withdraw` | `{reason}`, by the proposer only (403 `not_proposed_by_you`) |

What the database enforces, on top of the rules above:

| Rule | How |
| --- | --- |
| The line is the batch's | Money out of the trust account, in the batch's currency, dated on or after the day the batch was approved, checked at proposal and again at approval |
| It adds up | At commit, the payouts named by a bulk add up to its line exactly, and there is at least one |
| One batch, named once | A bulk's payouts are of its batch, and are written only in the transaction that proposed it, by its proposer |
| Decided together | A payout's settlement in a bulk is decided only after its bulk, to the same decision by the same person, and at commit every one matches the bulk |
| One claim on a line | A live bulk and a live single settlement never name the same line. Both take the same lock on the line before they look |

The bulk's line, once approved, accounts for its payouts, so the failed check above does not see it as a payout that may have left. Bank charges taken in the same debit are not handled: the line must equal the payouts exactly.

Not built yet: matching the settle entries with their lines in the day close automatically. The console's Money movement screen prepares, approves and settles batches, one payout or one bulk debit at a time, and downloads the file.

## A payout that came back

A payout the provider or bank confirmed can still come back: a bank account closed after the transfer left, or a mobile money reversal. The statement shows the money coming in again, and Finvest owes it to the client again (`backend/app/modules/payouts/returns.py`, routes in `staff_returns_api.py`, migration 0044). A paid payout never changes, so the return is a record of its own, and the ledger entry is never deleted or reversed: the return is an entry of its own.

1. One member of staff picks the paid payout (searching by the amount that came back) and then the line, from the two picker routes below, and proposes it with a reason, naming the statement line that brought the money back: on the pot the payout was paid from (the trust account for a bank payout, the network's ioTec wallet for mobile money), coming in, for the payout's amount and currency, dated on or after the day it was paid, and named by no other open or approved return.
2. A different member of staff approves it. In the same transaction the ledger posts `payout.returned` (debit the pot, credit the client's "payout pending" account for the payout's source), and the client is told `payout_returned`, naming where it was sent, never the amount or the number. They may instead decline it, or the proposer may withdraw it; nothing is posted and it can be proposed again.
3. The client sees `returned_at` on the payout and sends it again to an approved account of theirs (`POST /client/payouts/{id}/resend`). It is a new payout under the request key `return:<return id>`, so a lost answer can be tried again, and the client sends a returned payout again once. If that one fails, its money is parked for staff like any failed payout. If it is held for review and declined, its money goes back to "payout pending", and the client cannot send it again from the return, because the key is used: staff handle it from the pending queue. Until the client sends it, the money waits in "payout pending" and shows in the staff queue of non-empty pending accounts.

Insights take a returned payout off "paid out" in the month it came back ([insights.md](insights.md)).

Seeing returns needs `money_movement.view`. Proposing and deciding need `bank_payouts.change`, and approving and declining need the second step in the last ten minutes. Proposing and approving take an `Idempotency-Key`.

| Route | What it does |
| --- | --- |
| `GET /staff/payout-returns/returnable?amount_minor=&currency=UGX` | Paid payouts from the last 120 days that no open or approved return names, newest paid first, at most 100, each with when it was paid and where it went (bank or network, holder, masked account). With `amount_minor`, only payouts of that amount, to find the one a line coming in is |
| `GET /staff/payouts/{id}/return-lines` | Statement lines that could be this payout coming back: on the pot it was paid from, coming in, for its amount and currency, within 120 days of the day it was paid, and named by no open or approved return. 409 `payout_not_paid` |
| `GET /staff/payout-returns` | Returns waiting for a second person, oldest first, at most 100. Every return carries what the second person checks: the payout's amount and where it went (bank or network, holder, masked account), and the statement line itself (date, amount, reference, narrative) |
| `GET /staff/payout-returns/{id}` | One return, with the entry its approval posted |
| `POST /staff/payouts/{id}/returns` | `{statement_line_id, reason}`. 201. 409 `payout_not_paid`, `return_already_recorded`, `statement_line_in_use` or `statement_line_not_this_payout` |
| `POST /staff/payout-returns/{id}/approve` | `{reason}`. Posts the return and tells the client. 403 `proposed_by_you`; 409 `return_already_decided` |
| `POST /staff/payout-returns/{id}/decline` | `{reason}`. 403 `proposed_by_you`; 409 `return_already_decided` |
| `POST /staff/payout-returns/{id}/withdraw` | `{reason}`, by the proposer only (403 `not_proposed_by_you`) |

What the database enforces:

| Rule | How |
| --- | --- |
| The statement is the evidence | The line is checked against the payout's pot, direction, amount, currency and paid date at the proposal and again at approval |
| Two people | A different signed-in member of staff approves or declines; only the proposer withdraws |
| The approval carries its entry | An approved return must name the `payout.returned` entry with this payout's fixed key that credits the client's pending account for the payout's source with its amount, and only an approved return names an entry |
| Once | One open or approved return per payout, and a statement line takes back one payout |
| Nothing changes afterwards | A decided return never changes, and nothing is deleted. A client sees only their own approved returns |

The console's Money movement screen proposes and decides returns. The client app's payout screen shows a payout that came back and sends it again to an approved account.
