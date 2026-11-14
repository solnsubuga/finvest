# Insights

What a client put in, earned and was paid, month by month, and what comes next for their money. The code is in `backend/app/modules/insights`. It owns no tables and moves no money. Every figure is read from the ledger or from the investments module's client views, so insights always agree with the books.

## What a client sees

`GET /client/insights?months=6&currency=UGX` answers for the signed-in client only, for 1 to 24 months up to and including this one, in one currency: `UGX` (the default) or `USD`. Amounts are integers in the smallest unit of `currency`, whole shillings or cents. Every figure, including what is invested now and what comes up, counts only that currency's accounts and investments. Figures in different currencies are never added together: a client with dollar investments asks once per currency and sees each on its own.

| Figure | Read from |
| --- | --- |
| Invested in a month | Credits to the client's `client.holding` accounts by `investment.started` entries: new money that started earning. A rollover is not new money, so it is not counted. |
| Earned in a month | Credits to the client's `client.interest_accrued` accounts by `investment.interest_accrued` entries. That account holds interest after tax, so the figure is after tax. |
| Paid out in a month | Debits from the client's `client.payout_in_flight` accounts by `payout.settle` entries: money the provider confirmed reached the client's mobile money or bank account. Less credits to `client.payout_pending` by `payout.returned` entries, in the month the payout came back: a paid payout the bank or network sent back is not paid out. |
| Invested now | The principal of the client's active investments. |
| Earned so far | Interest earned after tax on those investments, as posted to the ledger. |
| Coming up | The next five maturities, with what comes back after tax and whether it rolls over or pays out. |

A month is a Kampala calendar month, counted by the effective date of each ledger entry. A daily interest run that posts several days late in one entry counts them in the month of the day it is dated.

Each month is a net figure. A reversal of one of these entries counts against it in the month the reversal is dated, as it does on the statement, so a correction found next month lowers next month. A month whose reversals outweigh what it added shows as negative.

## Home and Portfolio totals

`GET /client/portfolio?currency=UGX` gives the totals the client app shows on Home and Portfolio, for the signed-in client only, in one currency. Every figure is a sum of the investments module's `client_` views, so it agrees to the shilling with the list of investments. Only running investments count: one waiting for payment has earned nothing and may lapse, so it is only counted in `awaiting_payment_count`.

| Figure | What it is |
| --- | --- |
| `invested_minor` | The principal of the running investments |
| `worth_minor` | What they are worth today: principal plus interest so far, after tax |
| `interest_so_far_minor` | Interest earned after tax, as posted to the ledger |
| `earning_per_day_minor` | What one more day of interest adds across them, after tax: each investment's next posted day. Zero for one that has stopped earning or whose early withdrawal was asked for. Home shows it as "Earning UGX 2,416 a day". |
| `interest_today_minor` | What today's daily run added, once it has run |
| `outside_goals`, `in_goals` | `worth_minor` and a count, split by whether the investment is saved to an open goal (`goals.open_goal_savings`). The two add up to `worth_minor`. A closed goal's savings count as outside goals. |
| `open_goals` | Open goals, whether or not anything is saved to them yet |
| `products` | One row per product, biggest first, with a whole-number `percent` of `worth_minor`. The percentages add up to exactly 100 by the largest remainder method (`insights/domain/shares.py`), so the app draws the split without working anything out. |
| `next_maturity` | The running fixed-term investment that matures soonest, with its term, days earned and days left, or null |

## What it never shows

No tax rate, tax amount or rate before tax. Interest comes only from the after-tax interest account and the investments module's `client_` views, as [investing.md](investing.md) requires.

## The fence

The route opens the client's own transaction, so the ledger's row policies show only that client's accounts. `ledger.statements.client_month_totals` filters by the client too, so asking for another client's figures in a client transaction returns nothing. Staff and the system get `forbidden`: there is no staff view of a client's insights.

## Not built yet

Upcoming runs of automatic investing plans and goal due dates in "coming up". The client app screen runs on a demo until this route is deployed.
