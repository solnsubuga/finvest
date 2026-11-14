# apps/web-e2e

Browser tests (Playwright) for the client app at app.finvestug.com and the console at admin.finvestug.com, as their web builds. They run in Chromium at phone size (Pixel 7) and at a wide desktop size.

**Today both apps run against their in-app sample APIs**, because neither is wired to the backend yet: the client app is built with `EXPO_PUBLIC_DEMO_AUTH=1` and the console with `VITE_STUB_API=true`, exactly as a preview is. So these tests prove the screens, the navigation and the rules the screens enforce (a second step at sign-in, the client's own number for mobile money, a second person for a dealing order, a role that cannot open a screen), not the backend. The backend's own end-to-end journeys are in `backend/tests/journeys`. When an app talks to the real API, these tests should start the backend stack and sign in through it.

## Commands

Run `pnpm install` once at the repository root, then from this directory:

| Command | What it does |
| --- | --- |
| `pnpm build` | Builds both apps for the web into `.builds/` (ignored by git) |
| `pnpm test` | Serves both builds and runs every test, at phone and wide sizes |
| `pnpm typecheck` | `tsc --noEmit` |

Install a browser once with `pnpm exec playwright install chromium`, or point `PLAYWRIGHT_CHROMIUM_PATH` at a Chromium you already have. Retries are off: a test that passes only on a second try has failed.

## What is covered

| App | Flow |
| --- | --- |
| Client | The welcome screen says the sign-in is a demo; signing up with a texted code and a password; a wrong password; signing in with an authenticator code, a wrong one first; paying for an investment by mobile money from the account's own number, from the prompt to the receipt; adding a mobile money number to be paid to, a wrong texted code first; a new client handing in their ID, details and a selfie for the identity check; the app PIN after each log in (a simple PIN and a mismatch refused), locking in the background and opening with the PIN after a wrong one, five wrong PINs signing the phone out; sign up ending with the PIN and the passkey offer; a locked out client asking to be called back |
| Console | A screen asked for before signing in goes to sign-in; a password and a passkey; a password and an authenticator code, a wrong one first; staff with no second step are refused; a role that may not open the dealing desk sees "You cannot open this screen"; a dealing order its preparer cannot approve; opening a client who asked to be called back, then closing the request |

Accessibility: `accessibility.client.spec.ts` and `accessibility.console.spec.ts` run axe (`@axe-core/playwright`) against WCAG 2.1 A and AA on every main screen of both apps, at both sizes, and fail on any violation, naming the screen, the rule and the element. Add a new screen's path to their lists. axe checks contrast, names, roles and states, and keyboard reach of scrolling areas; it cannot judge whether a name makes sense, and it does not run on Android or iPhone (see [docs/gaps.md](../../docs/gaps.md#accessibility)).

The sample sessions keep nothing between page loads, so a test moves between screens inside the app rather than loading an address afresh.
