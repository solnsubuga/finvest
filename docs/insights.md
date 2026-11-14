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
| Coming up | The next five things to happen to the client's money, soonest first, each with a `kind`: a `maturity` (what comes back after tax, and whether it rolls over or pays out), an `autoinvest_run` (the next payment an active automatic plan asks for), a `goal_saving` (the next monthly saving of a goal's saving rule, named by the goal) or a `goal_date` (an active goal's date, with its target). A paused plan asks for nothing and is left out. Read from the investments, autoinvest and goals modules' own views. |

A month is a Kampala calendar month, counted by the effective date of each ledger entry. A daily interest run that posts several days late in one entry counts them in the month of the day it is dated.

Each month is a net figure. A reversal of one of these entries counts against it in the month the reversal is dated, as it does on the statement, so a correction found next month lowers next month. A month whose reversals outweigh what it added shows as negative.

## What it never shows

No tax rate, tax amount or rate before tax. Interest comes only from the after-tax interest account and the investments module's `client_` views, as [investing.md](investing.md) requires.

## The fence

The route opens the client's own transaction, so the ledger's row policies show only that client's accounts. `ledger.statements.client_month_totals` filters by the client too, so asking for another client's figures in a client transaction returns nothing. Staff and the system get `forbidden`: there is no staff view of a client's insights.

Each item of `upcoming` has `kind`, `on`, `amount_minor`, and whichever of `investment_id`, `plan_id`, `goal_id`, `product_name`, `goal_name` and `at_maturity` its kind has; the rest are `null`. A goal's name is the client's own words.

The client app's "Coming up" list shows every kind. A maturity opens the investment, an automatic run opens its plan, and a goal's saving or date opens the goal. The "next investment matures" callout above it is about the soonest maturity only.
