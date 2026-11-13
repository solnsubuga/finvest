# Contributing

## Branches and pull requests

- `main` is always in a working state. Nothing is pushed to it directly.
- Create a branch for each piece of work and open a pull request to `main`.
- One purpose per PR. A refactor and a feature are two PRs.
- Fill in the PR template: what changed, why, how it was tested.
- Every PR is reviewed before it merges.

## Screenshots for UI work

A PR that adds or changes a screen in the client app, the console or the website includes screenshots in its description. Show the states that were built (default, empty, error) and both the phone and wide layouts where they differ.

## Commits

- Imperative subject line, under about 70 characters. Use the body to explain why.
- No `Co-Authored-By: Claude` trailers and no other AI attribution lines in any commit that lands on `main`. This includes squash and merge commit messages.
- Never commit secrets, real client data or provider credentials. Use `.env` files locally. They are ignored by git, and each directory keeps a `.env.example` with placeholder values.

## Before asking for review

- Tests pass locally, without retries.
- Linters and type checks pass.
- Documentation in `docs/` still matches the code. If your change makes a document wrong, fix the document in the same PR.
- Database changes come with an Alembic migration, and the migration has been run forwards on a fresh database.
- Generated files (`packages/api-client/`) are regenerated, not edited.

## Rules for money code

Read [docs/rules.md](docs/rules.md). A change that touches the ledger, payments, interest or tax needs tests for the concurrent and repeated-request cases, not only the happy path.

## Changing a decision

Technology and product decisions are recorded in [docs/decisions.md](docs/decisions.md). To change one, raise it with the owner first, then update the log in the PR that carries out the change.
