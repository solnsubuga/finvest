# Automatic investing

A client sets up a plan to invest the same amount on a schedule, and Finvest asks for each payment on its day. The code is in `backend/app/modules/autoinvest`, and the database side is migration 0120. Other modules import only from `autoinvest/service.py`.

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

## How it joins the other modules

Agreed with the investing and payments threads on 13 November 2026.

- **The investment.** `investments.service.request_planned_investment` saves the investment for a run, as the system acting for the client, with the same checks as one the client confirms by hand: an approved identity check, the product's terms that day and the limit on investments waiting for payment. It is idempotent on the run. The investing module adds the column that links an investment to its run and the worker's right to insert it.
- **The payment.** `payments.service.request_planned_deposit` saves a mobile money deposit for that investment and its send job in the same transaction. It is idempotent through the deposit for that investment, because the idempotency table belongs to clients and staff. Payments adds the worker's right to insert it, in a payments migration.
- **The outcome.** When the deposit is confirmed, the investment starts in the usual way and the run is `paid`. When it fails, the run is `failed` and, on its first attempt, retried the next day.

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

## Not built yet

Asking for the investment and the payment for each run, and settling its outcome, wait for the investing module's maturity migration and the payments module's bank transfer change, and arrive in the next pull request. Until then the scheduler does not run `autoinvest.open_due_runs`, so no run is opened in a deployed system. Also not built: staff routes for the console, telling the client about a failed or missed run (it waits for notifications), approving a new plan with the app PIN (it waits for devices and PINs), pausing a client's plans when their phone number changes. The client app screens run on a demo until they are wired to these routes.
