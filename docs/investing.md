# Investing

Products, rates, interest and tax, and the rules in [rules.md](rules.md) that govern them. The pure rules are in `backend/app/modules/investments/domain`, the catalogue and rate commands in `backend/app/modules/investments/catalogue.py`, and the database side is migrations 0060 and 0061. Other modules import only from `investments/service.py`. The daily interest run, maturity and the screens arrive in later pull requests, and this document grows with them.

## Products

The catalogue starts with the three products Finvest 1.0 sold.

| Code | Product | Currency | Term | Smallest amount | Interest paid | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `TREA-91` | 91 Day Treasury Bill | UGX | 91 days | UGX 100,000 | At maturity | Live |
| `TREA-182` | 182 Day Treasury Bill | UGX | 182 days | UGX 200,000 | At maturity | Live |
| `USD-BOND` | USD Fixed Income | USD | Open | $500 | Monthly | Paused |

A product is draft, live or paused. Only a live product takes new investments. A paused product takes none, and its running investments carry on. The dollar product is paused until paying in and out in dollars is built.

There is no wallet, so an investment is paid for in one payment and that payment must meet the minimum by itself.

## Rates

A product does not hold a rate. Each product has dated rate versions: the yearly rate before tax, the tax on interest, and the day the version starts.

| Product | Yearly rate | Tax on interest | Clients see |
| --- | --- | --- | --- |
| 91 Day Treasury Bill | 12.50% | 15% | 10.63% a year, after tax |
| 182 Day Treasury Bill | 13.75% | 15% | 11.69% a year, after tax |
| USD Fixed Income | 7.25% | 5% | 6.89% a year, after tax |

Rates are stored as whole basis points (12.5% is 1250), never as decimals or floats.

### Changing a rate

1. One member of staff proposes the change with a reason and an effective date.
2. A different member of staff approves it or sends it back with a note. Nobody decides their own proposal. The proposer can withdraw it while it waits.
3. The effective date must be later than the day of the proposal and later than the day of the approval, so a rate never changes under a day that has already begun.
4. Two rates for one product cannot start on the same day, and a proposal that changes nothing is refused.

A change affects only investments that start on or after its effective date. A running investment keeps the rate and tax it started with.

One change waits for approval per product at a time. Proposing, approving, sending back and withdrawing are each recorded in the audit trail with the reason.

### What the database enforces

| Rule | How |
| --- | --- |
| A rate is never changed or removed | A trigger refuses UPDATE, DELETE and TRUNCATE on `investment_rates` for every role, the table owner included. |
| A rate exists only for an approved proposal | The application can insert a rate only with the ID of a proposal whose status is approved and whose product, date, rate and tax are the same. The seeded rates have no proposal, and only a migration can add such a row. |
| A second person decides | A check constraint refuses an approval or a send back by the proposer, and a withdrawal by anyone else. |
| An approval comes before the effective date | A check constraint compares the effective date with the Kampala day of the decision. |
| An approved proposal has its rate | A constraint checked at commit refuses an approval that wrote no rate. |
| Nobody acts in another's name | A trigger requires the proposer, and later the decider, to be the member of staff the transaction acts for. |
| A decision is final | A decided proposal never changes, and a proposal's product, rate, date and reason never change at all. |
| Products are read-only | The application roles hold only SELECT on `investment_products`. Editing a product arrives with its own approval flow. |
| Proposals are staff business | A row policy shows proposals to staff transactions only. |

Products and rates are readable by any API transaction, including one with nobody signed in, because the public website shows rates. The rows hold the tax rate, so the fence for clients is in the service: see below.

## Interest

The formula is the one Finvest has always used:

```
interest = principal × yearly rate × days ÷ 365
```

A year is 365 days in every year, leap years included. Nothing is earned on the start date. The first day is earned the day after, and the last on the maturity date, which is the start date plus the term. A 91 day bill earns exactly 91 days.

Interest is posted to the ledger once a day. To keep the postings equal to the formula on every day, nothing is rounded per day. The total earned after `n` days is worked out and rounded once, and the amount posted on day `n` is that total minus the total after `n - 1` days. On some days a small investment posts nothing, and it never posts a negative amount.

