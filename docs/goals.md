# Goals

How a client saves towards something: school fees, land, an emergency fund. The code is in `backend/app/modules/goals`, and the database side is migrations 0110 to 0112.

## A goal holds no money

There is no wallet, so a goal is not a pot. It is a target attached to the client's investments.

- A goal has a name, what it is for, a target amount and, usually, a date.
- Each saving towards it is a new investment in the goal's product, confirmed by the client and paid for by mobile money or bank transfer exactly as any other investment ([payments.md](payments.md)). It starts when the payment is confirmed, earns interest daily and rolls over at maturity.
- The goal's value is the value of its running investments: what was paid in plus interest after tax, read from the investments module, which reads the ledger. It cannot drift from the books.
- A saving the client confirmed but has not paid for yet is shown as awaiting payment and is not counted in the value.
- Every saving must meet the product's minimum, because it is an investment of its own.

The goals module never posts to the ledger, and never writes to the investments tables. It asks `investments.service.request_investment` for each saving and records in `goal_savings` which goal the investment was made for.

## The plan

The plan answers "how much a month gets me there by the date". It is an estimate the client is shown, at today's rates and after tax. No money moves by it. It is worked out in `domain/plans.py`, in integer arithmetic.

- Savings are monthly from today, on the same day of the month, or the 28th at the latest.
- A saving is planned only if its first term ends on or before the goal's date, so the money is back in time.
- Each saving earns interest only for whole terms that end by the date, with simple interest. What is already saved earns nothing more in the estimate. Each simplification can only make the estimate lower than what really happens.
- The monthly saving is the smallest whole UGX 1,000 (or whole dollar) that reaches the target. If that is below the product's minimum, the plan asks for the minimum and says so.
- Of the live fixed-term products, the plan uses the one that asks for the smallest monthly saving. A longer bill pays more, but fewer savings fit before the date, so a year away the 91 day bill usually asks for less than the 182 day one.
- A goal with no date has no plan, and its savings go to the shortest term, which keeps money closest to hand.
- A goal sooner than every term is refused, because no saving could mature in time.

Once a goal is set up, its plan is worked out again on every read for what is still needed, after what is running and what is awaiting payment.

## How a goal is doing

| Label | When |
| --- | --- |
| Reached | The value is at or above the target |
| On track | The value is at least where a straight line from the day the goal was set up to its date would be today |
| Behind | Short of that line |
| No date | A goal with no date is neither ahead nor behind |

## Changing and closing

The client can change the name, what it is for, the target and the date. A new target or date can change the product for later savings. Savings already made are not touched. The currency never changes.

Closing a goal is final. Its investments carry on as the client's ordinary investments, because they are the client's own and closing moves no money. A closed goal takes no saving.

## Rolling over

A saving that rolls over at maturity becomes a new investment for the proceeds, principal and interest after tax. The investments module publishes `investments.rolled_over` with the consumer `goals.follow_rollover` (named in `goals/events.py`, so investing imports nothing else of goals). The worker then links the new investment to the same goal, so the goal's value carries on. Only running investments count towards the value, so the matured one stays in the list as a record and is not counted twice.

A closed goal is not followed: its investments keep rolling over as the client's own. Following the same rollover again changes nothing.

## Group goals

One target that several clients save towards together, such as a family buying land. Decided for now by Solo (2026-11-13): a shared target, with each member saving into their own investments. Nothing is pooled, nothing needs an approval, and every member can leave with their own money. It is ordinary investing with a shared progress bar, so it stays clear of the open question of whether a pooled group is a collective investment scheme. The code is `groups.py` and `domain/groups.py`, and the database side is migration 0112.

- **Setting up.** The client gives the group's name, what it is for, the shared target and date, the name the others will see them by, and their own part. Their part is a goal of their own, with the group's date and their part as its target. They save into it like any goal.
- **Inviting.** The one who set the group up makes an invite code and sends the link by WhatsApp or SMS. It works for 7 days. A new code replaces the old one, which stops working, and the code can be withdrawn. Only its SHA-256 is stored, so it is shown once.
- **Joining.** With the code, a client sees the group's name, target, date, how many are in it and the first member's name, and nothing else. Joining makes them a goal of their own named after the group, for their own part. A client is in a group once at a time, and a group has at most 10 members.
- **What members see.** The names each member chose, when they joined, and the combined value against the target, with the same On track or Behind labels as any goal. Never another member's savings, amounts or investments one by one. With two members, each can work out the other's total from the combined value, and the app says so before joining.
- **The combined value.** The member's own goal is counted as it is now. The others are counted as the worker last recorded them, once a day at 01:30 Kampala time, after interest and maturities (`goals.refresh_groups`). The app shows that day. A client never reads another client's investments, and never writes a figure the group is shown.
- **Changing.** The one who set the group up changes its name, target and date. No member's own goal changes with it: each changes their own if they want to.
- **Leaving.** A member leaves, or the one who set the group up removes them. Their goal and its savings carry on as their own. Closing their own goal also leaves the group. The one who set the group up cannot leave it or close their goal while it is open: they close the group.
- **Closing.** Final. Every member's goal stays open as their own, and nobody's money moves.

