# apps/console

The Finvest back office console at admin.finvestug.com.

**Status:** the shell is built: staff sign-in, the menu that follows the person's role, and a placeholder for each screen. The screens themselves arrive phase by phase (see [docs/roadmap.md](../../docs/roadmap.md)).

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
| `src/pages/` | Sign-in, placeholder, no access and not found |

### Roles and permissions

The session carries a list of permissions, and each menu item names the one it needs. The console uses them only to decide what to show. The API must check every request itself.

The role to screen table in `src/auth/stub.ts` is a proposal, read from the role matrix on the canvas ("Team and permissions"). The backend's permissions module is the authority once it exists.

### Adding a screen

1. Create a folder in `src/screens/` named after the screen's path in `src/nav/nav.ts`, for example `src/screens/identity-checks/`.
2. Give it an `index.tsx` that default-exports the page. Keep the screen's other files in the same folder.
3. Add screenshots to the PR.

That replaces the placeholder. The route, the menu entry and the permission check are already there, and no shared file needs editing, so screens built at the same time do not conflict.

## Notes

- Every staff account must have a second step. Staff sign in with a password and then a passkey or an authenticator app code, whichever they have set up. An account with neither cannot sign in. There is no SMS option.
- Permissions are by role. Sensitive actions are maker and checker: one person proposes, another approves.
- Every staff action is written to the audit trail with a reason.
- The console is never linked from the public website and opens only for staff on allowed networks.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