| Figure | Rounding |
| --- | --- |
| Interest before tax, to date | Down to the smallest unit. Interest that has not been earned is never credited. |
| Tax, to date | The tax rate applied to that figure, rounded down. The fraction stays with the client. |
| What the client earns | The first minus the second, so the three always add up. |

Both directions are set in one place, `INTEREST_ROUNDING` and `TAX_ROUNDING` in `domain/interest.py`.

Worked example: UGX 1,000,000 in the 91 day bill earns 31,164 before tax over the term, 4,674 is tax, and the client earns 26,490 and gets back 1,026,490.

## The daily interest run

The scheduler fires `investments.accrue_interest` at 00:30 Kampala time, and the worker runs it. Each run is for one business day: the day it was scheduled in, whenever it actually executes.

For every running investment that has not been paid up to that day, the run works out everything earned by then from the formula, subtracts what earlier runs posted, and posts the difference as one ledger entry of type `investment.interest_accrued`:

| Debit | Credit |
| --- | --- |
| `interest.expense`, interest before tax | `client.interest_accrued`, the client's, reference the investment: what the client earns |
| | `tax.withholding_payable`: the tax held back |

The same transaction moves the investment's own totals (`accrued_through`, and interest and tax to date), so the ledger and the investment always agree.

Because the amount is "earned so far minus posted so far", the awkward cases need no special handling:

| Case | What happens |
| --- | --- |
| The run is retried or queued twice | The second pass finds every investment already paid for that day and posts nothing. |
| The scheduler was down for some days | Each missed day gets its own run, up to 31. Whichever executes first pays every missed day in one entry, and the others find nothing to do. |
| A payment is confirmed late at night and processed after the run | The start date is still the day it was confirmed, so the next run pays two days. The client loses nothing. |
| The worker dies part way | Investments are paid in batches of 200, each in its own transaction that only the current holder of the job can commit. The retry carries on with the rest. |
| Two workers hold the same run | Each skips rows the other has locked, and no investment is paid twice. |
| One investment cannot be paid | It is left exactly as it was and logged with its ID. Everyone else is paid. It stays due, so the next run tries again. |
| A fixed term has matured | It is paid up to its maturity date and no further, however late the run. |
| The run is late and the day is closed in the ledger | Entries are dated the day they are posted, not the day they are for, so a late run never writes into a closed day. |

A run is refused for a day that has not come.

Each run leaves a row per currency in `investment_interest_runs`: how many investments it paid, interest before tax, tax, and how many investments were still behind when it ended. Behind should always be zero. Anything else is logged as an error and needs a person to look. `investments.service.interest_runs` reads it for the console, staff only. Only the worker writes the table, its totals only grow, and nobody can delete from it. `investments.service.accrue_investment` pays one investment up to a day, for putting right one that a run left behind.

Not decided yet: nothing here pays interest out. Accrued interest sits in `client.interest_accrued` until maturity, and the monthly payment for the open term dollar product is not built.

## Maturity

On its maturity date a fixed-term investment has earned its last day. The scheduler fires `investments.mature` at 01:00 Kampala time, after the interest run, and the worker closes every active investment whose maturity date has come. Each is closed in its own savepoint, in one transaction with all its money moves. If the interest run has not reached the last days, they are posted first. Nothing is paid past the maturity date, however late the run is.

The client chooses what happens at maturity, from review and confirm up to the day before maturity. Rolling over is the default. Paying out needs one of the client's own approved payout destinations (`payouts.destinations.usable_destination`). The database refuses a change on or after the maturity date, by its own clock. Only the client can change the choice. Staff cannot.

| Choice | What the run does | Ledger entry |
| --- | --- | --- |
| Roll over, product still live | A new investment in the same product, for principal plus interest after tax, at the rate in force that day. It starts on the old maturity date, so no day goes without interest. | `investment.rolled_over`: debit the old `client.holding` and `client.interest_accrued`, credit the new `client.holding` |
| Pay out, destination chosen | Hands principal and interest to payouts and calls `payouts.payouts.create_payout` to that destination in the same transaction, with request key `maturity:<id>:<destination>`. | `investment.matured`: debit `client.holding` and `client.interest_accrued`, credit `client.payout_pending` (reference the investment) |
| Pay out, no destination | The same handover, and the money waits. | `investment.matured` |
| Roll over, product paused or closed | The same handover, and the money waits. It is never rolled into something the client did not choose. | `investment.matured` |
| Payouts refuses the payout | The handover stays. The refusal code (for example a mobile money limit) is kept on the investment. | `investment.matured` |

