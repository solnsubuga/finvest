# apps/console

The Finvest back office console at admin.finvestug.com.

**Status:** the shell is built: staff sign-in, the menu that follows the person's role, and a placeholder for each screen. The identity checks review screen is built on sample data (`src/screens/identity-checks`, behind the `KycApi` interface) until the backend publishes its routes. The migration screen (`src/screens/migration`, behind the `MigrationApi` interface) is built the same way: a trial import with its list of problems, the Finvest 1.0 totals that one person enters and another confirms, the comparison, sign-offs that one person proposes and another approves, and what still blocks the switch. It calls the real routes under `/staff/migration` (`src/screens/migration/http.ts`), naming staff through the shared directory; confirming totals and deciding a sign-off ask the person to confirm it is them. Under the stub, open `/migration?sample=empty` to see it before any trial has run, and `/migration?sample=unconfirmed` for totals waiting for a second person. The dealing desk is built the same way (`src/screens/dealing-desk`, behind the `DealingApi` interface, see [docs/dealing-desk.md](../../docs/dealing-desk.md)): the position, the liquidity buffer, and orders that one person prepares and another approves. Under it, the liquidity buffer section calls the real buffer routes (`src/screens/dealing-desk/Buffer.tsx`, behind `BufferApi`): Finvest's own money paid in or taken out, and changes to the buffer rules, each approved by a second person. Under that, the allocation report (`src/screens/dealing-desk/Allocations.tsx`, behind `AllocationsApi`, over `/staff/dealing/allocations`) shows for each product the bills held, the cost behind client investments and what is still waiting for a bill, and finds the bills behind one investment. It only reads. Under the stub, open `/dealing-desk?sample=empty` to see both before any money or bills are there, and `?sample=offline` as if Finvest could not be reached. The automatic investing screen is built the same way (`src/screens/automatic-investing`, behind the `AutoinvestApi` interface, see [docs/automatic-investing.md](../../docs/automatic-investing.md)): plan counts and every run of the last seven days with its outcome. It only reads. Open `/automatic-investing?sample=empty` or `?sample=error` to see those states. Support and messages (`src/screens/support`, behind `SupportApi`) and Complaints (`src/screens/complaints`, behind `ComplaintsApi`) are built the same way, to the routes in [docs/support.md](../../docs/support.md): the help chat inbox with saved replies, and the complaints register with its figures, history and outcome. Settings holds one section so far, client messages (`src/screens/settings`, behind `WordingsApi`, see [docs/notifications.md](../../docs/notifications.md)): the words of every notice, checked as they are typed, and changed only when a second person approves. Other settings join it as their own sections. The goals screen is built the same way (`src/screens/goals`, behind the `GoalsApi` interface, see [docs/goals.md](../../docs/goals.md)): every client goal, open or closed, and for a chosen goal its progress and the investments it is made of, rollovers included. It only reads, and calls `/staff/goals` and `/staff/goal-groups` (`src/screens/goals/http.ts`), naming clients through the shared directory. Under the stub, open `/goals?sample=empty` or `?sample=error` to see those states. The reconciliation screen starts with bank transfers to check (`src/screens/reconciliation`, behind the `BankTransfersApi` interface, see [docs/payments.md](../../docs/payments.md)): the receipt beside the client's details, the statement lines that could be the transfer, and a match that one person makes and a different person approves. Matching, refusing and approving need `bank_transfers.change`. It calls the real routes (`src/screens/reconciliation/http.ts`); until the list says which check each transfer waits for, the queue shows them all as waiting for a check. Under the stub, open `/reconciliation?sample=empty`, `?sample=error` or `?sample=unsorted` (the queue as the real list gives it today) to see those states. Further down the same screen, transfers between Finvest's own pots (`src/screens/reconciliation/transfers`, behind `PotTransfersApi`, over `/staff/reconciliation/pot-transfers`): one person records a transfer that was made, and a different person approves it, which posts it, or declines it. This needs `reconciliation.change`. Products, rates and investments are built the same way (`src/screens/products`, `rates` and `investments`, behind the `CatalogueApi` and `InvestmentsApi` interfaces shaped to the staff routes in `backend/app/modules/investments/api.py`): the catalogue with what clients see after tax, rate changes that one person proposes and another approves (which needs `rates.change`), and client investments with the daily interest run. They call those routes (`src/screens/products/http.ts` and `src/screens/investments/http.ts`); approving a rate change asks the person to confirm it is them. Under the stub, open `/rates?sample=empty` or `/investments?sample=empty` to see them with nothing in them. Under the investments, undoing an investment started by mistake and refunding money that never started one (`src/screens/investments/corrections`, behind `CorrectionsApi`, over `/staff/investment-corrections`): one person proposes and someone else approves, which needs `investment_corrections.change`. `?sample=offline` shows it as if Finvest could not be reached. Money movement (`src/screens/money-movement`, behind `PayoutsApi`, see [docs/payouts.md](../../docs/payouts.md)) calls the real payout routes: payouts held up at ioTec, and payouts ioTec never answered for, which one person proposes to settle from the ioTec statement and a different person approves. Settling needs `reconciliation.view`. Under the stub, open `/money-movement?sample=empty` or `?sample=error` to see those states. Below them, bank payouts (`src/screens/money-movement/bank`, behind `BankPayoutsApi`, over `/staff/payouts/bank`, `/staff/payout-bank-batches` and `/staff/payout-bank-settlements`): queued bank payouts that one person puts in a batch and a different person approves, the approved batch's transfer file for the bank's portal, and settling each sent payout from the trust statement, again by two people. Then payouts that came back (`src/screens/money-movement/returns`, behind `ReturnsApi`, over `/staff/payout-returns`): one person names the statement line that brought a paid payout back, and a different person approves. Every change there needs `bank_payouts.change`, and approving asks the person to confirm it is them. The same `?sample=` values show their empty and error states. The screens themselves arrive phase by phase (see [docs/roadmap.md](../../docs/roadmap.md)).

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

