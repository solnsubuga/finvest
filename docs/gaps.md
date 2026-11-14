# What is left before launch

A gap audit of `main` at `96e3ae0` (13 November 2026, 23:40 UTC) against the [roadmap](roadmap.md), the [launch plan](launch.md), the [decisions](decisions.md) and each area's "Not built yet" section. It lists what the parallel run and switch day still need, split into work engineering can do and things only the owner can provide. Work already owned by a running thread or an open pull request is named once, with its owner, and not counted as a gap.

How it was checked: every route in `packages/api-client/openapi.json` was compared with the routes the client app and the console call, every console screen in `apps/console/src/nav/nav.ts` was checked for a real API behind it, and the provider settings were read for real adapters.

This page is a snapshot. Strike a row with a link to the pull request that closes it, and delete the page once every row is closed.

## Launch blockers engineering can build

### Client app

The client app talks to the real API for sign-in, investing, paying, goals, automatic investing, insights, documents, notifications, help and complaints. These client routes exist in the API but no screen calls them.

| Gap | Why it blocks launch | Routes already built |
| --- | --- | --- |
| ~~**Identity check.** No screen lets a client hand in their details, ID document and selfie, or see the check's progress.~~ Closed by the client identity check screens PR (apps/client/src/app/id-check). | ~~A new client is never approved, so never gets an account number, so cannot pay by bank transfer or be paid out.~~ | `GET /client/identity-check`, `PUT .../details`, `PUT .../document`, `PUT .../photos/{kind}`, `POST .../submit` |
| ~~**Payout accounts and payouts.** No screen adds a mobile money number or bank account, enters its code, uploads proof, or lists payouts. The early withdrawal screen lists approved accounts, but a client has no way to make one.~~ Closed by the client payout screens PR (see [payouts.md](payouts.md), "Client screens"). | No client can ever be paid out, at maturity or early. | `/client/payout-destinations` (all six), `GET /client/payouts`, `GET /client/payouts/{id}`, `GET /client/withdrawal-limits` |
| ~~**Maturity choice after investing.** The choice is made at review, but cannot be changed afterwards, and paying out at maturity is never asked for.~~ Closed by the client payout screens PR. | The roadmap requires changing the choice before the maturity date. | `PUT /client/investments/{id}/at-maturity`, `POST /client/investments/{id}/pay-out` |
| **Claiming an account carried over from Finvest 1.0.** No screen opens the invitation link, sends the code, sets the password or reports a problem. | Switch day sends every Finvest 1.0 client a link ([launch.md](launch.md), T+85). Without the screen the link leads nowhere. | `GET /client/claim`, `POST /client/claim/start`, `.../complete`, `.../problem` |
| **Forgotten password.** "Forgot password?" leads to a "not ready yet" page. | A client who forgets their password is locked out for good. | `POST /client/password/reset/start`, `.../complete` |
| **Activity.** The Activity tab is a "coming later" page. | Clients cannot see their payments, interest and payouts in one list, which Finvest 1.0 users have today. | No single route. Built from investments, payments and payouts, or a new read route. |
| **Devices and sign-in methods.** No screen lists or signs out other sessions, lists or removes a passkey, or turns off an authenticator app. | A client with a stolen phone cannot sign it out. | `/client/sessions` (three), `GET /client/passkeys`, `DELETE /client/passkeys/{id}`, `GET`/`DELETE /client/authenticator` |
| **Email address proof.** No screen asks for or confirms the email link. | Statements are emailed only to a proved address ([decisions.md](decisions.md)), so no client gets one by email. | `POST /client/email/verification`, `.../confirm` |
| **Push registration.** The app never registers its push token. | Push alerts, once switched on, reach nobody. | `PUT /client/push-devices`, `POST /client/push-devices/remove` |

Also unused: `PUT /client/goals/{id}`, `PUT /client/goal-groups/{id}`, `PUT /client/goal-groups/{id}/me` and `PUT /client/autoinvest/plans/{id}` (editing). These are phase 4 and do not block launch.

