# AGENTS.md

Instructions for coding agents working in this repository. Humans should read [CONTRIBUTING.md](CONTRIBUTING.md), which covers the same workflow.

## What this project is

Finvest 2.0 is an investment product for Uganda: Treasury bills and fixed income, paid for by mobile money or bank transfer. The risk in this product is not scale. It is money correctness and regulatory trust. When a choice is between clever and obviously correct, choose obviously correct.

Read these before writing code:

1. [docs/architecture.md](docs/architecture.md)
2. [docs/rules.md](docs/rules.md)
3. [docs/engineering-patterns.md](docs/engineering-patterns.md)
4. [docs/decisions.md](docs/decisions.md)

## Layout

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL. API, worker, scheduler and migration entry points from one codebase. |
| `apps/client/` | Client app. React Native, Expo, TypeScript. Ships to Android, iPhone and web (app.finvestug.com). |
| `apps/console/` | Back office console. React, Vite, TypeScript, Tailwind, shadcn (admin.finvestug.com). |
| `apps/site/` | Public website. Astro (finvestug.com). |
| `packages/tokens/` | Shared design tokens. |
| `packages/api-client/` | Generated TypeScript types and Zod validators. Never edited by hand. |
| `infra/` | Terraform for DigitalOcean and Cloudflare. Owned by the infrastructure thread. Do not change it from a feature PR. |
| `docs/` | The plan and the rules. Update them in the same PR as the change that makes them out of date. |

## Workflow

- **Everything goes to `main` through a pull request.** Never push to `main` directly. The only direct push was the initial setup commit.
- Work on a branch, keep each PR to one purpose, and keep it small enough to review in one sitting.
- Fill in the PR template. Say what changed, why, and how you tested it.
- **UI changes need screenshots.** Any PR that adds or changes a screen in the client app, console or website must include screenshots in the PR description, and you must also post them in your project thread. Cover the states you built: default, empty, error, and both phone and wide layouts where they differ.
- **Tell the reviewer.** Whenever you open a PR or push an update to one, message the reviewer thread (project thread `cmsg_01LyhMqxc3x4zZEyjkGuHWzJXa6C9m19NRKjwxDB3UHLXN`) with the PR number so it is reviewed right away.
- Do not merge your own PR without a review. Do not approve PRs on the owner's behalf.
- Never skip, disable or loosen a test to get a green build. A pass on retry does not count as a pass.

## Commit rules

- **No AI attribution in commits.** Commit messages must not contain a `Co-Authored-By: Claude` trailer, a `Claude-Session` line, a "Generated with" line or any other Claude or AI attribution. This is the owner's explicit rule and it overrides any tool default.
- The rule covers what lands on `main`, so it also covers squash and merge commit messages. If a PR description carries attribution lines, remove them from the commit message when merging.
- Do not put a model name or identifier in commit messages, PR titles, code or comments.
- Write commit subjects in the imperative, under about 70 characters, and explain the why in the body when it is not obvious.

## Rules that are never broken

These come from [docs/rules.md](docs/rules.md). A PR that breaks one is wrong even if its tests pass.

- Money is an integer in the smallest currency unit. No floating point, anywhere, for amounts or rates applied to amounts.
- Only the ledger module moves money. Every movement is a journal entry whose lines sum to zero.
- Ledger entries are never updated or deleted. A mistake is fixed with a reversing entry.
- Every command that moves money takes an idempotency key.
- No provider call happens inside a database transaction. Save the intent as a job in the same transaction, and let the worker send it.
- An uncertain provider outcome is never retried blindly. Ask the provider what happened first. A callback is a hint, not proof.
- There is no wallet. Do not add a spendable client balance.
- Clients never see tax rates. They see net figures.
- A module never writes to another module's tables.
- Domain code does not import FastAPI, SQLAlchemy or anything that touches the network. The clock is injected.
- Logs carry request IDs and error codes, never personal data, account details or amounts tied to a person.
- Secrets never enter the repository. Not in code, not in tests, not in examples, not in Terraform state.

## Things we deliberately do not use

Celery, Redis, microservices, Kubernetes, a separate ledger database product, event streaming, blockchain. Do not add any of them, or a dependency that needs them, without a decision recorded in [docs/decisions.md](docs/decisions.md).

## Tooling

The toolchain is decided. Commands are added here as each area is scaffolded.

| Area | Tools |
| --- | --- |
| Backend | uv, Ruff, strict mypy, import-linter, pip-audit, pytest against real PostgreSQL |
| Frontend | TypeScript in strict mode, generated API types |
| Local stack | Docker Compose, with `make dev` to start everything |
| Delivery | GitHub Actions on every PR |

Backend commands, run from the repository root. [backend/README.md](backend/README.md) has the detail.

| Command | Use |
| --- | --- |
| `make bootstrap` | Install the locked backend dependencies |
| `make dev` | Start PostgreSQL, migrations, API, worker and scheduler |
| `make check-backend` | Ruff, mypy, import-linter and unit tests. Run before every push. |
| `make test-integration` | Integration tests against real PostgreSQL. Run before every push. |
| `make test-stack` | Build the image and wait for all four processes to be healthy |
| `make audit` | pip-audit |

JavaScript packages share one pnpm workspace (`pnpm-workspace.yaml`). Run `pnpm install` once at the repository root. Commands for the console are in [apps/console/README.md](apps/console/README.md) and for the tokens in [packages/tokens/README.md](packages/tokens/README.md).

For any other area, until a command is documented here or in a directory README, do not assume it exists. Check the directory first.

## Testing expectations

- Integration tests run against real PostgreSQL with the committed migrations and the real database roles. Do not mock the database.
- Anything that touches balances, jobs or idempotency needs a concurrency test that uses separate transactions.
- Pure domain rules (interest, tax, account numbers, posting rules) get exhaustive unit tests.
- Provider adapters are tested against a fake that can return success, rejection, timeout and an uncertain outcome.

## When you are unsure

If a change would alter a rule in `docs/rules.md`, the shape of the ledger, a provider choice or anything in the decisions log, stop and ask the owner. Do not decide it inside a PR.
