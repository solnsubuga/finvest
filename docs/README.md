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
| [reconciliation.md](reconciliation.md) | Statements, matching against the ledger, exceptions and the two-person day close |
| [identity-checks.md](identity-checks.md) | What a client hands in, the lifecycle of a check, automatic checks, and what the database enforces |
| [account-numbers.md](account-numbers.md) | Format, generation and lifetime of client account numbers |
| [roadmap.md](roadmap.md) | Build phases and the feature checklist for the client app and back office |
| [migration.md](migration.md) | Moving Finvest 1.0 clients onto the new system |
| [decisions.md](decisions.md) | Decisions made so far and questions still open |
| [design.md](design.md) | The design canvas, brand and tokens |

These documents describe the plan as of 13 November 2026. They are kept current: a pull request that makes one of them wrong also corrects it.

Items marked "confirm" have not been verified and must be checked before anyone relies on them.
