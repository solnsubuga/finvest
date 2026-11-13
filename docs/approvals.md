# Approvals inbox

Most staff operations need two people: one proposes, a different one approves (see the maker and checker rule in [decisions.md](decisions.md)). Each module keeps its own proposals and enforces the rule in PostgreSQL. The approvals inbox gathers what is waiting into one list for the console, so nobody has to visit every screen to find work.

## The route

`GET /staff/approvals` returns every item waiting for a second person that the reader may see, oldest first. Every role holds `overview.view`, so every member of staff has an inbox. What is in it depends on the rest of their permissions.

Each item has:

| Field | Meaning |
| --- | --- |
| `kind` | What it is, from the table below |
| `id` | The item's ID in its own module |
| `subject` | A short label from the item, such as a product code, a date or a message name. Never personal data. |
| `proposed_by`, `proposed_at` | Who asked, and when |
| `yours` | The reader proposed it. They can only withdraw it. |
| `can_decide` | The reader may approve or reject it |

The inbox only reads. Deciding happens on the owning module's own route, which checks its permission again and refuses the person who proposed the item.

## What it gathers

| Kind | Module and read function | Seen with | Decided with |
| --- | --- | --- | --- |
| `wording_change` | notifications `wording_store.list_wordings` | `settings.view` | `wordings.change` |
| `ledger_correction` | ledger `corrections.pending_corrections` | `finance.view` | `finance.view` |
| `pot_transfer` | reconciliation `transfers.waiting` | `reconciliation.view` | `reconciliation.view` |
| `bank_transfer_match` | payments `bank_transfers.matches_awaiting_approval` | `reconciliation.view` | `bank_transfers.change` |
| `day_close` | reconciliation `day_close.prepared_closes` | `reconciliation.view` | `reconciliation.view` |
| `dealing_order` | dealing `orders.open_orders`, prepared | `dealing_desk.view` | `dealing_desk.view` |
| `dealing_result` | dealing `orders.open_orders`, result recorded | `dealing_desk.view` | `dealing_desk.view` |
| `rate_change` | investments `list_proposals`, proposed | `rates.view` | `rates.view` |
| `migration_totals` | migration `overview`, totals not yet confirmed | `migration.view` | `migration.sign_off` |
| `migration_sign_off` | migration `overview`, sign-offs proposed | `migration.view` | `migration.sign_off` |
| `payout_account` | payouts `destinations.checks_awaiting_approval` | `review_queue.view` | `payout_accounts.change` |

Where a module has no separate deciding permission, the screen's own is used. Team changes, client locks and email suppression lifts join the table when their pull requests merge.

Only the sources the reader's permissions show are read, and all of them inside the reader's staff transaction, so row policies apply as they do on each module's own screen.

Not in the inbox, because they are not two-person decisions: identity checks (one reviewer, by the owner's decision), large payouts and withdrawal limits, refusing or removing a payout account, products and support.

## Where it lives

`app/modules/approvals` owns no tables. `domain/inbox.py` holds the pure rules (what each permission shows, `yours` and `can_decide`, the order), `service.py` reads the modules and `api.py` serves the route.
