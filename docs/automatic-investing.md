# Automatic investing

A client sets up a plan to invest the same amount on a schedule, and Finvest asks for each payment on its day. The code is in `backend/app/modules/autoinvest`, and the database side is migration 0120, with 0300 for the deposit the worker saves. Other modules import only from `autoinvest/service.py`.

There is no wallet, so a plan never draws on a balance. On each due day the worker saves an investment awaiting payment and sends a mobile money prompt for exactly that amount. The client approves the prompt on their phone with their mobile money PIN, as for any other payment. Each run is one ordinary investment, with the rate in force on its own day.

## A plan

| Field | Rule |
| --- | --- |
| Product | Any live product in shillings. The amount must meet the product's minimum, because each run is a whole investment. |
| Amount | Whole shillings, up to UGX 5,000,000 a run, the most one mobile money prompt asks for. A larger amount can be invested by hand by bank transfer. |
| Schedule | Weekly on a weekday, or monthly on a day from 1 to 31. In a month without that day the run is on the month's last day, so a plan for the 31st runs on 30 April and 28 February. |
| Payer | MTN or Airtel, and the number on the client's own account. A number registered to anyone else is refused. |
| At maturity | Roll over or pay out, as for an investment made by hand. Roll over if left out. |

A client keeps at most ten plans that are not stopped.

| Status | Meaning |
| --- | --- |
| `active` | Runs on each due day. |
| `paused` | Makes no runs. Resuming picks the next due day after the day it resumes. Days that passed while paused are not made up. |
| `stopped` | Final. Kept as a record, and never runs again. |

A plan's first run is on its first due day after the day it is set up, never the same day: a prompt arriving minutes after setting a plan up would surprise the client, who can invest today by hand. Changing a plan's schedule works out the next due day again the same way. Changing only the amount or the product keeps the next due day.

Setting a plan up takes an idempotency key, so two taps make one plan. Setting up, changing, pausing, resuming and stopping are each recorded in the audit trail with the product and the frequency, never the amount or the number.

## A run

One due day of one plan.

| Status | Meaning |
| --- | --- |
| `due` | Saved, waiting to ask for the investment and the payment. |
| `requested` | The investment is saved and the prompt is on its way to the phone. |
| `paid` | The payment was confirmed, so the investment has started. Final. |
| `failed` | The payment failed or was not approved. A first failure is tried once more the next day. A second is final. |
| `missed` | The day passed without a request. Final. Nothing is ever collected late. |

The daily run opens runs every 15 minutes between 08:00 and 20:00 in Kampala, so no prompt reaches a phone at night. For each active plan whose next due day has come:

1. A due day of today or yesterday is collected today. The run may have fired late, or the system may have been down overnight.
2. An older due day is recorded as `missed` with the code `late`. A week's or a month's saving is never collected late, and never two at once.
3. If the plan's previous run is still open, waiting for its payment to settle, this day is `missed` with the code `previous_run_open`, so a client never has two prompts from one plan.
4. The plan moves on to its next due day after today.

A failed first attempt is retried the next day. If the plan was paused or stopped since, the retry is `missed` with the code `plan_not_active`.

### What the database enforces

| Rule | How |
| --- | --- |
| A day is collected once | A unique key on the plan and the due day. |
| One prompt at a time | A unique index allows one open run (`due` or `requested`) per plan. |
| A plan starts tomorrow at the earliest | A trigger refuses a new or rescheduled plan whose next due day is not after the day of the change, in Kampala. |
| Whose plan it is never changes | The trigger refuses any change to a plan's client, currency or creation. |
| A stopped plan is final | The trigger refuses any change to it. |
| Only the client changes their plan | The API role holds UPDATE on the client's own columns, the trigger requires the client's own actor, and a row policy fences each client to their own plans. |
| The worker only moves a plan forwards | The worker holds UPDATE on the next due day only, and the trigger refuses moving it backwards or touching a plan that is not active. |
| Only the worker records runs | The API role holds SELECT only. The trigger requires the system actor on the worker's role, starts every run `due` or `missed` for its own plan's client, and allows only the moves in the table above. |
| Two tries at most | A check constraint holds the attempt to 1 or 2, and the trigger allows a failed run back to `due` only as its second attempt. |
| Nothing is deleted | No role holds DELETE on either table. |

## Asking for a run

The scheduler fires `autoinvest.open_due_runs` every 15 minutes, which opens the day's runs as above and queues one `autoinvest.request_run` job for each run that is due, keyed by the run and the day. The job, in one transaction as the system acting for the client (`requests.py`):

1. Leaves the run alone if it is no longer `due`, so a repeated job does nothing. Outside 08:00 to 20:00 in Kampala it sends nothing and the run waits.
2. Records a run of a plan paused or stopped since as `missed` (`plan_not_active`).
3. Reads the number on the client's account from identity. A locked, closed or unclaimed account fails the run (`client_not_active`). A number that is no longer the plan's payer number fails it too (`phone_changed`): a plan prompts only the account's own number, so the client sets the plan up again with the new one. Neither is retried.
4. Saves the investment awaiting payment with `investments.service.request_system_investment`, with the same checks as one the client confirms by hand: an approved identity check, an account not closing, the product's terms that day and the cap of five investments waiting for payment. The investment records the run as its source (`source_type` `autoinvest_run`), so a run has one investment, and the retry the next day finds the same one. A refusal fails the run with `investment_<code>`, such as `investment_conflict` when five are already waiting. A retry whose investment has been cancelled or whose 72 hour payment window closed fails with `investment_closed`. That happens when the retry lands more than 72 hours after the first attempt, for example when the system was down over a weekend or a holiday: the run then ends, and the plan carries on from its next day.
5. Saves the mobile money deposit for exactly the amount, from the plan's network and number, with `payments.service.request_system_deposit`, which also queues its send job. If the investment already has a deposit that has not failed, the run follows that one and no second prompt is sent.
6. Marks the run `requested`, with the investment and the deposit.