Money that waits shows in the payouts console queue. The client sends it on with `pay_out_matured` to a destination of theirs, and may try another destination if one is refused. A matured investment is final. The only change the database allows afterwards is recording the payout that carries waiting money.

The run publishes `investments.rolled_over` or `investments.matured`. The goals module follows `investments.rolled_over` (consumer `goals.follow_rollover`) to keep a rolled-over saving in its goal. A matured investment carries its maturity entry and exactly one of: the investment it rolled into, the payout, or the reason the money is waiting.

## HTTP routes

The code is `backend/app/modules/investments/api.py`. Amounts are integers in the smallest unit (`amount_minor`), and a request that sends one as text or with a fraction is refused. Client and public responses are built only from after-tax figures, and a unit test checks that no field of those contracts names tax, a gross figure or basis points.

| Route | Who | What |
| --- | --- | --- |
| `GET /public/products` | Anyone | Live and paused products with the rate after tax. `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *`. |
| `GET /public/quote?product=&amount_minor=` | Anyone | The returns calculator: interest after tax and total at maturity, or nulls for an open term. 422 with the field code when the product would not take the amount. |
| `GET /client/products`, `GET /client/products/{code}/quote` | Signed-in client | The same, for live products only. |
| `POST /client/investments` | Signed-in client, `Idempotency-Key` | Review and confirm. Body `{product_code, amount_minor, currency, at_maturity?}`. Returns the investment, awaiting payment. |
| `GET /client/investments`, `GET /client/investments/{id}` | Signed-in client | Their portfolio and one investment, after tax. |
| `PUT /client/investments/{id}/at-maturity` | Signed-in client | `{at_maturity, destination_id?}`, up to the day before maturity. |
| `POST /client/investments/{id}/pay-out` | Signed-in client | `{destination_id}`: send on money that matured and is waiting. A refusal from payouts is a 422 on `destination_id` with the payouts code. |
| `GET /staff/products`, `GET /staff/products/{code}/rates` | `products.view`, `rates.view` | Products with the rate and tax in force and the next approved one, and each product's rate history. |
| `GET /staff/rate-proposals?status=` | `rates.view` | Rate changes and their state. |
| `POST /staff/rate-proposals`, `POST /staff/rate-proposals/{id}/approve`, `/send-back`, `/withdraw` | `rates.change` | Proposing and deciding rate changes, with the second person enforced in the database. |
| `GET /staff/investments?status=&limit=`, `GET /staff/investments/{id}`, `GET /staff/interest-runs` | `investments.view` | Every investment with its tax, and the record of daily runs. |

Seeing rates needs `rates.view`. Proposing, approving, sending back and withdrawing a rate change need `rates.change`, held by admin and finance for now. A rate still never takes effect without a second person.

## What clients see

Clients see the yearly rate after tax and the interest they earn after tax. They never see the tax rate, the tax amount or the rate before tax. In the code, `ClientReturns` and `RateVersion.client_rate_percent` are the only figures a client-facing response is built from, and the service functions `client_products` and `client_quote` are the only ones a client-facing route may call. `client_quote` is the returns calculator and the review screen: what an amount earns after tax if it starts today, and when it matures. The rate shown is a label, rounded to two decimal places. No amount is ever worked out from it.

## An investment

One payment by one client into one product. The code is in `backend/app/modules/investments/holdings.py` and `jobs.py`, and the table is migration 0061.

| Status | Meaning |
| --- | --- |
| `awaiting_payment` | The client reviewed and confirmed. The terms are fixed. Nothing has been paid. |
| `active` | The payment was confirmed and the money is in the client's holding. It earns interest from the next day. |
| `cancelled` | It was not paid for in time. Final. No money ever moved. |
| `matured` | Its term ended and its money left the holding: rolled into a new investment or handed to payouts. Final. |

