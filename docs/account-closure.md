# Closing an account

A client can close their Finvest account from Profile, once nothing is left in it. Closing moves no money: it is refused while Finvest owes the client anything, so the client withdraws or waits for maturity first. The module is `backend/app/modules/closures`, which owns `closure_requests` and reads everything else through the functions the owning modules expose. The screen is Close account on the [design canvas](design.md).

## What must be clear first

| Blocker | Read from | What the client does |
| --- | --- | --- |
| `investments_running` | `investments.holdings.running_count`: waiting for payment or active | Wait for maturity, or withdraw early |
| `money_owed` | `ledger.statements.client_owed_as_of`, every entry including any dated ahead, summed per currency | Have it paid out. Covers money paid in and waiting to start, and a payout that failed and is parked. |
| `deposits_in_progress` | `payments.service.open_deposit_count`: not confirmed or failed, including an unclear outcome | Wait for it to end |
| `payouts_in_progress` | `payouts.payouts.open_payout_count`: held, queued, sent or unclear | Wait for it to end |
| `goals_open` | `goals.service.open_goal_count` | Close the goal |
| `plans_live` | `autoinvest.plans.live_plan_count`: active or paused | Stop the plan |

The checks run when the client asks, when staff prepare, and again in the transaction that closes the account, so an investment started or a payment made after the request still stops it.

## While a request waits

Nothing new starts while a request is `requested` or `prepared`, and nothing ever starts once it is `closed`. Each of these is refused with `conflict` and the field problem `account` / `closing`:

| Refused | Where |
| --- | --- |
| A new investment, including a goal's saving | `investments.holdings` |
| A payment in, by mobile money or bank transfer receipt | `payments.service.insert_deposit` |
| A new goal | `goals.service.open_goal` |
| A new automatic plan, or resuming a paused one | `autoinvest.plans` |
| An automatic run, new or a retry | `autoinvest.runs`: recorded as missed with code `closing_account` |

Every module asks through `closures/pending.py`, which calls `closure_pending`, a SECURITY DEFINER function from migration `0233_closure_pending`, so no other module reads `closure_requests` and the worker needs no grant on it. The function takes a share lock on the open request. A start that runs while the approval is being saved waits for it and then sees the account closed. Cancelling the request or a decline lifts the pause at once.

One gap is left on purpose: an investment checked before the request existed and saved after it. It is still caught, because staff prepare and approve in later transactions, and each checks the blockers again.

## Two people

1. **The client asks** (`POST /client/account-closure`), with an optional reason, from a sign-in in the last 10 minutes. A refusal lists the blockers as field problems with `field_path` `["blockers"]`. One open request per client.
2. **A member of staff prepares it** with a note, after checking the client page (for example, an open complaint or a next of kin question).
3. **A different member of staff approves it**, with a note and a fresh passkey or app code. The account becomes `closed`, every session the client holds ends, and they can never sign in again.

Either member of staff may decline an open request with a note, which the client sees, and the client may cancel while it is open. A client whose request was declined or cancelled may ask again. All of it needs `clients.close`, held by admin, operations and compliance. A prepared request is in the approvals inbox as `account_closure`.

## What the database enforces

Migration `0232_account_closures`:

- Only the client inserts their own request, only as `requested`, and only they cancel it.
- Only active staff prepare, approve or decline. A request is prepared once; the approver is never the preparer (`ck_closure_requests_two_people` and the guard); what the client asked and the preparation never change; a request that ended never changes. Nobody deletes a request.
- The guard on `identity_clients` from migration 0152 now also allows `active` to `closed`, only through a closure approved in the same transaction, applied by `identity_close_client`, a SECURITY DEFINER function. Nothing moves a client out of `closed`.

## After closing

- The client's records are kept for as long as the law requires: identity check, ledger, statements, audit trail and the request itself. Nothing is deleted when an account closes. Deleting records after the retention period is not built yet.
- The account number is never issued again (see [account-numbers.md](account-numbers.md)).
- The mobile number stays with the closed account, so it cannot open a new one. A client who wants to come back contacts support.
- `client_contact` answers nothing for a closed client, so Finvest sends them no messages.

## Not built yet

- Messages telling the client their request was declined or their account closed. The app shows the request's status while they are signed in.
- Dormant accounts (no activity for 12 months).
- A money movement that reaches a closed client, such as a provider's late confirmation, is left on their ledger account and shows on the console client page. Staff pay it out by hand.
