# apps/client

The Finvest client app. One codebase for Android, iPhone and the web at app.finvestug.com.

**Status:** app shell. Navigation, theme, and the welcome, sign up and log in screens, running against an in-memory demo sign-in. No investing or payment features yet.

## Stack

React Native, Expo (SDK 58) with Expo Router, TypeScript in strict mode. Brand values come from [`packages/tokens`](../../packages/tokens/README.md). API types will come from [`packages/api-client`](../../packages/api-client/README.md) once the backend publishes them.

## Commands

Run `pnpm install` once at the repository root, then from `apps/client`:

| Command | What it does |
| --- | --- |
| `pnpm start` | Starts the Expo dev server for Android, iPhone and web |
| `pnpm web` | Starts the dev server and opens the web version |
| `pnpm lint` | ESLint with the Expo rules |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm test` | Jest unit tests and the sign-in flow test |
| `pnpm build:web` | Exports the static web build to `dist/` |

Add Expo packages with `npx expo install <package>` so the version matches the SDK.

## Layout

| Path | Contents |
| --- | --- |
| `src/app/` | Routes only. `(auth)` holds the signed-out screens, `(app)` the signed-in shell. |
| `src/navigation/` | The destination list, the phone tab bar and the wide sidebar |
| `src/components/` | Shared building blocks: text, buttons, fields, code input, cards |
| `src/theme/` | Re-exports `@finvest/tokens`, loads Plus Jakarta Sans, and holds the wide breakpoint |
| `src/auth/` | The sign-in contract, the demo stub, and the phone and password rules |
| `docs/screenshots/` | Screenshots of every screen and state in this app |

## Navigation

One navigator with two faces. Below 900 points wide it shows bottom tabs: Home, Invest, Goals, Activity. At 900 and above it shows the black left sidebar with every destination. Both are drawn from `src/navigation/destinations.ts`, and a test checks that list against the route files. Screens without their own wide layout sit in a centred 440 point column.

## Sign-in

The screens talk to the `AuthClient` interface in `src/auth/types.ts`. It follows [docs/rules.md](../../docs/rules.md): the password never opens a session by itself, the second step is a passkey first and a code as backup, and a session opened with a code is marked restricted.

Until the backend sign-in API exists, the only implementation is the in-memory stub in `src/auth/stubAuthClient.ts`. It is a demo, and the app says so on screen while it is active.

- It runs in development, and in a production build only when `EXPO_PUBLIC_DEMO_AUTH=1` is set at build time. Without the flag a production build refuses every sign-in.
- Any SMS code is `123456`. One account exists already: `772 123 456` with the password `correcthorse1`. These are demo values, not credentials for any real system.
- Nothing is stored. Reloading the app signs you out.

`startSignup` answers the same way whether or not the number already has an account. Only `confirmSignupCode`, after the SMS code proves ownership, may say `phone_taken`. The backend must keep that property.

What the stub does not cover yet, each with a screen on the design canvas: the app PIN, passkey setup, the ID check, password reset, log in by email, and company or group accounts. Links to those lead to a short "not ready yet" screen.

Still outstanding on the sign up screen: "the terms" and "the privacy notice" are not links yet, because neither document exists. They must link to the real documents before launch.

## Notes

- The web version ships at launch. About five screens have wide layouts (home, invest, portfolio, activity, documents). Everything else reuses the phone screen in a centred column.
- There is no wallet screen and no spendable balance.
- Clients never see tax rates, only net figures.
- The app PIN or fingerprint unlocks a phone that is already signed in and approves payments. It never signs in a new device.
- `app.json` cannot import the tokens, so its two `#0D0D0D` values (Android icon and splash background) are written by hand. Keep them equal to `colors.black`.
- Screenshots in `docs/screenshots/` keep stable file names. Replace them in place when a screen changes.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
