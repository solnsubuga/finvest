# Client accounts in the console

How staff find a client, what the client page shows, and how an account is locked or unlocked. The screen is Clients on the [design canvas](design.md).

The page is put together by `backend/app/modules/console`, which owns no tables. It reads through the functions each module exposes: identity for the sign-in account and locks, KYC for the identity check, the ledger for what Finvest owes the client, payouts for saved destinations, and support for the chat and complaints. Locks belong to the identity module (`identity/client_locks.py`, migration `0152_client_locks`).

## Routes

| Route | Who | Use |
| --- | --- | --- |
| `GET /staff/clients/search?q=` | `clients.view` | A client by ID, ten-digit account number, or mobile number written any usual way (`0772…`, `256772…`, `+256772…`). |
| `GET /staff/clients/{id}` | `clients.view` | The client page: account status, second steps set up, last sign-in, the identity check and the verified name, what Finvest owes them at the end of today per ledger account, payout destinations (account numbers masked), every lock and unlock, and, for staff who may see them, the latest chat and the complaints. |
| `POST /staff/clients/{id}/locks` | `clients.lock` | Propose `lock` or `unlock`, with a reason. |
| `POST /staff/clients/locks/{id}/decision` | `clients.lock` | `approved` or `rejected` by someone else, or `withdrawn` by the proposer, with a reason. |

`clients.lock` is held by admin, operations and compliance.

## Locking

A locked client cannot sign in, every session they hold ends when the lock is approved, and Finvest sends them no messages. Their money stays where it is: a lock moves nothing in the ledger.

- **Two people.** The approver is never the proposer, and only the proposer withdraws. The database refuses anything else (`ck_identity_client_locks_two_people`).
- **Only an approval changes the status.** A trigger on `identity_clients` refuses a move between `active` and `locked` unless a lock or unlock approved in the same transaction says so, and refuses any move to or from `closed`, which account closure will own.
- **One open proposal per client.**
- **On the record.** `identity.client.<kind>_proposed` and `identity.client.<kind>_<decision>` are audit events with their reasons.
