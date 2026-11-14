# apps/client

The Finvest client app. One codebase for Android, iPhone and the web at app.finvestug.com.

**Status:** app shell. Navigation, theme, the welcome, sign up and log in screens, Sign in and security (add a passkey, set up an authenticator app), investing (products, a calculator, review and confirm, the portfolio and each investment), paying for an investment by mobile money or bank transfer, and notifications, help chat and complaints. Sign-in and investing talk to the real API. The other areas still run against in-memory demos until they are connected one by one.

## Stack

React Native, Expo (SDK 58) with Expo Router, TypeScript in strict mode. Brand values come from [`packages/tokens`](../../packages/tokens/README.md). API types and validators come from [`packages/api-client`](../../packages/api-client/README.md), generated from the backend.

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
| `src/api/` | The typed API client: one call per route, answers checked, offline and timeout told apart, the phone's session token in the secure store |
| `src/auth/` | The sign-in contract, the API client for it, the demo stub, WebAuthn for passkeys, and the phone and password rules |
| `src/investing/` | The investing contract, the demo stub, amount and date formatting, and the product and investment cards |
| `src/payments/` | The payments contract, the demo stub, money and time formatting, and the status polling |
| `src/autoinvest/` | The automatic investing contract, the demo stub, and the schedule rules shown on screen |
| `src/insights/` | The insights contract, the API client for it, the demo stub, and the chart helpers |
| `src/support/` | The notifications, help chat and complaints contract, the demo stub, quick answers, and the chat polling |
| `docs/screenshots/` | Screenshots of every screen and state in this app |

## Navigation

One navigator with two faces. Below 900 points wide it shows bottom tabs: Home, Invest, Goals, Activity. At 900 and above it shows the black left sidebar with every destination. Both are drawn from `src/navigation/destinations.ts`, and a test checks that list against the route files. Screens without their own wide layout sit in a centred 440 point column.

## Sign-in

The screens talk to the `AuthClient` interface in `src/auth/types.ts`. It follows [docs/rules.md](../../docs/rules.md): the password never opens a session by itself, the second step is whichever of a passkey, an authenticator app or an SMS code the client has set up, with the strongest offered first and the others one tap away, and a session opened with an SMS code is marked restricted.

`src/auth/apiAuthClient.ts` answers it from the real API.

- **Where the API is.** `EXPO_PUBLIC_API_URL` at build time. Without it a development build talks to a local backend on `http://localhost:8000` (`make dev`), and only a production build talks to `https://api.finvestug.com`. The local API must list the app's origin in `FINVEST_CLIENT_ALLOWED_ORIGINS`; Compose already allows `http://localhost:8081`.
- **The session.** On the web it is the API's HttpOnly cookie, so the page never sees it. On a phone the API hands over a token, kept in the Keychain or Keystore with `expo-secure-store`, this device only. The app looks the session up when it starts, so a reload keeps you signed in, and goes back to log in when the API says the session has ended.
- **Errors.** Every refusal arrives as the API's error code and becomes a sentence from `describeAuthError`. "Offline" and "timeout" are told apart in `src/api/http.ts` for the wording, but both count as uncertain, because a connection can drop after the request arrived. That matters for anything that moves money.
- **Passkeys** use the browser's WebAuthn on the web. A phone build has no WebAuthn, so it hides the passkey option at log in and says so on the add screen. Passkeys on Android and iPhone need a native module and a development build, which is not done yet.

The in-memory demo in `src/auth/stubAuthClient.ts` answers in tests, and in a build only with `EXPO_PUBLIC_DEMO_AUTH=1`, and the app says so on screen while it is active. A normal production build leaves it out, and CI checks that.

- Any SMS or authenticator code is `123456`. Two accounts exist already, both with the password `correcthorse1`: `772 123 456` has a passkey, an authenticator app and SMS, and `701 000 200` has an authenticator app and SMS but no passkey. An account you create by signing up has SMS only. These are demo values, not credentials for any real system.
- Nothing is stored. Reloading the app signs you out.

`startSignup` answers the same way whether or not the number already has an account. Only `confirmSignupCode`, after the SMS code proves ownership, may say `phone_taken`. The backend must keep that property.