### Console

Every console screen is built, but eight of them only work on the sample sign-in. In a real build they say they are not connected, because no HTTP client sits behind them.

| Screen | State on main | Staff routes waiting | Owner |
| --- | --- | --- | --- |
| Products | Sample data only | `/staff/products`, `/staff/products/{code}/rates`, `/staff/rate-proposals` (five) | None |
| Rates | Sample data only | Same as Products | None |
| Investments | Sample data only | `/staff/investments`, `/staff/investments/{id}`, `/staff/interest-runs` | None |
| Goals | Sample data only, goals and group goals | `/staff/goals`, `/staff/goals/{id}`, `/staff/goal-groups`, `/staff/goal-groups/{id}` | None |
| Reconciliation | Sample data only | `/staff/payments/bank-transfers` (four) | None |
| Migration | Sample data only | `/staff/migration` and its trials, control totals and sign-offs (seven) | Migration thread, to confirm |
| Dealing Desk | Sample data only; the buffer tab is being connected | `/staff/dealing/desk`, `/staff/dealing/orders` (seven) | Dealing desk thread (PRs #262, #265) |
| ~~Automatic Investing~~ | ~~Sample data only~~ Closed by the automatic investing staff routes PR. | `/staff/autoinvest/plans/counts`, `/staff/autoinvest/runs` | |

Four screens are still the placeholder page. The Review Queue is connected to `/staff/payout-destinations` and the held payout routes (`/staff/payouts/held`, `.../approve`, `.../decline`).

| Screen | What it needs | Routes |
| --- | --- | --- |
| Money Movement | Payouts that are held up or stranded, and deposits under way | `GET /staff/payouts/held-up`; stranded payouts in PR #263 (payouts thread) |
| Maturities | The daily maturity run and its exceptions | `GET /staff/interest-runs` covers interest only; a maturity run read route is needed |
| Finance | Day close, fee income, withholding tax owed and the accounting export | Reconciliation routes below; tax in the tax certificates thread |
| Compliance | Screening results and monitoring alerts | Nothing built (see below) |

Staff routes with no screen at all: `/staff/phone-changes` (three; the Clients screen does not show pending phone changes) and `/staff/sessions` (three).

### Backend

| Gap | Why it matters | Owner |
| --- | --- | --- |
| **Reconciliation has no HTTP routes.** `import_statement`, matching and the two-person day close exist as services only. | Finance cannot import a bank or provider statement or close a day, so no statement is ever final ([documents.md](documents.md)) and the daily reconciliation after the switch ([launch.md](launch.md) section 6) cannot run. | Payouts and reconciliation thread, to confirm |
| **Payouts held for review have no staff routes.** `held_for_review` and the approve and decline events exist in `payouts.py`, but no route lists or decides them. | A payout above the review amount, and every early withdrawal held for staff, can never be approved. | Payouts thread, to confirm |
| **Two-person settlement of a stranded payout.** | A payout the provider never answered for stays stuck. | Payouts thread, PR #263 |
| **Undoing a mistaken investment and refunding waiting money.** | | Investing thread, PR #260 |
| **Deposits credited to the wrong client.** | | Running thread "Fix deposits credited to the wrong client" |
| **Tax certificates and URA withholding reports.** | | Running thread "Build tax certificates and URA reports" |
| **Sending push, SMS and email.** | | Notifications thread |
| **Anti-money-laundering.** Nothing is built: no sanctions or politically exposed person screening, no transaction monitoring rules, no Financial Intelligence Authority report. | Listed on the roadmap's back office checklist and likely a licence condition. | None. Needs the owner's choice of list source first (below). |
| **Next of kin and the suitability questions.** Not built. | Listed on the roadmap and "usually asked for by the regulator". | None |
| **The real device checks** in [launch.md](launch.md) have not been run. | A launch condition. | Engineering, once Android and iPhone builds exist |
| **Web end-to-end tests** cover 7 client and 6 console journeys. The claim has none, because its screen does not exist yet. Payout accounts and the identity check have theirs. | Each new screen above should add its journey. | Each screen's thread |

### Follow-ups owed from reviews

The backend reviewer keeps a list of follow-ups asked for in reviews (team memory "finvest-review-followups"). The ones that touch money before launch: the investing sweep must not cancel while a deposit is open; stop the interest run after repeated failures and show it to staff; an idempotency key on `prepare_order` and a second person to settle a dealing order; the payouts closer must not be the importer, enforced in the database; the second signature for a day close enforced in the database; caching `/health/operations`.

## Built, but waiting on a provider

Each of these is blocked on something only the owner can get, and is listed again in the next section.

| Gap | State |
| --- | --- |
| **ioTec payments adapter.** `FINVEST_PAYMENTS_PROVIDER` accepts only `disabled` and `sandbox`. | No mobile money payment can be taken in production. |
| **ioTec payouts adapter.** Only the sandbox exists. | No payout can be sent. |
| **ioTec SMS adapter.** `FINVEST_SMS_BACKEND` accepts only `none` and `local-console`. | **No sign-up, SMS sign-in, payout account code or claim code can be delivered**, so nobody can sign up or claim an account. This is the most serious blocker. |
| **ioTec Verify: the name a network holds for a number.** | Mobile money payout accounts wait at `awaiting_proof` for good ([payouts.md](payouts.md)). |
| **Identity check provider.** Every client is reviewed by a person. | Works, but every sign-up needs staff time. |
| **Public site facts.** `registeredName`, `regulator` and `licenceNumber` in `apps/site/src/data/company.ts` are empty, and `pnpm placeholders --strict` fails until they are set. The legal pages are headings only. | The site cannot be opened to search engines. |

## What only the owner can provide

Credentials and accounts:

- ioTec API access: the API details, the callback signature scheme, sandbox access and production credentials, for payments, payouts, SMS and Verify. Also the ioTec questions in [decisions.md](decisions.md) (fees, settlement speed, payout limits, which account a bank payout leaves).
- The client trust account's details for `FINVEST_BANK_TRANSFER_ACCOUNTS`, and finance's confirmation that it is the account reconciliation imports.
- The GitHub secrets and variables still missing for provisioning and deploys (listed in [infra/README.md](../infra/README.md)), including `FINVEST_EMAIL_HASH_KEY` before the next release, `FINVEST_IDENTITY_DATA_KEY`, `FINVEST_AUTHENTICATOR_KEY`, `RESEND_HOOK_SECRET` and at least two `ALERT_EMAILS`.
- The Cloudflare token's Zone WAF Edit permission, so the rate limit can be applied.
- Repository settings: secret scanning, push protection and Dependabot ([security.md](security.md) finding 8), and "Keep my email addresses private" on GitHub.
- The go-ahead for the first Provision and the first production deploy.
- An identity check provider (Smile ID or ioTec Verify), if people are not to review every client.
- A sanctions and politically exposed persons list source, before screening can be built.

Legal and regulatory:

- The company's registered name, regulator and licence number for the website, and the wording of the terms, privacy notice and other legal pages from Finvest's lawyers.
- Whether the regulator must be told of the change of system, and how.
- Counsel's answers in [decisions.md](decisions.md): money in transit through the trust account, data hosted in London, group goals as a collective scheme, the tax on interest and its rounding, and the old system's higher interest.
- How long records must be kept, which decides the retention jobs for the ledger, the audit trail and email events.

Launch:

- The penetration testing firm, the test dates and the signed authorisation letter.
- A sample Finvest 1.0 export, and then a real one for the trial import.
- At least three admins with a passkey or authenticator app.
- The switch date, the go or no go, approval of the invitation texts and of the recovery targets.
- The open owner questions in [decisions.md](decisions.md). Each is built with a working default, so none blocks engineering, but each should be confirmed before launch.
