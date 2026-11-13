# Architecture

## In short

One well-structured backend, a modular monolith, on FastAPI and PostgreSQL with a double-entry ledger at its centre. The client app is built once in React Native with Expo and ships to Android, iPhone and the web. The back office console is built in React. Mobile money, the bank, identity checks, email and SMS sit behind small adapters, so any provider can be swapped without touching the core.

Finvest's risk is not scale. It is money correctness and regulatory trust. One deployable backend is far easier to reconcile, audit and operate than a set of services.

## Shape

The backend is one application split into modules with strict boundaries. Each module owns its tables and exposes functions to the others. Nothing writes to another module's tables. If a module later needs to scale separately it can be lifted out, but we do not pay that cost on day one.

```
 Client app (Android, iPhone, web)        Back office console (web)
              |                                     |
              +---------------- HTTPS --------------+
                                |
                        API (one FastAPI app)
   +--------+---------+--------+--------+---------+---------+
   |Identity| Clients | Ledger | Invest | Goals   | Console |
   |and auth| and KYC |        | ments  |         | and     |
   |        |         |        |        |         | audit   |
   +--------+---------+---+----+--------+---------+---------+
                          |
            PostgreSQL (single source of truth)
                          |
   Worker and scheduler (jobs stored in PostgreSQL)
       |          |          |          |          |
     ioTec Pay      ioTec       ioTec     Bank       Identity   Resend email,
     (MTN, Airtel,  Messaging   Verify    statement  check      Expo push
     bank payouts)  (SMS)       (phone)   import     adapter    adapters
```

## Processes

One codebase, four entry points, each with its own health check:

| Process | Job |
| --- | --- |
| API | Serves HTTP for the client app, console and website rates endpoint |
| Worker | Claims and runs jobs from the PostgreSQL jobs table |
| Scheduler | Keeps time and creates scheduled runs. PostgreSQL owns each run. |
| Migrate | Runs Alembic migrations under the migration role |

Each can be restarted or scaled alone without splitting the code.

## Modules

| Module | Owns |
| --- | --- |
| Identity and auth | Password plus a second step (for clients a passkey, an authenticator app, or a code by SMS or email, whichever the client has set up), app PIN for unlocking a trusted phone, devices, sessions, staff sign-in with password and a passkey or authenticator app code, roles and permissions |
| Clients and KYC | Client profiles, documents, identity check results, risk answers, next of kin |
| Ledger | Accounts, journal entries, balances. The only place money moves. |
| Payments | Deposits and payouts through ioTec Pay (MTN and Airtel) and the bank. Talks to providers and posts to the ledger. |
| Investments | Products, rates, client holdings, daily interest accrual, maturity, rollover |
| Dealing | The dealing desk: orders for bills and bonds, bills held at the custodian, allocation to client investments, the liquidity buffer |
| Goals | Goals, saving rules, group goals and approvals |
| Automatic investing | Plans that invest the same amount weekly or monthly, and the runs that ask for each payment. Module `autoinvest`. See [automatic-investing.md](automatic-investing.md). |
| Reconciliation | Statement import, matching, exceptions, daily close |
| Notifications | Templates and delivery by push, SMS and email |
| Console and audit | Review queue, maker and checker approvals, audit trail, reports |

## How money moves

1. Every movement is a journal entry with at least two lines that sum to zero. A mobile money payment debits "MTN collections account" and credits "client investment, awaiting start". When the investment starts, the money moves to the client's holding in that product.
2. Entries are never edited or deleted. A mistake is fixed with a new reversing entry.
3. A client's balance is the sum of their ledger lines, kept in a cached balance row that is updated in the same database transaction.
4. Every request that moves money carries an idempotency key, so a retry from a weak connection can never post twice.
5. Calls to providers are not made inside the database transaction. The intent is saved first as a job in the same transaction, a worker sends it, and the provider's callback or a status poll confirms it. This is the outbox pattern.
6. Each morning the reconciliation module compares the ledger to provider and bank statements.

This is what makes "instant" safe. The investment starts at once because the provider confirmed the payment, and reconciliation proves it the next morning.

## Working without a wallet

The wallet is parked because it would mean holding client money. Money is only ever in transit through the trust account.

| Situation | What happens |
| --- | --- |
| Investing | The client chooses a product and amount, then pays by mobile money or bank transfer. The investment starts when the payment is confirmed. |
| Maturity | The client chooses in advance: roll over, or pay out to mobile money or bank. There is no balance to park it in. |
| Withdrawal | Paid straight to a saved mobile money number or bank account. |
| Goals | Each saving is collected from mobile money and invested the same day, so each one must meet the product minimum. Nothing waits in a pot. |
| Failed payout | The money stays in a ledger account for that client marked "payout pending" and staff are alerted. This is the one place a balance can sit, and it should be cleared within a day. |
| Overpayment or unmatched deposit | Refunded to the sender or matched by staff. Never kept as credit. |

The ledger design does not change. It simply has no spendable wallet account, and it leaves room to add one later if the licence allows.

## Payout destinations

Clients save their own bank accounts and mobile money numbers for withdrawals and maturity payouts. Each one must be in the client's name, is confirmed by SMS code, needs an uploaded statement or other proof of ownership that staff check against the ID name, and waits 24 hours before its first payout.

## Addresses

| Address | Serves |
| --- | --- |
| finvestug.com | Public website. Holds no client data. |
| app.finvestug.com | Client app on the web |
| admin.finvestug.com | Back office console. Never linked from the website. Opens only for staff on allowed networks. |
| api.finvestug.com | Backend API (proposed, not yet confirmed) |

Separate addresses keep sign-in cookies apart.

## Hosting

Containers on one host behind Caddy, managed PostgreSQL with point-in-time recovery, and private object storage, on DigitalOcean with Cloudflare in front. The region must be confirmed against Uganda's data protection rules before committing. Terraform for all of this lives in [`infra/`](../infra/README.md) and is planned separately.
