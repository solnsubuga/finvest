# Platform settings

The console's Settings screen holds the rules the platform runs on. The trust account clients pay into has its own table and routes ([payments.md](payments.md#the-account-clients-pay-into)), the message wordings and announcements have theirs ([notifications.md](notifications.md)), and the withdrawal limits are versioned by payouts ([payouts.md](payouts.md)). Everything else on the screen is a **platform setting**, kept by the `platform_settings` module.

## How a setting changes

Every change takes two people, as for the trust account:

1. A member of staff proposes a new value, with a reason.
2. A different member of staff approves or rejects it, with a reason and a passkey or app code from the last ten minutes. The proposer may withdraw it instead.
3. An approved value is in use at once. Until a first value is approved, the default in `platform_settings/domain/catalogue.py` is in use, so a new deployment runs as the code says.

Only one change per setting waits at a time, so an approver always approves the value they read. A value is checked when it is proposed and again when it is approved.

## The settings

| Setting | Section | Default | What it means |
| --- | --- | --- | --- |
| `payment_channels` | Payment channels | All open | Which of MTN pay in, Airtel pay in, bank transfer pay in, MTN payout, Airtel payout and bank payout are open. A paused channel refuses new requests only; money already on its way finishes as it would have. |
| `public_holidays` | Calendar | Uganda's fixed-date holidays, Good Friday and Easter Monday, for this year and next | Days that are not working days, besides Saturday and Sunday, at most 60. The two Eid holidays follow the moon and are gazetted each year, so staff add them when announced. |
| `bank_payout_cutoff` | Calendar | 14:00 | The Kampala time after which a bank payout is paid on the next working day. Between 08:00 and 17:00. |
| `maturity_reminders` | Calendar | 7 days and 1 day before | How many days before an investment matures the client is reminded, up to three days between 1 and 30. Empty sends none. |
| `staff_idle_minutes` | Staff security | The deploy's `FINVEST_STAFF_SESSION_IDLE_MINUTES` (30) | Minutes without use after which a member of staff is signed out of the console, 5 to 240. |
| `office_networks` | Staff security | None (any network) | The networks, as addresses or CIDR ranges, staff may use the console from. At most 20, none wider than /8 (IPv4) or /32 (IPv6). |
| `statement_layouts` | Statement import | None | Column layouts for providers' CSV statement exports, beside the built-in `standard` layout. At most 10. |

Each section has its own permission to propose and decide a change. Seeing every section is `settings.view`.

| Section | Permission | Held by |
| --- | --- | --- |
| Payment channels | `payment_channels.change` | admin, operations, finance |
| Calendar | `calendar.change` | admin, operations, finance |
| Staff security | `staff_security.change` | admin |
| Statement import | `statement_layouts.change` | admin, finance |

Operations holds `settings.view` so it can pause a channel during an outage and keep the calendar. It sees the rest of Settings read only.

### Office networks never lock out the people changing them

A list of office networks that leaves out where the proposer is sitting is refused (409, field code `leaves_you_out`), and so is approving one that leaves out the approver. The console shows each member of staff the address their request came from (`your_address`), as the trusted proxy reports it (`FINVEST_TRUSTED_PROXIES`). If the office's own address changes and nobody can reach the console, the infrastructure owner, connected as the table owner `finvest_migrator`, inserts an approved row with an empty list for `office_networks`, with the guard trigger disabled for that one statement, and writes up why in the incident log. That is a break-glass step outside the application, never a route.

The edge can hold a second list, `ADMIN_OFFICE_NETWORKS`, which gates loading the console at admin.finvestug.com ([infra/README.md](../infra/README.md)). It must be the same as the Settings list or wider, so when the office moves, or in a break-glass, widen it too. The check here is the real one: the console calls api.finvestug.com, which the edge list does not cover.

## Routes

| Route | What it does |
| --- | --- |
| `GET /staff/settings/platform` | Every setting with its value in use, when and by whom it was last approved, any change waiting, and whether the reader may change it (`can_change`). Also `your_address` and the date formats a statement layout may use. Needs `settings.view`. |
| `POST /staff/settings/platform/proposals` | `{key, value, reason}`. 201 with the proposal. 403 without the section's permission, 422 naming the field and a code for a bad value, 409 if a change to that setting is already waiting, or `leaves_you_out`. |
| `POST /staff/settings/platform/proposals/{id}/decision` | `{decision: approved, rejected or withdrawn, reason}`. 403 for your own approval, someone else's withdrawal or without the section's permission; `confirm_it_is_you` without a recent second step; 404 once decided. |

Waiting changes appear in the approvals inbox as `payment_channels_change`, `calendar_change`, `staff_security_change` and `statement_layout_change`, with the setting's key as the subject ([approvals.md](approvals.md)).

## What the database enforces

Migration `0340_platform_settings`:

| Rule | How |
| --- | --- |
| Two people | A check constraint: an approval or rejection is by someone other than the proposer, a withdrawal only by the proposer. A trigger requires both to be the acting member of staff. |
| Decided once | A decided row never changes; only the decision columns can be written, once. |
| Nothing deleted | No role may delete. A downgrade refuses to drop the table while it holds rows. |
| Proposals are for staff | Clients and the worker read approved rows only, which is what they need to apply a setting. |

## Where each setting applies

| Setting | Read by | Effect |
| --- | --- | --- |
| `payment_channels` | payments `request_deposit` and the bank transfer details route; payouts `create_payout` | A paused pay in refuses a new payment with 503 `channel_paused`. A paused payout refuses a client's new withdrawal with `channel_paused`; a maturity payout the system asks for is still made |
| `staff_idle_minutes` | identity, on every staff request and when a staff session opens | Once approved, it replaces the deploy setting. A session idle for longer than a newly shortened time ends at its next request. It never stretches past the session's absolute limit |
| `office_networks` | identity, on every staff request | A request from outside every listed network is refused with 403 and field code `outside_office_network`, and does not count as use of the session. The session goes on working from the office |
| `public_holidays`, `bank_payout_cutoff` | payouts `GET /client/withdrawal-limits` | `bank_payout_day`, the day a bank payout asked for now is paid (`catalogue.bank_payout_day`) |
| `maturity_reminders` | investments `remind_maturing`, every morning at 08:30 | Which days before maturity a client is told their investment matures soon |

Complaint deadlines still count Monday to Friday only. Whether public holidays are left out is open for counsel ([decisions.md](decisions.md)); once decided, the complaints rule reads `public_holidays` here.
| `statement_layouts` | reconciliation `POST /staff/reconciliation/statements` and `GET /staff/reconciliation/statement-formats` | A statement can be imported in any approved layout by its name, as well as the built-in `standard` |

## For other modules

Read a setting through `platform_settings.service`: `payment_channels`, `public_holidays`, `bank_payout_cutoff`, `maturity_reminders`, `staff_idle_minutes`, `office_networks` and `statement_layouts`, each taking the session and the time now. Never read the table.