### Sign in and security

Profile opens `/security`, which lists the three second steps and which are on. Home points a client with SMS only to it.

- **Add a passkey** follows the canvas screen. `addPasskey` is where the real client will run WebAuthn on the device. If the client dismisses the device prompt it fails with `cancelled` and nothing is saved.
- **Set up an authenticator app** is not on the canvas, so it is drawn in the same style. Step 1 shows the key as a QR code (drawn on the device with `qrcode-generator`, never sent anywhere to be made into an image), as text to type, and on a phone as a link that opens the authenticator app. Step 2 asks for a code from the app. Only a right code switches it on.
- The key is held in the set-up screen's memory only. It is never put in a route, the session or storage. The demo key is random letters made on the spot, so no key sits in the source.
- Adding either one needs a session opened in the last 10 minutes, which is the sign-in API's rule. An older session gets `sign_in_again`, and the screen offers "Log in again" in place of the set-up.
- After logging in again the client is taken straight back to the set-up they were in. Only the two set-up routes can be a return address. It is dropped after five minutes. It survives the app going to the background, because reading an SMS or an authenticator code does that.
- If an authenticator set-up runs out of time before its code is entered, the screen fetches a new key and goes back to step 1. The old key is never shown again.
- When the real client lands: keep the key screen out of the app switcher and out of screen captures on Android and iPhone, and scan the QR code with a real authenticator app before release.
- A client who signed in with an SMS code can still add either one, and the screen says the 24 hour limit keeps running. See the open question in [docs/decisions.md](../../docs/decisions.md).

Not built: removing a passkey or an authenticator app, adding a second passkey from this screen, naming devices, and recovery codes. Offering the passkey during sign up, as on the canvas, waits for the app PIN step that comes before it.

**Forgot password** (`src/app/(auth)/reset/`) is not on the canvas, so it is drawn in the sign-up style. Step 1 takes the number and texts a code; the answer is the same whether or not the number has an account. Step 2 takes the code and the new password together, because the API checks the code only when the password is set, so a wrong code keeps the typed password and asks for the code again. The API then ends every session, the app forgets this device's token, and the client logs in with the new password. For 24 hours withdrawals go only to accounts used before (docs/rules.md), and both screens say so.

What the stub does not cover yet, each with a screen on the design canvas: the app PIN, the ID check, log in by email, and company or group accounts. Links to those lead to a short "not ready yet" screen.

Still outstanding on the sign up screen: "the terms" and "the privacy notice" are not links yet, because neither document exists. They must link to the real documents before launch.

## Goals

The goal screens live in `src/app/goals/` and talk to the `GoalsClient` interface in `src/goals/types.ts`. `src/goals/apiGoalsClient.ts` answers it from the API's `/client/goals` routes (docs/goals.md); the demo in `stubGoalsClient.ts` answers it in tests and with `EXPO_PUBLIC_DEMO_AUTH=1`.

- A goal holds no money. Each saving is an investment of its own, and the goal screen sends the client to the pay screens with its ID.
- Creating a goal and each saving carry an idempotency key made once per visit to the screen. When the answer to a saving is lost, being offline included, the amount is locked so a retry is the same saving under the same key.
- Saving needs an approved ID check, because it makes an investment. The API refuses it before that, and the screen says to finish the ID check.
- Every figure, the plan's rate included, arrives after tax from the API and is shown as sent.

## Investing

The investing screens talk to the `InvestingClient` interface in `src/investing/types.ts`. `src/investing/apiInvestingClient.ts` answers it from the client routes in `backend/app/modules/investments/api.py`, through `packages/api-client`. A client sees only the yearly rate after tax and net amounts, never a tax rate or a figure before tax, and every figure comes from the backend's quote or investment. The app adds nothing up.

