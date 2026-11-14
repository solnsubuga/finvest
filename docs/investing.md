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

Each run leaves a row per currency in `investment_interest_runs`: how many investments it paid, interest before tax, tax, and how many investments were still behind when it ended. Behind should always be zero. Anything else is logged as an error, and `/health/operations` raises `interest_behind` (migration 0271) until each investment is paid, so the uptime alert reaches a person. A run stops when 25 investments in a row cannot be paid (`MAX_FAILURES_IN_A_ROW`): that many has a common cause, such as a closed ledger day, so going on would only fail the rest. The batch it was in rolls back, the job fails with `interest_run_failing`, and every investment stays due for the next run. `investments.service.interest_runs` reads it for the console, staff only. Only the worker writes the table, its totals only grow, and nobody can delete from it. `investments.service.accrue_investment` pays one investment up to a day, for putting right one that a run left behind.

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

Money that waits shows in the payouts console queue. The client sends it on with `pay_out_matured` to a destination of theirs, and may try another destination if one is refused. When the client asks staff instead, two members of staff send it on for them as a `pay_out` correction (see [Undoing a mistake](#undoing-a-mistake)), to an approved account of the client's only. A matured investment is final. The only change the database allows afterwards is recording the payout that carries waiting money.

The run publishes `investments.rolled_over` or `investments.matured`. The goals module follows `investments.rolled_over` (consumer `goals.follow_rollover`) to keep a rolled-over saving in its goal. A matured investment carries its maturity entry and exactly one of: the investment it rolled into, the payout, or the reason the money is waiting.

The console's Maturities screen reads `GET /staff/maturities` (`investments/maturities.py`). It is worked out from the investments themselves, never from the jobs table, so it is right however the run went. For each maturity date from a week back to two weeks ahead, in each currency, it gives the number of terms and their principal, how many were rolled over, paid out or held, and how many are still open by what the client chose. It also lists every running investment past its maturity date (the run failed it or has not run) and every matured one whose money waits for the client, with the reason. It only reads: the run closes terms by itself, and waiting money is sent on by the client, or by two members of staff with a `pay_out` correction.

## Running a day again

When a day's interest run failed part way, or this morning's maturity run left terms open, staff ask for that run again (`investments/run_requests.py`, table `investment_run_requests`, migration 0330). One member of staff proposes with a reason, a different one approves or declines with a note, and only the proposer withdraws it. Proposing and withdrawing need `investment_corrections.change`; approving and declining need the same and a second step from the last few minutes. Each waiting request shows in the approvals inbox as `investment_run`.

Approving queues the run itself in the same transaction, as the same job the scheduler queues (`investments.accrue_interest` or `investments.mature`), keyed `staff-run:<request id>` so it never stands in for a scheduled run, with `scheduled_for` at noon in Kampala on the day. Nothing else moves money. Both runs are safe to repeat: interest posts "earned so far minus posted so far", so a day run twice pays nothing the second time, and maturity only closes terms still open.

The days are limited (`domain/runs.py`). Interest may be asked for any day from today back 31 days, the same window the scheduler catches up on by itself (422 `not_yet` or `too_old` on `run_date`). Maturity is asked for today only (`maturity_today_only`), because one run closes every term due by its day, and a request proposed yesterday is refused at approval as 409 with `run_date_passed`. The database refuses a day that has not come by its own clock, a decision by the proposer, a second open request for the same run and day, and any change after a decision. Only staff read the table on the API's role, and the worker only reads it.

Audit events on resource `investment_run_request`: `investments.run_request.proposed`, `.approved`, `.declined` and `.withdrawn`, with the run, the day and the job.

### Reminders before maturity

Every morning at 08:30 Kampala time the worker runs `investments.remind_maturing` (`investments/reminders.py`). It reads how many days before maturity to remind from Settings (7 days and 1 day unless staff changed it, [platform-settings.md](platform-settings.md)) and raises `investment_maturing` for every active investment maturing on one of those days, naming the product and the date, never an amount. Each reminder is keyed by the investment, its maturity date and the number of days, so a second run tells nobody twice. A morning the run missed is not made up: the next reminder day sends its own.

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
| `POST /client/investments/{id}/pay-out` | Signed-in client | `{destination_id}`: send on money that matured and is waiting. A refusal from payouts is a 422 on `destination_id` with the payouts code. On the phone app with a PIN it also needs `pin_confirmation` (see [Early withdrawal](#early-withdrawal)), else 428 `pin_required`. |
| `POST /client/investments/{id}/withdraw` | Signed-in client, `Idempotency-Key` | `{destination_id, pin_confirmation?}`: ask to withdraw early. See [Early withdrawal](#early-withdrawal). |
| `GET /client/transfer-recipients?to=` | Signed-in client | The client an account number or mobile number belongs to, shown as "Brian K.". 20 look-ups in 15 minutes. |
| `POST /client/investments/{id}/send` | Signed-in client, `Idempotency-Key` | `{to_account_number, amount_minor, currency, message?, whole?}`: send part or all of a running investment to another client. See [Sending an investment to another client](#sending-an-investment-to-another-client). |
| `GET /client/transfers`, `GET /client/transfers/{id}`, `POST /client/transfers/{id}/cancel` | Signed-in client | What they sent and received, and cancelling one held for staff. |
| `GET /staff/investment-transfers?status=&limit=`, `GET /staff/investment-transfers/held` | `review_queue.view` | Transfers and their state, and those held for two people. |
| `POST /staff/investment-transfers/{id}/release`, `/approve`, `/withdraw`, `/decline` | `investment_transfers.change` | Proposing, approving or withdrawing the release of a held transfer, with a second person enforced in the database, or declining it. |
| `GET /staff/products`, `GET /staff/products/{code}/rates` | `products.view`, `rates.view` | Products with the rate and tax in force and the next approved one, and each product's rate history. |
| `GET /staff/rate-proposals?status=` | `rates.view` | Rate changes and their state. |
| `POST /staff/rate-proposals`, `POST /staff/rate-proposals/{id}/approve`, `/send-back`, `/withdraw` | `rates.change` | Proposing and deciding rate changes, with the second person enforced in the database. |
| `GET /staff/investments?status=&limit=`, `GET /staff/investments/{id}`, `GET /staff/interest-runs` | `investments.view` | Every investment with its tax, and the record of daily runs. |
| `GET /staff/maturities` | `maturities.view` | The maturity run around today, terms it has not closed, and money held for clients. |
| `GET /staff/investment-corrections?status=&investment_id=&limit=` | `investments.view` | Unwinds and refunds and their state. |
| `POST /staff/investment-corrections`, `POST /staff/investment-corrections/{id}/approve`, `/send-back`, `/withdraw` | `investment_corrections.change`, `Idempotency-Key` | Proposing and deciding an unwind, a refund or a pay out of held money. See [Undoing a mistake](#undoing-a-mistake). |
| `GET /staff/investment-run-requests?status=&limit=` | `investments.view` | Requests to run interest or maturities again, newest first. |
| `POST /staff/investment-run-requests`, `POST /staff/investment-run-requests/{id}/approve`, `/decline`, `/withdraw` | `investment_corrections.change`, `Idempotency-Key` | `{kind, run_date, reason}`: asking for the interest run (`interest`) or the maturity run (`maturity`) again, and deciding it. See [Running a day again](#running-a-day-again). |

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

A retried request or a second start writes nothing new. The client is told when an investment starts (`investment_started`) and when it matures (`investment_matured`), through `notifications.notify` in the same transaction, keyed by the investment. When the daily run is recorded, the same transaction queues `investments.tell_earnings` for the day, so a notice that fails can never leave the money run unrecorded. That job tells each client that the day earned them interest (`interest_earned`): one message per client and currency. No notice names an amount, so the client sees the figure in the app. The variables still carry the day's interest after tax across their investments (the same `interest_today` the app shows, worked out by `domain.interest.day_earnings`) and how many earned. It is keyed by the day and the currency, so a retried run tells nobody twice, a page of clients per transaction. A client whose day earned nothing after tax is not told, and neither is anyone on the day an investment starts.

## Ledger accounts

The investments module opens these. The entries are described with the commands that post them.

| Account | Kind | Holds |
| --- | --- | --- |
| `client.holding`, per client, reference the investment | Liability | The principal of a running investment |
| `client.interest_accrued`, per client, reference the investment | Liability | Interest the client has earned after tax and not yet been paid |
| `tax.withholding_payable` | Liability | Tax held on interest, owed to URA |
| `interest.expense` | Expense | Interest before tax, as it is earned |

## How it joins the other modules

Agreed with the payments and payouts threads on 13 November 2026.

- **Starting.** The client reviews and confirms, which saves an investment that is awaiting payment. Its ID is the purpose of the deposit. Payments asks `investments.service.payable_investment` for the exact amount before taking a deposit. When the deposit is confirmed, payments publishes `payments.deposit_confirmed`, and the `investments.start_from_deposit` job moves the money from `client.awaiting_start` to `client.holding` and starts the investment.
- **Maturity.** See [Maturity](#maturity). Payouts finds the handover by the client and the investment ID (`payouts.domain.posting.funding_account`), and owns everything after `create_payout`.
- **Dealing desk.** It reads the book through `owed_by_product(session, on)`, `running_investments(session, product_code, started_after=None)` and `maturing(session, date_from, date_to)` in `investments.service`, and follows the outbox topics `investments.started`, `investments.matured` and `investments.rolled_over`. Interest in these reads is net of tax, as posted so far.
- **Saving sources.** An investment the client did not make themselves, from an automatic investing run or a goal's saving rule, carries `source_type` (`autoinvest_run` or `goal_saving`) and `source_id`. The columns and their checks are on main (migration 0063): only the worker creates such an investment, at most one per source. The function that creates one, `request_system_investment`, is planned and arrives with the automatic investing work.
- **Carried over from Finvest 1.0.** At the switch the migration module's job calls `investments.service.open_existing_investment` for each running investment, with the old system's start date, rate and tax, its legacy reference, the trial's "as of" day as `interest_through`, and its own opening balances account (an asset `AccountSpec`, so investing never imports migration). One entry debits that account with the principal plus the interest before tax and credits the holding, the interest after tax and the tax payable. The interest is worked out here with `posting_due`, the daily run's own function, and the investment's running totals are set to match, so the daily run carries on from the next day. The migration switch checks the figures against its own `Opening.interest` and stops if they differ. Only the worker opens one (migration 0065), only while it is still running and for days already passed, and the legacy reference is unique, so it is the command's idempotency key: a second call returns the same investment, and the same reference with other terms is refused with `conflict`. It sends no notice and publishes nothing: the client hears from the claim, and the bills behind it are already on the desk's books.
- **Withdrawal started by staff.** Two members of staff may ask for an early withdrawal for a client who asked for it in writing in their help chat, as the owner decided (2026-11-14). The request is checked and paid exactly as the client's own. See [client-accounts.md](client-accounts.md#acting-for-a-client).
- **Unwinding and refunds.** For a deposit credited by mistake, staff propose an unwind and a second person approves it. See [Undoing a mistake](#undoing-a-mistake).

## Early withdrawal

A client may withdraw a running investment early, once, up to the day before a fixed term matures. An open term can be withdrawn at any time. They name one of their approved payout destinations that is past its first day's safety wait, and send an idempotency key (`POST /client/investments/{id}/withdraw`). A destination that could never carry the amount in one payout is refused before anything is saved, with the payouts code (`over_mobile_money_limit`, `over_bank_limit`, `over_daily_limit` or `currency_not_supported`): the amount is final once interest stops, so the request checks it against the account kind's limits. What else the client withdraws the same day is counted when the payout is made.

**The app PIN confirms it on the phone.** Once the client has set an app PIN, the phone app sends `pin_confirmation` with the withdrawal and with a matured pay-out: a confirmation from `POST /client/pin/confirm` with action `withdrawal`, which lasts three minutes and works once, for that session. Without one the route answers 428 `pin_required` and saves nothing. It is checked on top of everything else the request checks, never in place of it. The confirmation is used up in the same transaction as the request, so it is kept if the request is refused or rolled back, and a replay of the same idempotency key answers as before without asking for one. A browser session has no PIN and sends none. Choosing `pay_out` at maturity with a destination (`PUT /client/investments/{id}/at-maturity`) does not ask for the PIN: it can only name an approved payout account, which passed its own checks when it was added, and the PIN too when it was added on a phone that has one. The identity module owns the check (`PinService.spend`); the route hands it to the investing service as a `confirm` callback, so the service imports nothing from identity.

**Switched off until it is paid end to end.** `FINVEST_EARLY_WITHDRAWAL_OPEN` is false by default, everywhere. While it is off, `can_withdraw` is false on every investment and the route answers `conflict` before anything is saved. The withdrawn money is published to the dealing desk, which funds it, and the payout follows; a publish made before the consumer exists is never replayed. Both consumers are now wired and the journey `tests/journeys/test_06_early_withdrawal.py` pays a withdrawal end to end. It stays off in production until the owner says to turn it on.

It pays the principal plus the interest earned after tax so far, with no fee: exactly the `value_minor` the client was shown. Interest stops at the request, so the figure cannot move while the money is found. The owner decided this on 2026-11-13 (see [decisions.md](decisions.md)).

1. **The request.** The client's transaction stamps `withdrawal_requested_at` with PostgreSQL's clock, keeps the destination, queues `investments.withdraw_early`, records `investments.withdrawal_requested` and tells the client (`investment_withdrawal_requested`, keyed and linked by the investment, with no amount in the words). Nothing moves yet. The daily run and the maturity run skip the investment from then on.
2. **The move to payouts.** The worker posts `investment.withdrawn_early`: the holding and the interest go to the client's payout pending account for the investment, in one entry. The investment becomes `withdrawn`, the worker records `investments.withdrawn_early`, and it publishes `investments.withdrawn_early` with `{investment_id, client_id, product_code, principal_minor, currency, amount_minor}` for the dealing desk.
3. **The payout.** The dealing desk funds the withdrawal from the liquidity buffer or a sale and publishes `dealing.withdrawal_funded`. The consumer `investments.pay_early_withdrawal` checks the amount is exactly what was withdrawn, then creates the payout with the key `early:<funding id>`. A refusal fails the job for staff, and the money stays in the payout pending account. Creating the payout sends no notice of its own: payouts tells the client when the money is on its way and when it is sent (`payout_processing`, `payout_sent`), so `investment_withdrawal_paid` is not raised yet, to avoid saying "sent" before it is.

The two consumers are wired: `dealing.events.FUND_EARLY_WITHDRAWAL` is named in the `investments.withdrawn_early` publish, and `investments.events.PAY_EARLY_WITHDRAWAL` in `dealing.withdrawals.FUNDED_CONSUMERS`.

The database fences each step (migration 0064). Only the client asks, once, while the investment runs, before the maturity date and by the database clock. Only the worker withdraws an investment someone asked for. A withdrawn investment changes only to record its payout, once. Accrual and the choice for maturity stop at the request.

## Sending an investment to another client

A client sends part or all of a running investment to another approved client, from the investment's Send screen. Nothing is cashed out and no money leaves Finvest: the ledger moves the holding from one client to the other. The code is `backend/app/modules/investments/transfers.py` (rules in `domain/transfers.py`), and the table is `investment_transfers` (migration 0241).

**The recipient.** The sender types a Finvest account number or a mobile number (`GET /client/transfer-recipients?to=`). The recipient must have an approved identity check and an active sign-in account. They are shown by their first given name and the initial of their surname ("Brian K."), enough to confirm the right person without telling a stranger a full name from a phone number. The name comes through `finvest_kyc_transfer_recipient` (migration 0240), a definer function that answers only for approved checks, because a client cannot read another client's check. Look-ups are throttled to 20 in 15 minutes per client. A client cannot send to themselves.

**The request** (`POST /client/investments/{id}/send`, with an idempotency key) names the recipient's account number, the amount and an optional message of up to 140 characters. It is refused while the client's sign-in is restricted after a code sign-in, and when the investment is not running, has an early withdrawal asked, matures today or has matured, already has an open transfer, or is in another currency. The recipient gets at least the product's minimum, and the sender keeps nothing or at least the minimum, so no investment is ever smaller than the product allows. `can_send` on the client's investment says whether the Send button shows.

**The hold.** A transfer is held for two members of staff when it, with everything else the client sent that day, is above the size at which a payout is held for review (`review_above` in payouts), or when its currency has no such size set. Splitting one large transfer into small ones does not avoid the review. One person proposes letting it through with a reason, and a different person approves; the proposer may withdraw the proposal. One person may decline, with a reason, and the sender may cancel it while it is held. Proposing, approving and declining need `investment_transfers.change`, held by admin, operations and compliance. Held transfers with a proposal show in the approvals inbox as `investment_transfer`.

**Screening.** A sender on a screening hold (a screen under way, an open screening case, or a confirmed sanctions match; see [screening.md](screening.md)) has every transfer held for two members of staff, whatever its size. The column `held_for` says `screening` or `size`, and staff see it; the sender hears exactly what they would for a large transfer. A held transfer cannot be let through while the sender or the recipient is on a hold, so staff decline it or wait for compliance. Just before the money moves, the worker checks both clients again: if either is on a hold, the transfer fails with `not_possible` and nothing moves, and the sender is told only that it could not be made. Migration `0241` enforces the same rule with `screening_payout_clearance`. A client with no screen in the last 24 hours is not held, as a payout would be, because the value never leaves Finvest and every client is screened at approval and every 30 days. For transaction monitoring, the worker transaction that makes a transfer calls `screening.contract.watch_transfer`, and `transfers.for_monitoring` gives screening the transfer that was made: both clients, the amount the recipient got and when. Screening records it as money out for the sender and money in for the recipient ([screening.md](screening.md#transaction-monitoring)).

**The move.** A transfer that is not held, or is released, queues `investments.carry_out_transfer`, keyed by the transfer. The worker checks the investment again (still running, no withdrawal asked, before the maturity date, enough value) and both clients (still active, not on a screening hold, and neither with a request to close their account waiting or approved), and otherwise marks the transfer `failed` with the refusal code and tells the sender, and nothing moves. It posts the interest due up to today first, then posts `investment.transferred` (key `investment-transfer:<id>`) in one entry:

| Line | Account | Amount |
| --- | --- | --- |
| debit | `client.holding`, sender, old investment | principal |
| debit | `client.interest_accrued`, sender, old investment | interest after tax so far |
| credit | `client.holding`, recipient, new investment | the amount sent |
| credit | `client.holding`, sender, new investment | what is left, if anything |

The old investment closes as `transferred`. Two new investments start today in the same product, on the same rate, tax and maturity date, with `term_days` the days left: the recipient's for exactly the amount sent (`source_type` `transfer_received`), and the sender's for the rest (`transfer_kept`). The interest the sender had earned becomes part of what they keep, as at a rollover, and the tax on it stays held. Interest earned between the request and the move only grows what the sender keeps. When the sender asks to send everything (`whole` on the request, which sends the value at the request even if interest was added since they looked), the recipient gets the whole value at the move, including that interest, and the sender keeps nothing. The recipient's investment rolls over at maturity by default and they can change it; the sender's keeps their choice and payout destination. Both earn from the next day, so no day is paid twice and none is missed.

The move publishes `investments.transferred` with `{investment_id, new_investment_id}` to the goals rollover consumer when the sender kept something, so a goal follows the sender's new investment. The recipient's investment belongs to no goal.

**Notices.** The sender gets `investment_sent` and the recipient `investment_received`, both naming the other person by their shown name. A held transfer tells the sender (`investment_transfer_held`), as does a decline (`investment_transfer_declined`) and a failure (`investment_transfer_failed`). No notice names the amount. Statements show the old investment as "Sent to another client", with "Investment sent" and "Investment received" lines.

**Why it is sent.** The sender picks a `purpose`: `family_support`, `gift`, `loan_repayment`, `business` or `other`, and with `other` a `purpose_note` of their own words (required, at most 140 characters; refused with any other purpose as `only_for_other`). It is kept for anti money laundering, shown to staff on `GET /staff/investment-transfers` and never to the recipient. Migration 0243 refuses a new transfer without one and any change to it; transfers saved before it have none.

**Closing an account.** While a client's request to close waits for staff, they cannot send (`conflict`, field problem `account` / `closing`, as for anything else new; see [client-profile.md](client-profile.md)), and nobody can send to them: the sender is told `to` / `no_such_client`, so a stranger learns nothing about their account. Both checks use `closures/pending.py`.

**What each person sees.** `GET /client/transfers` lists what the client sent and, once done, what they received, with the other person's shown name. The recipient sees a transfer only once it is done, so a held or declined transfer is never shown to them. Staff list transfers with `GET /staff/investment-transfers` and the held ones with `/held`.

**What the database enforces** (migration 0241). Only the sender inserts a transfer, from their own running investment with no withdrawal asked, to an approved client who is someone else, stamped near the database's clock, with no decision set. One open transfer per investment. What was asked for never changes. Only staff propose, approve or decline, and never approve their own proposal; only the sender cancels; only the worker finishes one, and a finished one never changes. Only the worker opens the two investments, from a transfer that is waiting, with the product, rate, tax, currency and remaining term of the old one and exactly the amounts above. Only the worker closes the old one, and a transferred investment never changes. An early withdrawal is refused while a transfer is open.

Audit events on resource `investment_transfer`: `investments.transfer.requested` (with whether it was held), `.release_proposed`, `.release_withdrawn`, `.released`, `.declined`, `.cancelled`, `.done` and `.failed`, and `investments.transferred` on the old investment. Details carry IDs and codes only.

## Undoing a mistake

Two staff commands correct money that went the wrong way. Both are maker and checker: one person proposes with a reason, and a different person approves, sends back with a note, or the proposer withdraws it. The code is `backend/app/modules/investments/corrections.py`, and the table is `investment_corrections` (migration 0066). Proposing and deciding need `investment_corrections.change`, held by admin and finance for now. Each one shows in the approvals inbox as `investment_correction`, and the console proposes and decides them on the Investments page.

- **Unwind.** For a running investment that should never have started, for example a deposit credited to the wrong client. The amount is the principal. It is refused if the client asked to withdraw it early, and for an investment carried over from Finvest 1.0 (`carried_over` on `investment_id`), which was never paid for here: a migration mistake is put right by the migration. Nothing leaves a running investment except through maturity or a withdrawal, so no interest has been paid out.
- **Refund.** For money waiting in `client.awaiting_start` for an investment that was cancelled or unwound. It names one of the client's approved payout destinations past its first day's safety wait, checked against the payouts limits as an early withdrawal is. The amount is the whole balance waiting at the proposal.

  **A refund is only for money the client paid in themselves.** It pays the money to that client's own payout account. A deposit credited to the wrong client is put right by payments, which reverses the credit, and never by a refund here: after an unwind the money sits with the client it was wrongly credited to, and a refund would pay it to someone who never sent it. Payments then moves a bank transfer to the right client with two people, or sends mobile money back to the number it came from. The owner confirmed this rule on 2026-11-14 (see [decisions.md](decisions.md)).

- **Pay out.** For the money of a matured investment that waits for the client (neither rolled over nor paid out), when the client asks staff to send it instead of choosing in the app. It names one of the client's approved payout destinations past its first day's safety wait, checked as a refund is. The amount is what the term handed over, principal and interest after tax (migration 0331).

Approving queues `investments.apply_correction`, keyed by the correction, and the worker moves the money, because staff never create payouts:

- An unwind posts `investment.unwound` (key `investment-unwind:<id>`): debit `client.holding` and credit `client.awaiting_start` with the principal, and reverse the interest so far, debiting `client.interest_accrued` with the interest after tax and `tax.withholding_payable` with the tax, and crediting `interest.expense` with the interest before tax. The investment becomes `unwound`. Clients never see an unwound investment.
- A refund posts `investment.refunded`, moving the waiting money to the client's payout pending account for the correction, and creates the payout with the key `refund:<id>` in the same savepoint. A payout refusal rolls both back.
- A pay out posts no entry here: the money is already in the client's payout pending account for the investment. The worker creates the payout from it as the client's own `pay_out_matured` would, with the key `maturity:<investment id>:<destination id>`, and records it on the investment in the same savepoint. Because the key is the client's own, the client and staff can never both send the same money to the same account, and once the payout is recorded the client's request is refused. A payout refusal leaves the money waiting.

The worker checks the figures again first: an unwind needs the investment still running with no request, and the holding and interest balances matching the investment; a refund needs the waiting balance to equal what was approved. If not, the correction is `failed` with a code (`investment_not_running`, `investment_not_refundable`, `investment_not_held` when the client sent the money on meanwhile, `amount_changed`, `holding_changed`, `interest_changed`, `tax_already_remitted` when the tax pool no longer covers the investment's tax because it was paid over to URA, or the payouts code) and nothing moves, so staff propose again. Running the job twice changes nothing.

The dealing desk's reads (`owed_by_product`, `running_investments`, `maturing`) count only running investments, so an unwound one leaves the book at once. The desk's next allocation run releases its bills (`investment_ended`), and any bills left over show as surplus for the desk to sell. A refund lowers the trust cash and the clients' claim together, so the buffer is unchanged. A refund of money that is already in bills is paid straight away from trust cash, before those bills are sold, the same position as an early withdrawal paid from the buffer. The owner decided refunds are not held to the early withdrawal limits (2026-11-14, see [decisions.md](decisions.md)).

The database fences it. Only staff read or write corrections on the API's role, by row level security, so a client transaction sees none. A correction starts as proposed by the person signed in, is decided once by staff, and never by its proposer (`ck_investment_corrections_second_person`), except that only the proposer withdraws it. Only the worker records what an approved one did, and a finished one never changes. One open correction of each kind per investment. Only the worker moves an investment to `unwound`, only from running with no withdrawal request and only with an approved unwind for it, and an unwound investment never changes.

Audit events on resource `investment_correction`: `investments.correction.proposed`, `.approved`, `.sent_back`, `.withdrawn` by the staff member with their reason or note, and `.done` or `.failed` by the system. Details carry IDs and codes only, never an amount. No notice is sent: payouts tells the client about a refund's payout.

## Not built yet

Editing a product, the monthly coupon for open terms, and the early withdrawal screen in the client app. The client app's Send screens and the console's held transfers follow the transfers backend in their own pull requests. The dealing desk is its own module: see [dealing-desk.md](dealing-desk.md).
