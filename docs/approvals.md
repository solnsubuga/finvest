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
| `proposed_by`, `proposed_by_name`, `proposed_at` | Who asked, by ID and name, and when |
| `yours` | The reader proposed it. They can only withdraw it. |
| `can_decide` | The reader may approve or reject it |

The inbox only reads. Deciding happens on the owning module's own route, which checks its permission again and refuses the person who proposed the item.

## What it gathers

| Kind | Module and read function | Seen with | Decided with |
| --- | --- | --- | --- |
| `wording_change` | notifications `wording_store.list_wordings` | `settings.view` | `wordings.change` |
| `ledger_correction` | ledger `corrections.pending_corrections` | `finance.view` | `finance.view` |
| `pot_transfer` | reconciliation `transfers.waiting` | `reconciliation.view` | `reconciliation.change` |
| `bank_transfer_match` | payments `bank_transfers.matches_awaiting_approval` | `reconciliation.view` | `bank_transfers.change` |
| `day_close` | reconciliation `day_close.prepared_closes` | `reconciliation.view` | `reconciliation.change` |
| `dealing_order` | dealing `orders.open_orders`, prepared | `dealing_desk.view` | `dealing_orders.change` |
| `dealing_result` | dealing `orders.open_orders`, result recorded | `dealing_desk.view` | `dealing_orders.change` |
| `buffer_rules` | dealing `buffer.waiting_proposals`. The subject is the currency and the day the rules would start, such as `UGX from 2026-12-01`. | `dealing_desk.view` | `dealing_orders.change` |
| `buffer_capital` | dealing `capital.waiting`: Finvest's own money paid into or taken out of the buffer. The subject is `UGX paid in` or `UGX taken out`, with no amount. | `dealing_desk.view` | `dealing_orders.change` |
| `rate_change` | investments `list_proposals`, proposed | `rates.view` | `rates.view` |
| `migration_totals` | migration `overview`, totals not yet confirmed | `migration.view` | `migration.sign_off` |
| `migration_sign_off` | migration `overview`, sign-offs proposed | `migration.view` | `migration.sign_off` |
| `phone_change` | identity `phone_changes.waiting` | `client_phones.change` | `client_phones.change` |
| `team_change` | identity `team.open_changes`. The person it changes never decides it. | `team.view` | `team.change` |
| `client_lock` | identity `client_locks.open_locks` | `clients.view` | `clients.lock` |
| `suppression_lift` | notifications `suppression_lifts.open_lifts`. The subject is the start of the address hash. | `clients.view` | `email_suppressions.lift` |
| `complaint_outcome` | support `complaints.pending_outcomes`. The subject is the complaint's reference. | `complaints.view` | `complaints.close` |
| `announcement` | notifications `announcements.list_announcements`, proposed | `settings.view` | `announcements.send` |
| `bank_account_change` | payments `bank_accounts.list_accounts`, a change waiting. The subject is the currency, never the account details. | `settings.view` | `bank_accounts.change` |
| `payout_resolution` | payouts `resolutions.waiting` | `reconciliation.view` | `reconciliation.change` |
| `deposit_reassignment` | payments `reassignments.proposals_awaiting_approval`. The subject is the deposit's ID. | `reconciliation.view` | `deposit_reassignments.change` |
| `payout_account` | payouts `destinations.checks_awaiting_approval`: a check recorded, not yet approved | `review_queue.view` | `payout_accounts.change` |
| `payout_release` | payouts `reviews.waiting`: a proposal to release a payout held because it is large. The subject is the payout's ID. | `review_queue.view` | `payout_accounts.change` |
| `compliance_case` | screening `service.waiting_proposals`: a proposal to close a screening or transaction monitoring case. The subject is the proposed outcome, such as `not_a_match`. | `compliance.view` | `compliance_cases.change` |
| `account_code_map` | finance `code_maps.waiting`: a proposed account code map for an accounting system. The subject is the system, such as `xero`. | `settings.view` | `account_codes.change` |
| `investment_correction` | investments `list_corrections`, proposed. The subject is `unwind` or `refund`. | `investments.view` | `investment_corrections.change` |
| `investment_transfer` | investments `held_transfers`: a proposal to let through an investment sent to another client that was held because it is large. The subject is the transfer's ID. | `review_queue.view` | `investment_transfers.change` |
| `organisation` | organisations `waiting`: an application for a company or group account that one member of staff checked. The subject is the kind (`company` or `group`), never a name. See [organisations.md](organisations.md). | `review_queue.view` | `organisations.review` |
| `tax_remittance` | tax `service.waiting`: a payment to URA recorded, not yet approved. The subject is the month and currency, never the amount. | `finance.view` | `tax_remittances.change` |

Where a module has no separate deciding permission, the screen's own is used.

Only the sources the reader's permissions show are read, and all of them inside the reader's staff transaction, so row policies apply as they do on each module's own screen.

Not in the inbox, because they are not two-person decisions: identity checks (one reviewer, by the owner's decision), large payouts and withdrawal limits, refusing or removing a payout account (one person each, as neither lets money out), products and support.

## Where it lives

`app/modules/approvals` owns no tables. `domain/inbox.py` holds the pure rules (what each permission shows, `yours` and `can_decide`, which is never true for the person an item changes, the order), `service.py` reads the modules and `api.py` serves the route.