Without `VITE_API_URL`, `pnpm dev` uses sample staff from `src/auth/stub.ts`. Any password works. The passkey step is simulated, and any six digits except `000000` work as an authenticator code.

| Email | Role | Second step set up |
| --- | --- | --- |
| admin@finvest.test | Admin | Passkey and authenticator app |
| operations@finvest.test | Operations | Passkey and authenticator app |
| finance@finvest.test | Finance | Authenticator app only |
| compliance@finvest.test | Compliance | Passkey only |
| support@finvest.test | Support | Passkey and authenticator app |
| invited@finvest.test | Support | Neither, so sign-in is refused |

To try first-time set-up, open http://localhost:5174/enrol#sample-invite. It sets up `invited@finvest.test`. The link works once per browser tab session, as a real one works once. Any password of 10 characters or more works except `password1234`, which the stub treats as leaked so that error can be seen.

The stub is not security. It runs only under `pnpm dev`, or in a build made with `VITE_STUB_API=true`. A normal build signs in against the backend.

### Against the real backend

Set `VITE_API_URL` in `.env.local` (for example `http://localhost:8000` with `make dev` running) and leave `VITE_STUB_API` unset. The console then calls the staff sign-in routes with the session cookie. Deployed builds need no URL: `VITE_DEPLOY_ENV=production` uses https://api.finvestug.com and `staging` uses https://api-staging.finvestug.com. A build with neither says at sign-in that it is not connected, rather than guessing.

Whenever the stub is active, a banner reading "Sample sign-in. Not connected to Finvest." stays on screen. A build fails if `VITE_STUB_API=true` is combined with `VITE_DEPLOY_ENV=production`, so the deploy must set `VITE_DEPLOY_ENV` for the production console.

## Screenshots

`docs/screenshots/` holds the current screenshots of this app for PR descriptions. Replace files in place when a screen changes, delete ones that no longer apply, and keep them compressed. Do not add a new set per PR.

## How it fits together

| Path | Contents |
| --- | --- |
| `src/api/http.ts` | The HTTP client: base URL, cookies, idempotency keys and the backend's error envelope as `ApiError` |
| `src/api/webauthn.ts` | Turns the backend's passkey options into browser calls and back |
| `src/auth/api.ts` | `AuthApi`, the interface the screens use for sign-in |
| `src/auth/http.ts` | `AuthApi` against the backend's `/staff/login`, `/staff/session` and `/staff/enrolment` routes. `src/auth/index.ts` picks it, or the stub. |
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

Opening a screen needs its `.view` permission. Changing something on it needs its own action permission, with the backend's names: `support.reply`, `complaints.manage`, `complaints.close`, `wordings.change`, `announcements.send`, `bank_transfers.change` and `payout_accounts.change`. A screen hides what the person cannot do. The menu lists only the `.view` ones.

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