- `/invest` lists the products. `/invest/[code]` shows one, with a calculator: the amount typed is quoted after a short pause, and Review opens once the quote is back.
- `/invest/review` saves the investment at today's rate under one idempotency key per request, so a double tap saves one investment, then replaces itself with the pay screen for that investment. After a try whose answer was lost, the choices are locked and the key is kept, so trying again is the same request. After a definite refusal nothing was saved, and the next try gets a new key. A fixed term asks what happens at the end: roll over (the default) or pay out.
- `/portfolio` lists the client's investments with what each is worth today. `/investment/[id]` shows one, with interest so far, what it pays at maturity, and a Pay button while it waits for payment.
- `/investment/withdraw/[id]` withdraws a running investment early. It shows exactly what comes back (the `value` the API sent: what was invested plus the interest after tax so far, no fee), lists the client's approved accounts with any still in their first day's safety wait shown but not choosable, and confirms under one idempotency key per request, locked after a lost answer as on the review screen. The investment then reads "Withdrawn early" in the portfolio and on its own screen, following `withdrawalRequestedAt` (the API keeps it `active` until the money has moved), says interest stopped and that we tell the client when it is sent, and drops everything about maturity. The Withdraw early button appears only when the API says `can_withdraw`, which stays false while `FINVEST_EARLY_WITHDRAWAL_OPEN` is off (backend/README.md). The demo lets every running investment be withdrawn. On the web, `?accounts=none` shows a client with no approved account, and `?withdraw=lost` loses the first withdrawal's answer to show the locked retry.
- The demo (`src/investing/stubInvestingClient.ts`) loads only in tests and in a build with `EXPO_PUBLIC_DEMO_AUTH=1`. It has two investments already running, works interest out as the backend does, and starts a term when the demo payment for it is confirmed (`src/demo/payables.ts` joins the two demos). Open `/?sample=empty` on the web to start as a client with no investments.

Not built yet: changing what happens at maturity from the investment screen (it needs the payout destinations screens) and statements.

## Paying

The pay screens live in `src/app/pay/` and talk to the `PaymentsClient` interface in `src/payments/types.ts`. A payment always pays for one thing, an investment or a goal, and its amount comes from that thing. There is no wallet and no amount field.

