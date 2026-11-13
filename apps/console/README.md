# apps/console

The Finvest back office console at admin.finvestug.com.

**Status:** the shell is built: staff sign-in, the menu that follows the person's role, and a placeholder for each screen. The identity checks review screen is built on sample data (`src/screens/identity-checks`, behind the `KycApi` interface) until the backend publishes its routes. The migration screen (`src/screens/migration`, behind the `MigrationApi` interface) is built the same way: a trial import with its list of problems, the Finvest 1.0 totals that one person enters and another confirms, the comparison, sign-offs that one person proposes and another approves, and what still blocks the switch. Open `/migration?sample=empty` to see it before any trial has run, and `/migration?sample=unconfirmed` for totals waiting for a second person. The dealing desk is built the same way (`src/screens/dealing-desk`, behind the `DealingApi` interface, see [docs/dealing-desk.md](../../docs/dealing-desk.md)): the position, the liquidity buffer, and orders that one person prepares and another approves. The automatic investing screen is built the same way (`src/screens/automatic-investing`, behind the `AutoinvestApi` interface, see [docs/automatic-investing.md](../../docs/automatic-investing.md)): plan counts and every run of the last seven days with its outcome. It only reads. Open `/automatic-investing?sample=empty` or `?sample=error` to see those states. Support and messages (`src/screens/support`, behind `SupportApi`) and Complaints (`src/screens/complaints`, behind `ComplaintsApi`) are built the same way, to the routes in [docs/support.md](../../docs/support.md): the help chat inbox with saved replies, and the complaints register with its figures, history and outcome. Settings holds one section so far, client messages (`src/screens/settings`, behind `WordingsApi`, see [docs/notifications.md](../../docs/notifications.md)): the words of every notice, checked as they are typed, and changed only when a second person approves. Other settings join it as their own sections. The goals screen is built the same way (`src/screens/goals`, behind the `GoalsApi` interface, see [docs/goals.md](../../docs/goals.md)): every client goal, open or closed, and for a chosen goal its progress and the investments it is made of, rollovers included. It only reads. Open `/goals?sample=empty` or `?sample=error` to see those states. The reconciliation screen starts with bank transfers to check (`src/screens/reconciliation`, behind the `BankTransfersApi` interface, see [docs/payments.md](../../docs/payments.md)): the receipt beside the client's details, the statement lines that could be the transfer, and a match that one person makes and a different person approves. Matching, refusing and approving need `bank_transfers.change`. Open `/reconciliation?sample=empty` or `?sample=error` to see those states. The screens themselves arrive phase by phase (see [docs/roadmap.md](../../docs/roadmap.md)).

## Stack

React, Vite, TypeScript in strict mode, Tailwind, and shadcn-style components in `src/components/ui`. Brand values come from [`packages/tokens`](../../packages/tokens/README.md) through `src/index.css`. Nothing in this app hard-codes a brand colour.

## Commands

Install once from the repository root with `pnpm install`. Then, from this directory:

| Command | What it does |
| --- | --- |
| `pnpm dev` | Starts the console at http://localhost:5174 |
| `pnpm test` | Runs the tests |
| `pnpm lint` | Runs ESLint |
| `pnpm typecheck` | Type checks the app |
| `pnpm build` | Type checks and builds to `dist/` |

## Signing in locally

The backend's sign-in is not built yet, so `pnpm dev` uses sample staff from `src/auth/stub.ts`. Any password works. The passkey step is simulated, and any six digits except `000000` work as an authenticator code.

| Email | Role | Second step set up |
| --- | --- | --- |
| admin@finvest.test | Admin | Passkey and authenticator app |
| operations@finvest.test | Operations | Passkey and authenticator app |
| finance@finvest.test | Finance | Authenticator app only |
| compliance@finvest.test | Compliance | Passkey only |
| support@finvest.test | Support | Passkey and authenticator app |
| invited@finvest.test | Support | Neither, so sign-in is refused |

To try first-time set-up, open http://localhost:5174/enrol#sample-invite. It sets up `invited@finvest.test`. The link works once per browser tab session, as a real one works once. Any password of 10 characters or more works except `password1234`, which the stub treats as leaked so that error can be seen.

The stub is not security. It runs only under `pnpm dev`, or in a build made with `VITE_STUB_API=true`. A normal production build has no working sign-in until the real API client is wired in.

Whenever the stub is active, a banner reading "Sample sign-in. Not connected to Finvest." stays on screen. A build fails if `VITE_STUB_API=true` is combined with `VITE_DEPLOY_ENV=production`, so the deploy must set `VITE_DEPLOY_ENV` for the production console.

## Screenshots

`docs/screenshots/` holds the current screenshots of this app for PR descriptions. Replace files in place when a screen changes, delete ones that no longer apply, and keep them compressed. Do not add a new set per PR.

## How it fits together

| Path | Contents |
| --- | --- |
| `src/auth/api.ts` | `AuthApi`, the interface the screens use for sign-in. It stands in for the generated client in [`packages/api-client`](../../packages/api-client/README.md). When the backend publishes its OpenAPI description, a real implementation replaces the stub in `src/auth/index.ts` and the screens stay as they are. |
| `src/auth/types.ts` | Roles, permissions and the session |
| `src/auth/stub.ts` | Sample staff, and which screens each role opens |
| `src/nav/nav.ts` | The menu: groups, screens, and the permission each one needs |
| `src/routes.tsx` | Routes. Each screen is guarded by its permission, so typing an address does not get around the menu. |
| `src/components/` | The shell, sidebar and logo |
| `src/screens/` | One folder per built screen, picked up automatically |
| `src/directory/` | Names for the IDs that support and complaints return. Those modules never read identity tables, so the console looks a client or a member of staff up here. Sample data only until the clients and team routes exist. |
| `src/pages/` | Sign-in, first-time set-up, placeholder, no access and not found |

### Roles and permissions

The session carries a list of permissions, and each menu item names the one it needs. The console uses them only to decide what to show. The API must check every request itself.

Opening a screen needs its `.view` permission. Changing something on it needs its own action permission, with the backend's names: `support.reply`, `complaints.manage`, `complaints.close`, `wordings.change` and `bank_transfers.change`. A screen hides what the person cannot do. The menu lists only the `.view` ones.

The role to screen table in `src/auth/stub.ts` is a proposal, read from the role matrix on the canvas ("Team and permissions"). The backend's permissions module is the authority once it exists.

### Adding a screen

1. Create a folder in `src/screens/` named after the screen's path in `src/nav/nav.ts`, for example `src/screens/identity-checks/`.
2. Give it an `index.tsx` that default-exports the page. Keep the screen's other files in the same folder.
3. Add screenshots to the PR.

That replaces the placeholder. The route, the menu entry and the permission check are already there, and no shared file needs editing, so screens built at the same time do not conflict.

## Notes

- New staff set themselves up from a one-time link, `/enrol#<token>`: a password, then a passkey or an authenticator app. The token is after the `#` so it is never sent in an address, and the page removes it from the address bar once read.
- Every staff account must have a second step. Staff sign in with a password and then a passkey or an authenticator app code, whichever they have set up. An account with neither cannot sign in. There is no SMS option.
- Permissions are by role. Sensitive actions are maker and checker: one person proposes, another approves.
- Every staff action is written to the audit trail with a reason.
- The console is never linked from the public website and opens only for staff on allowed networks.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
