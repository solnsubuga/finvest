# Insights

What a client put in, earned and was paid, month by month, and what comes next for their money. The code is in `backend/app/modules/insights`. It owns no tables and moves no money. Every figure is read from the ledger or from the investments module's client views, so insights always agree with the books.

## What a client sees

`GET /client/insights?months=6` answers for the signed-in client only, for 1 to 24 months up to and including this one. Amounts are integers in the smallest unit of `currency`.

| Figure | Read from |
| --- | --- |
| Invested in a month | Credits to the client's `client.holding` accounts by `investment.started` entries: new money that started earning. A rollover is not new money, so it is not counted. |
| Earned in a month | Credits to the client's `client.interest_accrued` accounts by `investment.interest_accrued` entries. That account holds interest after tax, so the figure is after tax. |
| Paid out in a month | Debits from the client's `client.payout_in_flight` accounts by `payout.settle` entries: money the provider confirmed reached the client's mobile money or bank account. |
| Invested now | The principal of the client's active investments. |
| Earned so far | Interest earned after tax on those investments, as posted to the ledger. |
| Coming up | The next five maturities, with what comes back after tax and whether it rolls over or pays out. |

A month is a Kampala calendar month, counted by the effective date of each ledger entry. A daily interest run that posts several days late in one entry counts them in the month of the day it is dated.

Each month is a net figure. A reversal of one of these entries counts against it in the month the reversal is dated, as it does on the statement, so a correction found next month lowers next month. A month whose reversals outweigh what it added shows as negative.

## What it never shows

No tax rate, tax amount or rate before tax. Interest comes only from the after-tax interest account and the investments module's `client_` views, as [investing.md](investing.md) requires.

## The fence

The route opens the client's own transaction, so the ledger's row policies show only that client's accounts. `ledger.statements.client_month_totals` filters by the client too, so asking for another client's figures in a client transaction returns nothing. Staff and the system get `forbidden`: there is no staff view of a client's insights.

## Not built yet

Upcoming runs of automatic investing plans and goal due dates in "coming up". Insights in dollars, when the dollar product opens. The client app screen runs on a demo until this route is deployed.
