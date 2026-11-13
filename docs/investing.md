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

## What clients see

Clients see the yearly rate after tax and the interest they earn after tax. They never see the tax rate, the tax amount or the rate before tax. In the code, `ClientReturns` and `RateVersion.client_rate_percent` are the only figures a client-facing response is built from, and the service functions `client_products` and `client_quote` are the only ones a client-facing route may call. `client_quote` is the returns calculator and the review screen: what an amount earns after tax if it starts today, and when it matures. The rate shown is a label, rounded to two decimal places. No amount is ever worked out from it.

## An investment

One payment by one client into one product. The code is in `backend/app/modules/investments/holdings.py` and `jobs.py`, and the table is migration 0061.

| Status | Meaning |
| --- | --- |
| `awaiting_payment` | The client reviewed and confirmed. The terms are fixed. Nothing has been paid. |
| `active` | The payment was confirmed and the money is in the client's holding. It earns interest from the next day. |
| `cancelled` | It was not paid for in time. Final. No money ever moved. |

1. **Review and confirm.** `request_investment` saves the investment under the client's idempotency key, so two taps invest once. The client must have passed the identity check. The terms are the product's rate and tax that day, and the investment keeps them for good.
2. **Pay.** The investment's ID is the purpose of the deposit. Payments asks `payable_investment` who pays and exactly how much. A review stays payable for 72 hours.
3. **Start.** When the deposit is confirmed, payments publishes `payments.deposit_confirmed` and the job `investments.start_from_deposit` runs `start_investment`. The whole amount must be waiting in `client.awaiting_start` for this investment, no less and no more. One entry moves it to `client.holding`, and `investments.started` is published.
4. **Or not.** If the money waiting is not exactly the amount, nothing moves, the job fails for good and staff see it among the failed jobs. An investment nobody paid for is cancelled an hour after its window closes, by a sweep every 15 minutes. One with any money waiting is never cancelled.

A client can have five investments waiting for payment at a time.

### The start date

The start date is the business day, in Kampala, on which the payment was confirmed. It is not the day of the review, and not the day the start was processed. If the worker is slow or down overnight, the client still earns from the day their money arrived. Payments sends that day in the event as `confirmed_on`.

### What the database enforces

| Rule | How |
| --- | --- |
| The terms are the product's | A trigger checks a new investment against the catalogue itself: the product is live, the currency and term are the product's, the amount meets the minimum, and the rate and tax are the ones in force that day. |
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

## Ledger accounts

The investments module opens these. The entries are added with the commands that post them.

| Account | Kind | Holds |
| --- | --- | --- |
| `client.holding`, per client, reference the investment | Liability | The principal of a running investment |
| `client.interest_accrued`, per client, reference the investment | Liability | Interest the client has earned after tax and not yet been paid |
| `tax.withholding_payable` | Liability | Tax held on interest, owed to URA |
| `interest.expense` | Expense | Interest before tax, as it is earned |

## How it joins the other modules

Agreed with the payments and payouts threads on 13 November 2026. Starting is built. Maturity and unwinding are not.

- **Starting.** The client reviews and confirms, which saves an investment that is awaiting payment. Its ID is the purpose of the deposit. Payments asks `investments.service.payable_investment` for the exact amount before taking a deposit. When the deposit is confirmed, payments publishes `payments.deposit_confirmed`, and the `investments.start_from_deposit` job moves the money from `client.awaiting_start` to `client.holding` and starts the investment.
- **Maturity.** The client chooses in advance to roll over or be paid out. For a payout, the maturity run moves principal and interest to `client.payout_pending` (reference the investment) and calls `payouts.payouts.create_payout` with that account, in one transaction. If the payout is refused the money stays there and appears in the payouts staff queue.
- **Unwinding.** For a deposit credited by mistake, `investments.service.unwind_investment` will move the principal back to `client.awaiting_start` and reverse interest not yet paid out. It refuses if anything was already paid out.

## Not built yet

HTTP routes (they need sign-in sessions and permissions), editing a product, the daily interest run, maturity and rollover, early withdrawal, the monthly coupon for open terms, statements, and every screen. The dealing desk is its own module: see [dealing-desk.md](dealing-desk.md).
