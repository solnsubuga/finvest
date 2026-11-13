# Investing

Products, rates, interest and tax, and the rules in [rules.md](rules.md) that govern them. The pure rules are in `backend/app/modules/investments/domain`, the catalogue and rate commands in `backend/app/modules/investments/service.py`, and the database side is migration 0060. Investments, the daily interest run, maturity and the screens arrive in later pull requests, and this document grows with them.

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

Worked example: UGX 1,000,000 in the 91 day bill earns 31,164 before tax over the term, 4,674 is tax, and the client earns 26,490 and gets back 1,026,490.

## What clients see

Clients see the yearly rate after tax and the interest they earn after tax. They never see the tax rate, the tax amount or the rate before tax. In the code, `ClientReturns` and `RateVersion.client_rate_percent` are the only figures a client-facing response is built from, and the service functions `client_products` and `client_quote` are the only ones a client-facing route may call. `client_quote` is the returns calculator and the review screen: what an amount earns after tax if it starts today, and when it matures. The rate shown is a label, rounded to two decimal places. No amount is ever worked out from it.

## Ledger accounts

The investments module opens these. The entries are added with the commands that post them.

| Account | Kind | Holds |
| --- | --- | --- |
| `client.holding`, per client, reference the investment | Liability | The principal of a running investment |
| `client.interest_accrued`, per client, reference the investment | Liability | Interest the client has earned after tax and not yet been paid |
| `tax.withholding_payable` | Liability | Tax held on interest, owed to URA |
| `interest.expense` | Expense | Interest before tax, as it is earned |

## How it joins the other modules

Agreed with the payments and payouts threads on 13 November 2026. None of it is built yet.

- **Starting.** The client reviews and confirms, which saves an investment that is awaiting payment. Its ID is the purpose of the deposit. Payments asks `investments.service.payable_investment` for the exact amount before taking a deposit. When the deposit is confirmed, payments publishes `payments.deposit_confirmed`, and the `investments.start_from_deposit` job moves the money from `client.awaiting_start` to `client.holding` and starts the investment.
- **Maturity.** The client chooses in advance to roll over or be paid out. For a payout, the maturity run moves principal and interest to `client.payout_pending` (reference the investment) and calls `payouts.payouts.create_payout` with that account, in one transaction. If the payout is refused the money stays there and appears in the payouts staff queue.
- **Unwinding.** For a deposit credited by mistake, `investments.service.unwind_investment` will move the principal back to `client.awaiting_start` and reverse interest not yet paid out. It refuses if anything was already paid out.

## Not built yet

HTTP routes for the catalogue and rates (they need sign-in sessions and permissions), editing a product, investments and their life cycle, the daily interest run, maturity and rollover, early withdrawal, the monthly coupon for open terms, the dealing desk, statements, and every screen.