1. **Review and confirm.** `request_investment` saves the investment under the client's idempotency key, so two taps invest once. The client must have passed the identity check. The terms are the product's rate and tax that day, and the investment keeps them for good.
2. **Pay.** The investment's ID is the purpose of the deposit. Payments asks `payable_investment` who pays and exactly how much. A review stays payable for 72 hours.
3. **Start.** When the deposit is confirmed, payments publishes `payments.deposit_confirmed` and the job `investments.start_from_deposit` runs `start_investment`. The whole amount must be waiting in `client.awaiting_start` for this investment, no less and no more. One entry moves it to `client.holding`, and `investments.started` is published.
4. **Or not.** If the money waiting is not exactly the amount, nothing moves, the job fails for good and staff see it among the failed jobs. An investment nobody paid for is cancelled an hour after its window closes, by a sweep every 15 minutes. One with any money waiting is never cancelled, and neither is one with a deposit that has not failed (`payments.service.has_live_deposit`), because a mobile money payment still pending or uncertain can confirm after the window. The sweep locks the investment before it asks, and `payable_investment` takes a shared lock on the same row before payments lets a deposit in, so a deposit and a cancel can never both get through.

A client can have five investments waiting for payment at a time.

### The start date

The start date is the business day, in Kampala, on which the payment was confirmed. It is not the day of the review, and not the day the start was processed. If the worker is slow or down overnight, the client still earns from the day their money arrived. Payments sends that day in the event as `confirmed_on`.

The ledger entry for the start is dated the day it is posted, like every entry, so that it never lands in a day the books have closed. When the worker was down overnight that date is later than the start date. The start date on the investment is the one interest is counted from.

### What the database enforces

| Rule | How |
| --- | --- |
| The terms are the product's | A trigger checks a new investment against the catalogue itself: the product is live, the currency and term are the product's, the amount meets the minimum, and the rate and tax are the ones in force that day. |
| The terms are today's | A second trigger (migration 0062) refuses an investment whose creation time is more than five minutes from PostgreSQL's own clock, or whose payment window is not 72 hours. The rate is picked by the creation time, so a server with a wrong clock cannot quote an old rate. |
| The terms never change | The same trigger refuses any change to the client, product, amount, rate, tax, term or dates of the review. |
| Only the worker starts or cancels | Status changes need the system actor on the worker's database role. The API roles hold UPDATE on the maturity choice only. |
| A start is written once | The start date, time and ledger entry never change. A cancelled investment never changes at all. |
| Interest only goes up | Accrued interest and its date move forwards only, and only the worker moves them. |
| Clients are fenced | A row policy shows a client only their own investments, and lets them create only their own. |
| Nothing is deleted | No role holds DELETE. |

Ledger entry for the start (`investment.started`, key `investment-start:<investment ID>`):

| Debit | Credit |
| --- | --- |
| `client.awaiting_start`, the client's, reference the investment | `client.holding`, the client's, reference the investment |

### The audit trail and the client's messages

Each step of an investment's life is an audit event on resource `investment`, written in the same transaction as the step:

| Action | Actor | Details |
| --- | --- | --- |
| `investments.requested` | the client | `product`, `at_maturity` |
| `investments.maturity_chosen` | the client | `at_maturity`, `destination_id` |
| `investments.started` | the system | `start_date`, `ledger_entry_id` |
| `investments.matured` | the system | `step`: `roll_over`, `pay_out` or `hold` |

A retried request or a second start writes nothing new. The client is told when an investment starts (`investment_started`) and when it matures (`investment_matured`, with the amount after tax), through `notifications.notify` in the same transaction, keyed by the investment. The daily `interest_earned` message is not sent yet.

## Ledger accounts

The investments module opens these. The entries are described with the commands that post them.

| Account | Kind | Holds |
| --- | --- | --- |
| `client.holding`, per client, reference the investment | Liability | The principal of a running investment |
| `client.interest_accrued`, per client, reference the investment | Liability | Interest the client has earned after tax and not yet been paid |
| `tax.withholding_payable` | Liability | Tax held on interest, owed to URA |
| `interest.expense` | Expense | Interest before tax, as it is earned |

## How it joins the other modules

Agreed with the payments and payouts threads on 13 November 2026. Starting and daily interest are built. Maturity and unwinding are not.