The payments worker then prompts the phone as for any payment ([payments.md](payments.md)). The client approves with their mobile money PIN. The app PIN is not asked: it confirms money leaving, and a prompt the client did not expect can simply be declined.

`autoinvest.settle_runs` runs every 5 minutes, at any hour since it sends nothing. For each `requested` run it reads the deposit and the investment, which their own modules own:

| What it finds | The run |
| --- | --- |
| The deposit is confirmed and the investment has started | `paid`. The client already heard from investing that the investment started. |
| The deposit is confirmed and the investment has not started yet | Waits. Investing starts it from `payments.deposit_confirmed`. |
| The deposit failed | `failed` with the deposit's code, such as `declined` or `insufficient_funds`. A first attempt is retried the next day. |
| The deposit is still open or uncertain | Waits. Reconciliation settles an uncertain deposit, never this sweep. The plan's next day is missed meanwhile, so the client is never prompted twice. |

A run still `due` a day after it was opened lost its request job, for example because the worker was down all day. The sweep records it as `missed` with the code `late`. It is never asked for late.

### What the client is told

Through the notifications module, linked to the plan, naming the due day and never the amount:

| Event | When |
| --- | --- |
| `autoinvest_run_retrying` | A first attempt failed. The prompt comes again tomorrow. |
| `autoinvest_run_failed` | A run failed for good: its second attempt, or a reason a retry cannot fix. |
| `autoinvest_run_missed` | A run was missed because it was late (`late`) or the previous payment was still open (`previous_run_open`). A plan the client paused or stopped, or an account they asked to close, sends nothing. |

Each is sent once per run and attempt, so a repeated job tells nobody twice.

## How it joins the other modules

Agreed with the investing and payments threads on 13 November 2026, and built on 14 November.

- **The investment.** `investments.service.request_system_investment(session, *, client_id, source_type, source_id, product_code, amount, at_maturity, clock)`. Idempotent on the source. The worker's right to insert an investment with a source is in investing's migration 0063.
- **The payment.** `payments.service.request_system_deposit(session, request, *, client_id, account_phone, provider, clock)`. It is idempotent through the deposit for that investment, because the idempotency table belongs to clients and staff. Migration 0300 gives the worker INSERT on deposits, fenced by a restrictive row policy to a new mobile money deposit for an investment: `created`, MTN or Airtel, with no provider reference, ledger entry or settlement.
- **The outcome.** Read back by the settling sweep, through `payments.service.find_deposit` and `investments.service.get_investment`. Neither module knows about runs.

The jobs run only where a collections provider is configured: without one the scheduler fires none of them and the worker registers no request handler.

## Client routes

Under `/client/autoinvest/plans`, for the signed-in client only. Amounts are integers in the smallest unit with their currency. Every route answers with a plan: `plan_id`, `product_code`, `product_name`, `currency`, `amount_minor`, `frequency`, `day`, `network`, `payer_msisdn`, `at_maturity`, `status`, `next_run_on`, `created_at`, `paused_at`, `stopped_at` and `runs`. `runs` is latest first, and each run has `run_id`, `due_on`, `status`, `attempt`, `retry_on`, `code` and `investment_id`. The list leaves `runs` empty.

| Route | Does |
| --- | --- |
| `GET ?include_stopped=false` | The client's plans, newest first, as `{"plans": [...]}`. |
| `POST` | Sets up a plan from `product_code`, `amount_minor`, `currency` (UGX), `frequency`, `day`, `network`, an optional `phone` and an optional `at_maturity`. It needs an `Idempotency-Key`. The same key returns the same plan, and the same key with other details is refused with `idempotency_key_reused` (422). Answers 201. |
| `GET /{plan_id}` | One plan with its runs. |
| `PUT /{plan_id}` | Replaces what the plan does, with the same body as `POST`. |
| `POST /{plan_id}/pause`, `/resume`, `/stop` | Sets the state. Sending one again changes nothing, so they carry no key. |

`phone` defaults to the number on the account, and any other number is refused on `phone`. A client whose identity check is not approved gets `forbidden` (403). The eleventh open plan, or resuming a stopped plan, gets `conflict` (409). Another client's plan is `not_found` (404). An amount, day or product that no run could be paid for gets `validation_failed` (422) with the field.

## Staff routes

The console's Automatic Investing screen reads two routes, both needing `automatic_investing.view` (admin, operations and finance). They only read: a run asks a client for money, so staff never start, retry or change one.

| Route | What it gives |
| --- | --- |
| `GET /staff/autoinvest/plans/counts` | How many plans are active and paused. |
| `GET /staff/autoinvest/runs?date_from=&date_to=&status=&before_id=&limit=` | Runs due in the range (at most 92 days), newest first, 200 a page, each with its plan's terms now and the client's ID. `next_before_id` reads the next page. Every read is an audit event (`autoinvest.runs_viewed`). |

The console names each client through `/staff/clients/cards`; this module never reads the identity tables.

## Not built yet

Pausing a client's plans when their phone number changes: until then each run of such a plan fails with `phone_changed` and the client is told. Approving a new plan with the app PIN is not planned: a plan only ever asks for money coming in, which the client approves prompt by prompt.
