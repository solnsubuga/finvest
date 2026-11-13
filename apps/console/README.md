# apps/console

The Finvest back office console at admin.finvestug.com.

**Status:** not scaffolded yet.

## Stack

React, Vite, TypeScript, Tailwind, shadcn. API types come from [`packages/api-client`](../../packages/api-client/README.md) and brand values from [`packages/tokens`](../../packages/tokens/README.md).

## Notes

- Staff sign in with a password and a passkey. There is no SMS option.
- Permissions are by role. Sensitive actions are maker and checker: one person proposes, another approves.
- Every staff action is written to the audit trail with a reason.
- The console is never linked from the public website and opens only for staff on allowed networks.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