- **Starting.** The client reviews and confirms, which saves an investment that is awaiting payment. Its ID is the purpose of the deposit. Payments asks `investments.service.payable_investment` for the exact amount before taking a deposit. When the deposit is confirmed, payments publishes `payments.deposit_confirmed`, and the `investments.start_from_deposit` job moves the money from `client.awaiting_start` to `client.holding` and starts the investment.
- **Maturity.** See [Maturity](#maturity). Payouts finds the handover by the client and the investment ID (`payouts.domain.posting.funding_account`), and owns everything after `create_payout`.
- **Dealing desk.** It reads the book through `owed_by_product(session, on)`, `running_investments(session, product_code, started_after=None)` and `maturing(session, date_from, date_to)` in `investments.service`, and follows the outbox topics `investments.started`, `investments.matured` and `investments.rolled_over`. Interest in these reads is net of tax, as posted so far.
- **Saving sources.** An investment the client did not make themselves, from an automatic investing run or a goal's saving rule, carries `source_type` (`autoinvest_run` or `goal_saving`) and `source_id`. The columns and their checks are on main (migration 0063): only the worker creates such an investment, at most one per source. The function that creates one, `request_system_investment`, is planned and arrives with the automatic investing work.
- **Unwinding.** For a deposit credited by mistake, `investments.service.unwind_investment` will move the principal back to `client.awaiting_start` and reverse interest not yet paid out. It refuses if anything was already paid out.

## Early withdrawal

A client may withdraw a running investment early, once, up to the day before a fixed term matures. An open term can be withdrawn at any time. They name one of their approved payout destinations that is past its first day's safety wait, and send an idempotency key (`POST /client/investments/{id}/withdraw`).

**Switched off until it is paid end to end.** `FINVEST_EARLY_WITHDRAWAL_OPEN` is false by default, everywhere. While it is off, `can_withdraw` is false on every investment and the route answers `conflict` before anything is saved. The withdrawn money is published to the dealing desk, which funds it, and the payout follows; a publish made before the consumer exists is never replayed. The PR that wires the dealing funding consumer and `investments.pay_early_withdrawal` proves the whole path with it on in tests. It stays off in production until the owner says to turn it on.

It pays the principal plus the interest earned after tax so far, with no fee: exactly the `value_minor` the client was shown. Interest stops at the request, so the figure cannot move while the money is found. The owner decided this on 2026-11-13 (see [decisions.md](decisions.md)).

1. **The request.** The client's transaction stamps `withdrawal_requested_at` with PostgreSQL's clock, keeps the destination, queues `investments.withdraw_early` and records `investments.withdrawal_requested`. Nothing moves yet. The daily run and the maturity run skip the investment from then on.
2. **The move to payouts.** The worker posts `investment.withdrawn_early`: the holding and the interest go to the client's payout pending account for the investment, in one entry. The investment becomes `withdrawn`, the worker records `investments.withdrawn_early`, and it publishes `investments.withdrawn_early` with `{investment_id, client_id, product_code, principal_minor, currency, amount_minor}` for the dealing desk.
3. **The payout.** The dealing desk funds the withdrawal from the liquidity buffer or a sale and publishes `dealing.withdrawal_funded`. The consumer `investments.pay_early_withdrawal` checks the amount is exactly what was withdrawn, then creates the payout with the key `early:<funding id>`. A refusal fails the job for staff, and the money stays in the payout pending account.

Whichever merges second wires the two consumers: `dealing.events.FUND_EARLY_WITHDRAWAL` into the `investments.withdrawn_early` publish, and `investments.events.PAY_EARLY_WITHDRAWAL` into `dealing.withdrawals.FUNDED_CONSUMERS`.

The database fences each step (migration 0064). Only the client asks, once, while the investment runs, before the maturity date and by the database clock. Only the worker withdraws an investment someone asked for. A withdrawn investment changes only to record its payout, once. Accrual and the choice for maturity stop at the request.

## Not built yet

Editing a product, a staff refund of money that did not start an investment, the monthly coupon for open terms, and the early withdrawal screen in the client app. The dealing desk is its own module: see [dealing-desk.md](dealing-desk.md).
