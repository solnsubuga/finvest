# apps/client

The Finvest client app. One codebase for Android, iPhone and the web at app.finvestug.com.

**Status:** app shell. Navigation, theme, the welcome, sign up and log in screens, Sign in and security (add a passkey, set up an authenticator app), and paying for an investment by mobile money, all running against in-memory demos. No investing screens yet.

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
| `pnpm test` | Jest unit tests and the flow tests for sign-in and for Sign in and security |
| `pnpm build:web` | Exports the static web build to `dist/` |

Add Expo packages with `npx expo install <package>` so the version matches the SDK.

## Layout

| Path | Contents |
| --- | --- |
| `src/app/` | Routes only. `(auth)` holds the signed-out screens, `(app)` the signed-in shell, `security` the Sign in and security screens. |
| `src/navigation/` | The destination list, the phone tab bar and the wide sidebar |
| `src/components/` | Shared building blocks: text, buttons, fields, code input, cards |
| `src/theme/` | Re-exports `@finvest/tokens`, loads Plus Jakarta Sans, and holds the wide breakpoint |
| `src/auth/` | The sign-in contract, the demo stub, and the phone and password rules |
| `src/payments/` | The payments contract, the demo stub, money and time formatting, and the status polling |
| `docs/screenshots/` | Screenshots of every screen and state in this app |

## Navigation

One navigator with two faces. Below 900 points wide it shows bottom tabs: Home, Invest, Goals, Activity. At 900 and above it shows the black left sidebar with every destination. Both are drawn from `src/navigation/destinations.ts`, and a test checks that list against the route files. Screens without their own wide layout sit in a centred 440 point column.

## Sign-in

The screens talk to the `AuthClient` interface in `src/auth/types.ts`. It follows [docs/rules.md](../../docs/rules.md): the password never opens a session by itself, the second step is whichever of a passkey, an authenticator app or an SMS code the client has set up, with the strongest offered first and the others one tap away, and a session opened with an SMS code is marked restricted.

Until the backend sign-in API exists, the only implementation is the in-memory stub in `src/auth/stubAuthClient.ts`. It is a demo, and the app says so on screen while it is active.

- It runs in development, and in a production build only when `EXPO_PUBLIC_DEMO_AUTH=1` is set at build time. Without the flag a production build refuses every sign-in.
- Any SMS or authenticator code is `123456`. Two accounts exist already, both with the password `correcthorse1`: `772 123 456` has a passkey, an authenticator app and SMS, and `701 000 200` has an authenticator app and SMS but no passkey. An account you create by signing up has SMS only. These are demo values, not credentials for any real system.
- Nothing is stored. Reloading the app signs you out.

`startSignup` answers the same way whether or not the number already has an account. Only `confirmSignupCode`, after the SMS code proves ownership, may say `phone_taken`. The backend must keep that property.

### Sign in and security

Profile opens `/security`, which lists the three second steps and which are on. Home points a client with SMS only to it.

- **Add a passkey** follows the canvas screen. `addPasskey` is where the real client will run WebAuthn on the device. If the client dismisses the device prompt it fails with `cancelled` and nothing is saved.
- **Set up an authenticator app** is not on the canvas, so it is drawn in the same style. Step 1 shows the key as a QR code (drawn on the device with `qrcode-generator`, never sent anywhere to be made into an image), as text to type, and on a phone as a link that opens the authenticator app. Step 2 asks for a code from the app. Only a right code switches it on.
- The key is held in the set-up screen's memory only. It is never put in a route, the session or storage. The demo key is random letters made on the spot, so no key sits in the source.
- Adding either one needs a session opened in the last 10 minutes, which is the sign-in API's rule. An older session gets `sign_in_again`, and the screen offers "Log in again" in place of the set-up.
- After logging in again the client is taken straight back to the set-up they were in. Only the two set-up routes can be a return address. It is dropped after five minutes, and when the app goes to the background.
- If an authenticator set-up runs out of time before its code is entered, the screen fetches a new key and goes back to step 1. The old key is never shown again.
- When the real client lands: keep the key screen out of the app switcher and out of screen captures on Android and iPhone, and scan the QR code with a real authenticator app before release.
- A client who signed in with an SMS code can still add either one, and the screen says the 24 hour limit keeps running. See the open question in [docs/decisions.md](../../docs/decisions.md).

Not built: removing a passkey or an authenticator app, adding a second passkey from this screen, naming devices, and recovery codes. Offering the passkey during sign up, as on the canvas, waits for the app PIN step that comes before it.

What the stub does not cover yet, each with a screen on the design canvas: the app PIN, the ID check, password reset, log in by email, and company or group accounts. Links to those lead to a short "not ready yet" screen.

Still outstanding on the sign up screen: "the terms" and "the privacy notice" are not links yet, because neither document exists. They must link to the real documents before launch.

## Paying

The pay screens live in `src/app/pay/` and talk to the `PaymentsClient` interface in `src/payments/types.ts`. A payment always pays for one thing, an investment or a goal, and its amount comes from that thing. There is no wallet and no amount field.

- `/pay/[payableId]` confirms the network. The number is the one on the client's own account and cannot be changed, because a payment from a number registered to someone else is refused. The screen makes one idempotency key per visit and sends it with every try. Once a try may have reached us (the answer was lost), the network is locked too, so every retry is the same request under the same key and asks for the money once.
- `/pay/deposit/[depositId]` is the one address for a payment while it is open. It reads the status every 3 seconds, every 10 after the first minute, not at all while the app is in the background, and shows the prompt, the check or the failure. It only reads. A payment with no clear answer is settled by the server asking the network, so that screen tells the client not to pay again and offers no way to.
- `/pay/receipt/[depositId]` shows a confirmed payment.

Amounts are integers in the smallest unit (`Money` in `types.ts`) and are formatted without passing through a fraction. Times are shown in Kampala time.

Until the backend publishes its client routes, the only implementation is the in-memory stub in `src/payments/stubPaymentsClient.ts`, loaded under the same rule as the demo sign-in and checked out of production builds by CI. In the demo, the Invest page has a "Demo: pay for an investment" button, and the last two digits of the account's number choose the outcome (sign up in the demo with a number that ends that way): `01` declined, `02` not enough money, `03` never approved, `04` unclear then confirmed, anything else approved.

Not built yet: bank transfer and receipt upload, the fee row (fees are not decided), "send the prompt again" (the backend has no such command), the signed transaction note, sharing a receipt, and the notification when a payment is confirmed.

## Notes

- The web version ships at launch. About five screens have wide layouts (home, invest, portfolio, activity, documents). Everything else reuses the phone screen in a centred column.
- There is no wallet screen and no spendable balance.
- Clients never see tax rates, only net figures.
- The app PIN or fingerprint unlocks a phone that is already signed in and approves payments. It never signs in a new device.
- `app.json` cannot import the tokens, so its two `#0D0D0D` values (Android icon and splash background) are written by hand. Keep them equal to `colors.black`.
- `docs/screenshots/sheets/` holds contact sheets of the same screenshots, for PR descriptions.
- Screenshots in `docs/screenshots/` keep stable file names. Replace them in place when a screen changes.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
