# Payouts

How money leaves Finvest, and the rules in [rules.md](rules.md) that guard it. The pure rules are in `backend/app/modules/payouts/domain`. Tables, commands, the provider adapter and the screens arrive in later pull requests, and this document grows with them.

## Payout destinations

A client is paid only to their own mobile money number or bank account. A new destination goes through four steps, in order, and no step can be skipped:

| Step | Who | Status after |
| --- | --- | --- |
| Add it and confirm the SMS code | Client | Awaiting proof |
| Upload proof of ownership | Client | In review |
| Check the proof against the name on the ID | Staff | Approved, or rejected with a reason |
| Wait 24 hours from the approval | Nobody | Payable |

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
| Who may take which step | A trigger allows a client transaction only the client's steps, a staff transaction only approve, reject and remove, and the worker only the note of the first payout |
| A client cannot approve their own destination | The same trigger, and an insert policy that admits only a new row at the first step |
| A different name is never approved | An approved row must carry an exact or partial match, the staff member who checked it, and the approval time |
| A check is signed by who made it | `checked_by` must be the staff member of the transaction |
| The account never changes | Owner, kind, number and holder name are fixed at creation |
| Nothing is deleted, and removed is final | Triggers refuse DELETE and TRUNCATE for every role, and any change to a removed row |
| One account, one client | An account approved for one client cannot be approved for another (migration 0032). The check is made at approval, so a client learns nothing about anyone else's accounts. Staff see `account_in_use` and look at both. |
| The approval time is real | An approval must be stamped within five minutes of the database clock, so the 24 hour wait cannot be shortened by a wrong time |
| Suspected fraud is final | A destination rejected for suspected fraud cannot be proved again, only removed |
| Clients are fenced | Row policies show a client only their own destinations |

Every step writes an audit event in the same transaction. Account numbers never enter the audit trail.

Two things belong to other modules and are passed in by the caller: the SMS code is checked by the identity module before the code step, and the ID name comes from the clients module.

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
| A review is signed by who made it | `reviewed_by` must be the staff member of the transaction |
| Mobile money carries at most UGX 5,000,000 | A CHECK on `payouts` and on `withdrawal_limits` |
| A limits version takes two people | CHECK that proposer and approver differ, and a policy that the approver is the staff member inserting it |
| Clients are fenced | Row policies show a client only their own payouts |

Not built yet: sending a queued payout to the provider, and the two-person settlement of a stranded payout. Both follow.
