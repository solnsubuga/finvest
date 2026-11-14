# Client accounts in the console

How staff find a client, what the client page shows, and how an account is locked or unlocked. The screen is Clients on the [design canvas](design.md).

The page is put together by `backend/app/modules/console`, which owns no tables. It reads through the functions each module exposes: identity for the sign-in account and locks, KYC for the identity check, the ledger for what Finvest owes the client, payouts for saved destinations, and support for the chat and complaints. Locks belong to the identity module (`identity/client_locks.py`, migration `0152_client_locks`).

## Routes

| Route | Who | Use |
| --- | --- | --- |
| `GET /staff/clients/search?q=` | `clients.view` | A client by ID, ten-digit account number, or mobile number written any usual way (`0772…`, `256772…`, `+256772…`). |
| `GET /staff/clients/cards?ids=` | `clients.view` | Up to 100 clients by ID, each with their verified name (none until the identity check is approved), account number, status and when they joined. For screens that list clients by ID, such as support chats and complaints, so they need not read each client page. An ID with no client is left out. |
| `GET /staff/clients/{id}` | `clients.view` | The client page: account status, second steps set up, last sign-in, the identity check and the verified name, what Finvest owes them at the end of today per ledger account, payout destinations (account numbers masked), every lock and unlock, and, for staff who may see them, the latest chat and the complaints. |
| `POST /staff/clients/{id}/locks` | `clients.lock` | Propose `lock` or `unlock`, with a reason. |
| `POST /staff/clients/locks/{id}/decision` | `clients.lock` | `approved` or `rejected` by someone else, or `withdrawn` by the proposer, with a reason. |

| `GET /staff/clients/{id}/notes` | `clients.view` | The client's staff notes, newest first (the latest 100), and whether the reader may write one. |
| `POST /staff/clients/{id}/notes` | `client_notes.write` | Keep a note of up to 1,000 characters. |
| `GET /staff/clients/{id}/actions` | `clients.view` | Every action proposed for the client, newest first, and the kinds the reader may propose or decide (`allowed`). |
| `GET /staff/clients/{id}/limits` | `clients.view` | The withdrawal limits for every client today (`shared`), staff's stricter ones for this client (`own`, or null), what a payout today is checked against (`applied`, the lower of the two for each sort), all in whole shillings, and whether the reader may change them. |
| `GET /staff/clients/{id}/withdrawal-options` | `client_withdrawals.start` | What a staff withdrawal can name today: the client's investments that can be withdrawn early with what they would pay, their approved accounts past the first day's wait, their own help chat messages from the last seven days, and whether early withdrawal is switched on. |
| `POST /staff/clients/{id}/actions` | The kind's permission | Propose an action, with a reason. A `withdrawal` proposal carries `withdrawal` (`investment_id`, `destination_id`, `message_id`), and is refused with `409` while early withdrawal is switched off (`early_withdrawal_open`). A `statement` proposal carries `statement` (`date_from`, `date_to`), refused on its field as a client's own request is. A `withdrawal_limits` proposal carries `limits`, each sort in whole shillings or null. `422` with `not_below_shared` on the field for a figure at or above the shared limit of its sort. `409` if one of that kind is already waiting for the client, or the account is not active (`not_active`, `closed`). |
| `POST /staff/clients/actions/{id}/decision` | The kind's permission, and a fresh second step | `approved` or `rejected` by someone else, or `withdrawn` by the proposer, with a reason. Approving applies it at once. |

`clients.lock` is held by admin, operations and compliance.

## Staff notes

A note is what one member of staff writes about a client for the others, such as "Called about a failed payout, resent it". It is written by one person under their own name and kept as written: nothing edits or deletes one, and a mistake is put right by a later note. It moves nothing and changes nothing about the client, so it is not a two-person step. Only staff read notes. A client never sees them, and they are not in the client's data export. The audit trail records that a note was written (`console.client.note_written`), never its words. `client_notes.write` is held by every role. The table is `console_client_notes` (migration 0350): the API may only read and insert, and the row policy accepts a note only under the acting member of staff's own name.

## Acting for a client

