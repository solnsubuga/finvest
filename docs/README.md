# Finvest documentation

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | The shape of the system, the modules, how money moves, and how the product works without a wallet |
| [technology.md](technology.md) | Every technology choice with its reason, and what we leave out |
| [engineering-patterns.md](engineering-patterns.md) | The backend patterns carried over from Fidiba: jobs in PostgreSQL, the outbox, idempotency, database roles |
| [rules.md](rules.md) | Rules for money, security and compliance |
| [ledger.md](ledger.md) | How the ledger, integer money, account number issuing and the audit trail are built, and what the database enforces |
| [payouts.md](payouts.md) | Payout destinations, the name lock, the 24 hour wait, withdrawal limits and the life of a payout |
| [investing.md](investing.md) | Products, rates and their approval, the interest formula, tax, rounding and what clients see |
| [dealing-desk.md](dealing-desk.md) | Placing client money into real bills, orders and who approves them, allocation, bills held and the liquidity buffer |
| [reconciliation.md](reconciliation.md) | Statements, matching against the ledger, exceptions and the two-person day close |
| [identity-checks.md](identity-checks.md) | What a client hands in, the lifecycle of a check, automatic checks, and what the database enforces |
| [payments.md](payments.md) | How a deposit by mobile money is sent, confirmed and posted, what happens when the provider's answer is unclear, and how a mistaken credit is corrected |
| [goals.md](goals.md) | Goals as targets attached to investments, the monthly plan and how a goal's progress is labelled |
| [notifications.md](notifications.md) | How a module tells a client something, the event catalogue, channels, and how staff change the words |
| [tax.md](tax.md) | Withholding tax on interest: the monthly return for URA and its file, paying URA by two people, and the figures on a client's yearly certificate |
| [documents.md](documents.md) | Statements and receipts: what they show, how the figures are checked, and the emails that carry them |
| [support.md](support.md) | Help chat between clients and staff, and the complaints register: routes, rules and who sees what |
| [automatic-investing.md](automatic-investing.md) | Plans that invest the same amount on a schedule, the daily run that asks for each payment, and what the database enforces |
| [insights.md](insights.md) | What a client invested, earned and was paid each month, what comes next, and where each figure is read from in the ledger |
| [client-profile.md](client-profile.md) | Next of kin and their shares, the investing questions and the profile they give |
| [account-closure.md](account-closure.md) | Closing an account at the client's request: what must be clear first, the two staff steps and what the database enforces |
| [client-accounts.md](client-accounts.md) | Finding a client in the console, the client page, and locking or unlocking an account with two people |
| [console-admin.md](console-admin.md) | The team and its two-admin changes, the approvals inbox, the audit trail viewer, email suppressions and client account support |
| [account-numbers.md](account-numbers.md) | Format, generation and lifetime of client account numbers |
| [approvals.md](approvals.md) | The approvals inbox: what waits for a second member of staff, who sees it and who may decide |
| [roadmap.md](roadmap.md) | Build phases and the feature checklist for the client app and back office |
| [migration.md](migration.md) | Moving Finvest 1.0 clients onto the new system |
| [security.md](security.md) | The security review before launch: findings, the ASVS checklist and the penetration test scope |
| [observability.md](observability.md) | Logs, health checks, alerts and the first steps when an alert fires |
| [backup-and-restore.md](backup-and-restore.md) | What is backed up, the restore drill and its record, and a real restore |
| [gaps.md](gaps.md) | What is left before launch: missing screens and routes, provider blockers and what only the owner can provide |
| [launch.md](launch.md) | The parallel run against Finvest 1.0, go or no go, switch day, rollback and what only the owner can provide |
| [decisions.md](decisions.md) | Decisions made so far and questions still open |
| [design.md](design.md) | The design canvas, brand and tokens |

These documents describe the plan as of 13 November 2026. They are kept current: a pull request that makes one of them wrong also corrects it.

Items marked "confirm" have not been verified and must be checked before anyone relies on them.