The database holds these, in migration 0112:

- Only the one who set a group up changes it, invites, removes members or closes it. A closed group never changes, and nobody joins it.
- A member joins with their own active goal in the group's currency, either as the one who set it up or with the group's current invite. Whether the invite is in date is checked by the application, against the injected clock. The count of 10 locks the group, so two joining at once cannot pass it.
- Members, the one who set the group up and staff read a group. Nobody else does. Staff change nothing.
- A member row keeps its group, goal, client and joining time. Only the member changes their name. Leaving happens once, for good.
- Only the worker, as the system, writes the figure the group is shown.
- Nobody deletes a group or a member, and a downgrade refuses to drop the tables while they hold rows.

## Limits

- 20 open goals per client, checked under a per-client lock so two at once cannot pass it.
- Goal names up to 60 characters, extra spaces removed.
- A date no more than ten years away.

## What the database enforces

- A client creates and changes only their own goals. Staff read every goal and change none. The worker reads.
- A goal begins active, keeps its client, currency and start, and never changes once closed.
- A saving belongs to one goal of the same client. It is never changed or removed by any role, and it is refused for a goal that is not active. The check locks the goal, so a saving and a close at the same moment cannot cross.
- Only the worker records a rollover, as the system, and only from a saving already linked to the same goal. A saving rolls over once. A client's own saving never names one.
- The guards run with `search_path = pg_catalog, pg_temp` and name every table in full. The savings guard runs as its owner, so the worker can lock the goal without holding UPDATE on it.
- Nobody deletes a goal, and a downgrade refuses to drop the tables while they hold rows.

## API

| Route | Use |
| --- | --- |
| `GET /client/goals/plan?target_minor=&target_date=` | The plan before a goal is saved. `null` when no term fits. |
| `POST /client/goals` | Set up a goal. Takes an `Idempotency-Key`. |
| `GET /client/goals`, `GET /client/goals/{id}` | The client's goals with progress, plan and savings |
| `PUT /client/goals/{id}` | Change the name, what it is for, the target or the date |
| `POST /client/goals/{id}/close` | Close it |
| `POST /client/goals/{id}/savings` | Confirm a saving: returns the new investment awaiting payment. Takes an `Idempotency-Key`. Pay for it with the investment's ID as the purpose. |
| `GET /staff/goals`, `GET /staff/goals/{id}` | Staff with `goals.view` |
| `POST /client/goal-groups` | Set up a group goal, with the client's own part. Takes an `Idempotency-Key`. |
| `GET /client/goal-groups`, `GET /client/goal-groups/{id}` | The groups the client is in, with members' names and the combined value |
| `PUT /client/goal-groups/{id}` | Change the group's name, target or date. Only for the one who set it up. |
| `PUT /client/goal-groups/{id}/me` | Change the name the others see the client by |
| `POST /client/goal-groups/{id}/invite`, `DELETE /client/goal-groups/{id}/invite` | Make a new invite code, shown once, or withdraw it |
| `GET /client/goal-groups/invites/{code}` | What the invite is for, before joining |
| `POST /client/goal-groups/invites/{code}/join` | Join, with the client's own part. Takes an `Idempotency-Key`. |
| `POST /client/goal-groups/{id}/leave` | Leave. The client's own goal carries on. |
| `POST /client/goal-groups/{id}/members/{goal_id}/remove` | Remove a member. Only for the one who set it up. |
| `POST /client/goal-groups/{id}/close` | Close the group for good |
| `GET /staff/goal-groups`, `GET /staff/goal-groups/{id}` | Staff with `goals.view`, with each member's figure as last recorded |

Client responses carry net figures and the yearly rate after tax as a label. No tax rate, tax amount or figure before tax.

## Not built yet

- Saving rules: a monthly saving collected on a set day, with a mobile money prompt sent by the worker.
- Screens for group goals in the client app and the console.
- Withdrawing from a goal before its date, which waits for early withdrawal.