Staff act for a client from their page with two people: one proposes with a reason, a different one approves or rejects, and the proposer may withdraw. Approving applies the action in the same transaction, through the module that owns what it changes, so nothing is ever approved and not yet done. Each kind is decided with its own permission and has its own kind in the approvals inbox. Staff act only for an active account: a locked account is unlocked first. Proposing and deciding take an `Idempotency-Key`, because approving a withdrawal moves money: a retry with the same key answers with the same action and does nothing twice.

| Kind | Permission, held by | Inbox kind | What approving does |
| --- | --- | --- | --- |
| `pin_reset` | `client_pins.reset`: admin, operations, compliance, support | `client_pin_reset` | Ends every phone app session the client holds. A PIN belongs to one phone app session (see [decisions.md](decisions.md)), so the PIN goes with it: the client logs in again with their password and a second step and chooses a new one, as after forgetting it. A browser session has no PIN and is left alone. The client is told by SMS and, if their email is proved, by email (template `app_signed_out`, fixed in code like the other security notices). |
| `withdrawal_limits` | `client_limits.change`: admin, operations, compliance, finance | `client_limits` | Records the client's own limits for mobile money and bank payouts, one payout and per day, and the size above which a payout is held for staff (`payouts.set_client_limits`, migration 0352). They only ever tighten the shared limits: every payout, early withdrawal and refund for the client is checked against the lower of the two for each sort, and a daily limit below the limit for one payout lowers that too. All null clears them. The client's app shows the limits that apply to them. A version is written only in the transaction that approved it, and none is ever changed. |
| `statement` | `client_statements.send`: admin, operations, compliance, support | `client_statement` | Asks documents for a requested statement of those dates, keyed `staff:<action ID>` (`documents.staff_statement`, migration 0353). The worker builds it, puts it in the client's app and emails it to their proved address, locked with their date of birth, exactly as one the client asks for. It is not charged to the client's ten a day. A client with no proved email gets it in the app only. The documents row policy accepts it only when the action was approved in the same transaction. |
| `withdrawal` | `client_withdrawals.start`: admin, operations | `client_withdrawal` | Asks to withdraw a running investment early for the client (`investments.request_withdrawal_for_client`, migration 0354). By the owner's decision, only when the client asked for it in writing: the proposal cites the client's own message in their help chat, from the last seven days, and one message backs one withdrawal. The money goes only to an approved payout account of the client's own past its first day's wait, with the client's limits, and everything after the request (the move to payouts, funding by the dealing desk, the payout) is exactly as when the client asks in the app. The client is told by SMS and, if proved, email (template `staff_withdrawal_started`), as well as the usual notice. The investments guard accepts a staff request only for the investment and account a `withdrawal` action approved in the same transaction names. |

The proposals are `console_client_actions` (migration 0351). PostgreSQL enforces the two people (`ck_console_client_actions_two_people`), lets only the decision columns change and only once, allows one open proposal of each kind per client, and records the approving transaction in `decided_in`, so a module that applies an action can check it was approved in that same transaction. `console.client.<kind>_proposed` and `console.client.<kind>_<decision>` are audit events with their reasons.

## Locking

A locked client cannot sign in, every session they hold ends when the lock is approved, and Finvest sends them no messages. Their money stays where it is: a lock moves nothing in the ledger.

- **Two people.** The approver is never the proposer, and only the proposer withdraws. The database refuses anything else (`ck_identity_client_locks_two_people`).
- **Only an approval changes the status.** The API holds no write on a client's status. It applies an approved lock or unlock through `identity_apply_client_lock`, a SECURITY DEFINER function that acts only on a change approved in the same transaction. A trigger on `identity_clients` backs this up: a move between `active` and `locked` needs that approval, `unclaimed` to `active` is left to the claim function (`identity_claim_client`), and nothing else moves, so never from `closed`. Only an approved account closure moves a client to `closed` ([account-closure.md](account-closure.md)).
- **One open proposal per client.**
- **On the record.** `identity.client.<kind>_proposed` and `identity.client.<kind>_<decision>` are audit events with their reasons.