- `/pay/[payableId]` confirms the network. The number is the one on the client's own account and cannot be changed, because a payment from a number registered to someone else is refused. The screen makes one idempotency key per visit and sends it with every try. Once a try may have reached us (the answer was lost), the network is locked too, so every retry is the same request under the same key and asks for the money once.
- `/pay/deposit/[depositId]` is the one address for a payment while it is open. It reads the status every 3 seconds, every 10 after the first minute, not at all while the app is in the background, and shows the prompt, the check or the failure. It only reads. A payment with no clear answer is settled by the server asking the network, so that screen tells the client not to pay again and offers no way to.
- `/pay/receipt/[depositId]` shows a confirmed payment.
- `/pay/bank/[payableId]` shows where to send a bank transfer, each detail with a Copy button, the reference to use (the client's Finvest account number) and the rule that the money must come from an account in the client's own name. The details come from the server. The app holds none.
- `/pay/bank/receipt/[payableId]` takes the receipt (a file, or a photo on a phone; JPG, PNG or PDF up to 10 MB, checked in `src/payments/receipt.ts` and again by the server) and what the client says they sent: amount, day, bank and transaction number. The app reads nothing from the receipt. One idempotency key per visit, and after a lost try the receipt and details are locked so a retry is the same request.
- `/pay/bank/transfer/[transferId]` is the one address for a bank transfer: checking, confirmed, or not matched with the reason in plain words and a way to upload another receipt.

Amounts are integers in the smallest unit (`Money` in `types.ts`) and are formatted without passing through a fraction. Times are shown in Kampala time.

`src/payments/apiPaymentsClient.ts` answers the screens from the real API: `GET /client/investments/{id}` for what is owed, the mobile money and bank transfer routes under `/client/investments/{id}/payments/` with the screen's idempotency key, and `GET /client/payments/{id}` for the status. Mobile money sends only the network: the API takes the amount from the investment and the number from the session. The paying number comes back masked (`+256 77• ••• •56`) and is shown that way, in the demo too. A refusal because the investment already has a payment reads its live payment: one already paid cannot be paid again, and one under way is opened in place of the pay screen. Any other refused detail says the payment was not accepted, never that Finvest could not be reached, because nothing was saved. The bank screen reads where to send the money from `GET /client/investments/{id}/bank-transfer-details`. Where no account is set up for the investment's currency, the API answers 503 and the screen says the details are not in the app yet and points to mobile money or Help.

The in-memory stub in `src/payments/stubPaymentsClient.ts` loads only in tests and in a build with `EXPO_PUBLIC_DEMO_AUTH=1`, and CI checks it is out of production builds. In the demo, the Invest page has a "Demo: pay for an investment" button, and the last two digits of the account's number choose the outcome (sign up in the demo with a number that ends that way): `01` declined, `02` not enough money, `03` never approved, `04` unclear then confirmed, anything else approved. A demo bank transfer is matched after nine seconds, where staff take hours, and the last two digits of the transaction number choose: `01` the amount differs, `02` not on the statement, anything else matched.

Not built yet: reading the receipt for the client (the canvas shows fields filled in from it), "I will upload later" and "Share these details" on the bank screen, finding an open payment again after closing the app (it needs the Activity page), the fee row (fees are not decided), "send the prompt again" (the backend has no such command), the signed transaction note, sharing a receipt, and the notification when a payment is confirmed.

## Automatic investing

A plan collects the same amount every week or every month from the client's own mobile money number and invests it in one product. There is no pot: each payment becomes its own investment the same day, so each one must meet the product's minimum. The screens live in `src/app/autoinvest/` and talk to the `AutoinvestClient` interface in `src/autoinvest/types.ts`.

- `/autoinvest` lists the client's plans, each with its amount, schedule and next day. It is reached from Home and from Insights.
- `/autoinvest/new` starts a plan: product, amount, every week (a weekday) or every month (a day, where the 31st means the month's last day), what happens at maturity, and the network. The number is the one on the account. One idempotency key per visit. Nothing is collected the day a plan starts: the first prompt comes on the first due day after today.
- `/autoinvest/[planId]` shows the next prompt, the terms and past payments with the reason for any missed or failed one, and lets the client pause, resume or stop the plan. Stopping asks first.

The schedule rules in `src/autoinvest/schedule.ts` mirror the backend's (`docs/automatic-investing.md`) so the screen can say the first day before the plan is sent. The server is still the judge.

`src/autoinvest/apiAutoinvestClient.ts` answers the screens from `/client/autoinvest/plans` and its pause, resume and stop routes. A new plan is sent under the screen's key without a number: the API takes the account's own from the session. The products offered are the live shilling products with a fixed term from `GET /client/products`, because mobile money collects shillings only and the plan asks what happens at maturity. A refused detail says the plan was not accepted, never that Finvest could not be reached. The demo (`src/autoinvest/stubAutoinvestClient.ts`) loads only in tests and with `EXPO_PUBLIC_DEMO_AUTH=1`.

## Insights

`/insights` shows what the client's money earned each month for the last six months, what is invested now, what has been earned so far, this month's money in, earned and paid out, and the next maturities. Every figure is after tax, and no tax rate is shown. On a wide screen it has two columns.

Insights come from `GET /client/insights` through `src/insights/apiInsightsClient.ts`, one currency at a time. A client who holds a dollar investment (any not cancelled, from `GET /client/investments`) gets a UGX/USD switch; everyone else sees shillings only, as before. Each currency is fetched on its own and shown in its own unit, bars in thousands of shillings or in dollars and cents, and the two are never added together. An answer in a currency the screen did not ask for is the error state. The insights demo in `src/insights/stubInsightsClient.ts` loads only in tests and in a build with `EXPO_PUBLIC_DEMO_AUTH=1`. Automatic investing still runs on the in-memory stub in `src/autoinvest/stubAutoinvestClient.ts`, in development as well, until its product list can come from the API. CI checks that production builds leave both out. The two demo accounts have one monthly plan and six months of figures. A new demo account starts empty, and one whose number ends in `99` cannot reach Finvest, to show the error states.

Not built yet: sending an investment to another client, changing a plan's amount or day (the backend allows it, the screen does not yet), and the notifications before each prompt.

## Documents

`/documents` is where a client asks for a statement of any period up to a year, follows it while it is made, and downloads statements and receipts. On a phone it is not in the bottom bar: it is reached from Profile (the Home card "Your details, documents and help", then Documents). On a wide screen it is in the sidebar. The screen shows no amounts: every figure is in the PDF, after tax on statements, and with the tax withheld on the yearly tax certificate, which has its own section once the client has one.

`src/documents/apiDocumentsClient.ts` answers from the API routes in [docs/documents.md](../../docs/documents.md). The PDF is fetched with the session by `src/documents/savePdf.ts`: on the web as a browser download, and on a phone into the cache, then the share sheet, then the cached copy is removed. Tests and demo builds (`EXPO_PUBLIC_DEMO_AUTH=1`) use `src/documents/stubDocumentsClient.ts`, which CI checks is left out of production builds. A development build talks to the real API. In the demo a statement asked for from before 2020 fails, to show that state.

## Notes

- The web version ships at launch. About five screens have wide layouts (home, invest, portfolio, activity, documents). Everything else reuses the phone screen in a centred column.
- There is no wallet screen and no spendable balance.
- Clients never see tax rates, only net figures.
- The app PIN or fingerprint unlocks a phone that is already signed in and approves payments. It never signs in a new device.
- `app.json` cannot import the tokens, so its two `#0D0D0D` values (Android icon and splash background) are written by hand. Keep them equal to `colors.black`.
- `docs/screenshots/sheets/` holds contact sheets of the same screenshots, for PR descriptions.
- Screenshots in `docs/screenshots/` keep stable file names. Replace them in place when a screen changes.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.

## Notifications, help and complaints

The screens talk to the `SupportClient` interface in `src/support/types.ts`, whose shapes follow the backend routes in [docs/notifications.md](../../docs/notifications.md) and [docs/support.md](../../docs/support.md). `src/support/apiSupportClient.ts` answers it from the real API. The demo in `src/support/stubSupportClient.ts` answers only in tests and in a build with `EXPO_PUBLIC_DEMO_AUTH=1`, and CI checks that production builds leave it out. A lost connection shows the screen's "could not reach Finvest" state, and a chat message keeps its key, so it is never posted twice.

- **Notifications.** The bell on Home shows the unread count and opens `/notifications`, the canvas screen. Tapping one marks it read and opens the screen it names. A notification carries a kind and an ID, never an address, so `src/support/links.ts` decides where it goes and a kind without a screen yet opens nothing.
- **Notification settings.** `/notifications/settings` has push, SMS and email for each kind of alert, saved as each one is tapped. Security alerts keep SMS or email: the screen says so instead of sending a change the server would refuse.
- **Help.** `/help` is the canvas screen: quick answers above, one chat with the Finvest team below. The chat asks for new messages every 4 seconds while it is in front and not at all otherwise. A message shows as "Sending" at once and keeps its key, so trying again after a lost answer never posts it twice. "Call us" appears only when `EXPO_PUBLIC_SUPPORT_PHONE` is set at build time, because Finvest has not published a support line yet.
- **Quick answers** are in `src/support/answers.ts`. They say only what `docs/` establishes and show no tax rate. "Still stuck?" opens the chat filed under the answer's topic.
- **Complaints.** `/support/complaints` lists the client's complaints, `/support/complaints/new` makes one, and each has a page with its reference, status, the date Finvest will answer by (5 working days) and the outcome once closed. Staff notes are never shown. The form makes one key and sends it again on a retry after a lost answer, so a complaint is logged once. A definite refusal logged nothing, so the next try gets a new key. After a lost answer the form's words are locked, because the API answers the same key with the first complaint, and the screen says that trying again sends the same complaint.
- In the demo (tests, or `EXPO_PUBLIC_DEMO_AUTH=1`) each signed-in account has its own memory. The two ready-made accounts start with the canvas's notifications and one closed complaint. An account made by signing up starts empty. A chat message gets one canned reply a few seconds later, which says it is the demo.

Not built: push notifications on the device (they come with the backend's sending work), attachments in chat, and the screens behind payout, payout account and identity check notifications.
