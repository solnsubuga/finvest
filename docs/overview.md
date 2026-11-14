# The console's Overview

The Overview is the first screen every member of staff sees. It shows client money, money in and out, the mobile money floats, how far the daily balancing has got, the cash needed for maturities, where client money sits, and what needs attention. Beside it is the approvals inbox ([approvals.md](approvals.md)).

The backend is `backend/app/modules/overview`. It owns no tables and moves nothing. It reads the ledger only through `app.modules.ledger.statements`, and each other module through its own read functions: `investments.figures`, `investments.accrual.interest_runs`, `kyc.service.status_counts`, `payouts.payouts.held_up`, `reconciliation.day_close`, the review queue ([review-queue.md](review-queue.md)) and the approvals inbox.

## Sections and who sees them

Each section is null for a role that may not see it.

| Section | Needs | What it is |
| --- | --- | --- |
| `money` | `money_movement.view` | Client money held (every client liability account added up, so it counts what is invested, accrued interest, money awaiting start and payouts on their way), in transit (`client.payout_pending` and `client.payout_in_flight`), awaiting start, clients with a running investment, clients whose identity check is approved, the latest interest run, and money in and out for the last seven days |
| `floats` | `money_movement.view`, UGX only | Each network's share of Finvest's ioTec wallet by the ledger, with the warning line |
| `balancing` | `reconciliation.view` | The last day two people closed as balancing with the providers, a day waiting for its second person, and how many ended days are not closed |
| `maturities` | `maturities.view` | Running terms maturing this week (from today to Sunday) and the next three weeks: how many, their principal, and the principal set to pay out |
| `holdings` | `investments.view` | Running investments by product, the largest first |
| `attention` | everyone | Only what the reader may see, warnings first, nothing at zero |

**Money in and out.** In is what client deposits (`payment.deposit` entries) put into the cash accounts `bank.trust` and `iotec.wallet`. Out is what payouts (`payout` entries) took from them, less any that came back. A transfer between Finvest's own pots is neither. The days are effective dates in Kampala.

**Floats.** The warning line is UGX 10,000,000 per network, decided for now from the design and set in `overview/domain/figures.py`. The figure is the ledger's: what ioTec's own statement says is compared on the Reconciliation screen when the day closes.

## What needs attention

| Code | Tone | Shown when |
| --- | --- | --- |
| `float_low` | warning | A network's float is below the line; `subject` is the network |
| `payouts_waiting_for_float` | warning | Payouts wait because the ioTec wallet is short |
| `interest_run_missing` | warning | After 02:00 in Kampala, today's interest run has not happened and investments are running |
| `interest_run_behind` | warning | The latest run ended with investments it had not paid |
| `maturities_not_closed` | warning | Running terms are past their maturity date |
| `books_not_balanced` | warning past one day, else info | Ended days are not closed yet; `subject` is the last closed day |
| `review_queue` | info | Items in the review queue, with the oldest |
| `approvals_for_you` | info | Changes the reader may approve |
| `day_close_to_approve` | info | A day close is waiting for its second person |

## Route

`GET /staff/overview?currency=UGX` needs `overview.view`, which every role has. `currency` is `UGX` (the default) or `USD`. It reads only and writes nothing to the audit trail: it names no client.
