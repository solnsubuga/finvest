# The review queue

The console's Review queue lists, in one place, the work the system could not finish on its own and that waits for a person to look at it. A change that one person proposed and a second must approve is in the approvals inbox on the Overview ([approvals.md](approvals.md)). An item here can still show that a first person has already looked and proposed what to do, so nobody looks twice.

The backend is `backend/app/modules/review_queue`. It owns no tables. It reads each owning module's own read functions inside the reader's staff transaction, and the work itself is still done on each owning screen, whose routes check their own permissions and refuse the person who proposed.

## What is in it

| Kind | Group | From | Seen with | Reason codes |
| --- | --- | --- | --- | --- |
| `bank_transfer` | deposits | Bank transfers in review (payments) | `reconciliation.view` | `find_on_statement`, `match_to_approve` |
| `identity_check` | identity | Checks needing a person or with compliance (KYC) | `identity_checks.view` | `needs_review`, `with_compliance` |
| `payout_account` | accounts | Payout accounts in review (payouts) | `review_queue.view` | the proof kind, such as `bank_statement` or `network_registered_name` |
| `held_payout` | withdrawals | Withdrawals above the review limit (payouts) | `review_queue.view` | `above_review_limit` |
| `payout_held_up` | withdrawals | Mobile money payouts the provider keeps taking nothing for | `money_movement.view` | `wallet_short`, `provider_took_nothing` |
| `payout_unanswered` | withdrawals | Payouts the provider never said the end of | `reconciliation.view` | `no_answer` |
| `investment_transfer` | transfers | Transfers to another client held for size or screening (investments) | `review_queue.view` | `size`, `screening` |
| `organisation` | accounts | Company and group applications handed in (organisations) | `organisations.review` | the kind of organisation |

Identity checks waiting for the client, and checks the automatic checks are still running, are not waiting for us and are left out. Each source is read up to 100 items; past that the owning screen lists the rest.

## The figures

| Figure | Meaning |
| --- | --- |
| Waiting | The items listed for this reader |
| Cleared automatically today | Mobile money deposits the provider confirmed today, plus mobile money payouts paid today that no member of staff released, Kampala day |
| Oldest | When the longest waiting item started waiting |

## Route

`GET /staff/review-queue` needs `review_queue.view` (admin, operations, finance and compliance). Each item has its `kind`, `group`, `id`, `client_id` (for a company application, its applicant), `amount` or null, `reason`, `waiting_since`, `proposed_by` when a first person has proposed, and `yours` when that was the reader. `kinds` lists every kind the reader may see, even at zero, so the console can show a filter for each. It reads only and writes nothing to the audit trail: opening an item on its own screen is what is recorded.

## Counts on the menu

The console menu shows how much work waits beside the screens that have some, as drawn on the AdminNav canvas ("Review Queue 7"). `GET /staff/menu-counts` needs only a signed-in member of staff and returns `counts`, a list of `{screen, count}` in menu order, one for each screen below that the reader may open. A screen they may not open is left out, not sent as zero, so the menu never hints at another team's work. Each source is read no further than 100, and the console shows "99+" past 99 and nothing at zero. The console asks when the menu appears, every minute, and on each move to another screen, so a decision just made shows at once. It reads only and writes nothing to the audit trail.

| Screen | Needs | Counts |
| --- | --- | --- |
| Review Queue | `review_queue.view` | Every item this reader's review queue lists |
| Identity Checks | `identity_checks.view` | Identity checks a person must decide |
| Maturities | `maturities.view` | Terms past their maturity date the run has not closed, plus matured money held for the client |
| Support and Messages | `support.view` | Open chats whose last message is the client's |
| Complaints | `complaints.view` | Open complaints |
| Reconciliation | `reconciliation.view` | Bank transfers still to find on the statement or to approve |
| Rates | `rates.view` | Rate changes proposed and not yet decided |
| Compliance | `compliance.view` | Open compliance cases |

The code is `review_queue/menu.py` (reading) and `review_queue/domain/menu.py` (which screens, and the cap).
