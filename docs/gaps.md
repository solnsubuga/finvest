# What is left before launch

A gap audit of `main` at `96e3ae0` (13 November 2026, 23:40 UTC) against the [roadmap](roadmap.md), the [launch plan](launch.md), the [decisions](decisions.md) and each area's "Not built yet" section. It lists what the parallel run and switch day still need, split into work engineering can do and things only the owner can provide. Work already owned by a running thread or an open pull request is named once, with its owner, and not counted as a gap.

How it was checked: every route in `packages/api-client/openapi.json` was compared with the routes the client app and the console call, every console screen in `apps/console/src/nav/nav.ts` was checked for a real API behind it, and the provider settings were read for real adapters.

The launch blocker rows were checked again on 14 November 2026 and the closed ones struck. This page is a snapshot. Strike a row with a link to the pull request that closes it, and delete the page once every row is closed.

## Launch blockers engineering can build

### Client app

The client app talks to the real API for sign-in, investing, paying, goals, automatic investing, insights, documents, notifications, help and complaints. These client routes exist in the API but no screen calls them.

| Gap | Why it blocks launch | Routes already built |
| --- | --- | --- |
| ~~**Identity check.** No screen lets a client hand in their details, ID document and selfie, or see the check's progress.~~ Closed by the client identity check screens PR (apps/client/src/app/id-check). | ~~A new client is never approved, so never gets an account number, so cannot pay by bank transfer or be paid out.~~ | `GET /client/identity-check`, `PUT .../details`, `PUT .../document`, `PUT .../photos/{kind}`, `POST .../submit` |
| ~~**Payout accounts and payouts.** No screen adds a mobile money number or bank account, enters its code, uploads proof, or lists payouts. The early withdrawal screen lists approved accounts, but a client has no way to make one.~~ Closed by the client payout screens PR (see [payouts.md](payouts.md), "Client screens"). | No client can ever be paid out, at maturity or early. | `/client/payout-destinations` (all six), `GET /client/payouts`, `GET /client/payouts/{id}`, `GET /client/withdrawal-limits` |
| ~~**Maturity choice after investing.** The choice is made at review, but cannot be changed afterwards, and paying out at maturity is never asked for.~~ Closed by the client payout screens PR. | The roadmap requires changing the choice before the maturity date. | `PUT /client/investments/{id}/at-maturity`, `POST /client/investments/{id}/pay-out` |
| ~~**Claiming an account carried over from Finvest 1.0.** No screen opens the invitation link, sends the code, sets the password or reports a problem.~~ Closed: `apps/client/src/app/claim.tsx`. | ~~Switch day sends every Finvest 1.0 client a link ([launch.md](launch.md), T+85). Without the screen the link leads nowhere.~~ | `GET /client/claim`, `POST /client/claim/start`, `.../complete`, `.../problem` |
| ~~**Forgotten password.** "Forgot password?" leads to a "not ready yet" page.~~ Closed: `apps/client/src/app/(auth)/reset`. | ~~A client who forgets their password is locked out for good.~~ | `POST /client/password/reset/start`, `.../complete` |
| ~~**Activity.** The Activity tab is a "coming later" page.~~ Closed: the Activity tab, with search, filters and a CSV on wide screens. | ~~Clients cannot see their payments, interest and payouts in one list, which Finvest 1.0 users have today.~~ | No single route. Built from investments, payments and payouts, or a new read route. |
| ~~**Devices and sign-in methods.** No screen lists or signs out other sessions, lists or removes a passkey, or turns off an authenticator app.~~ Closed: `apps/client/src/app/security` (devices, passkeys, authenticator). | ~~A client with a stolen phone cannot sign it out.~~ | `/client/sessions` (three), `GET /client/passkeys`, `DELETE /client/passkeys/{id}`, `GET`/`DELETE /client/authenticator` |
| ~~**Email address proof.** No screen asks for or confirms the email link.~~ Closed: `apps/client/src/app/verify-email.tsx` and Sign in and security. | ~~Statements are emailed only to a proved address ([decisions.md](decisions.md)), so no client gets one by email.~~ | `POST /client/email/verification`, `.../confirm` |
| ~~**Push registration.** The app never registers its push token.~~ Closed: `apps/client/src/push/registration.ts`. | ~~Push alerts, once switched on, reach nobody.~~ | `PUT /client/push-devices`, `POST /client/push-devices/remove` |

Also unused: `PUT /client/goals/{id}`, `PUT /client/goal-groups/{id}`, `PUT /client/goal-groups/{id}/me` and `PUT /client/autoinvest/plans/{id}` (editing). These are phase 4 and do not block launch.

### Console

Every console screen is built, but eight of them only work on the sample sign-in. On 14 November 2026 every one of them was on the real staff routes. In a real build they say they are not connected, because no HTTP client sits behind them.

| Screen | State on main | Staff routes waiting | Owner |
| --- | --- | --- | --- |
| ~~Products~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only~~ | `/staff/products`, `/staff/products/{code}/rates`, `/staff/rate-proposals` (five) | None |
| ~~Rates~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only~~ | Same as Products | None |
| ~~Investments~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only~~ | `/staff/investments`, `/staff/investments/{id}`, `/staff/interest-runs` | None |
| ~~Goals~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only, goals and group goals~~ | `/staff/goals`, `/staff/goals/{id}`, `/staff/goal-groups`, `/staff/goal-groups/{id}` | None |
| ~~Reconciliation~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only~~ | `/staff/payments/bank-transfers` (four) | None |
| ~~Migration~~ | ~~Sample data only~~ Closed by the console migration wiring PR (`src/screens/migration/http.ts`). | `/staff/migration` and its trials, control totals and sign-offs (seven) | |
| ~~Dealing Desk~~ Closed: on the real staff routes (checked end to end on 14 November 2026). | ~~Sample data only; the buffer tab is being connected~~ | `/staff/dealing/desk`, `/staff/dealing/orders` (seven) | Dealing desk thread (PRs #262, #265) |
| ~~Automatic Investing~~ | ~~Sample data only~~ Closed by the automatic investing staff routes PR. | `/staff/autoinvest/plans/counts`, `/staff/autoinvest/runs` | |

Four screens were still the placeholder page. The Review Queue is connected to `/staff/payout-destinations` and the held payout routes (`/staff/payouts/held`, `.../approve`, `.../decline`).

| Screen | What it needs | Routes |
| --- | --- | --- |
| ~~Money Movement~~ Closed: held-up, stranded and returned payouts on the real routes. | ~~Payouts that are held up or stranded, and deposits under way~~ | `GET /staff/payouts/held-up`; stranded payouts in PR #263 (payouts thread) |
| ~~Maturities~~ | ~~The daily maturity run and its exceptions~~ Closed by the Maturities screen PR. | `GET /staff/maturities` |
| ~~Finance~~ | ~~Day close, fee income, withholding tax owed and the accounting export~~ Built: income and costs, the books by code and the daily journal export for QuickBooks Online, Xero, Sage Accounting, Zoho Books or as a CSV ([finance.md](finance.md)). The ioTec collection charge is booked on each confirmed payment in. | `GET /staff/finance`, `POST /staff/finance/journal.csv`, `/staff/finance/account-codes` |
| Compliance | Screening results and monitoring alerts | `/staff/compliance` routes ([screening.md](screening.md)), with the rules that are on, reports to the FIA this year and a register CSV. Risk rating, source of funds, document requests and escalation wait on Solo |

~~Staff routes with no screen at all: `/staff/phone-changes` (three; the Clients screen does not show pending phone changes) and `/staff/sessions` (three).~~ Closed by the mobile number and devices PR: the client page has a Mobile number section, and every member of staff has Your devices.

### Backend

| Gap | Why it matters | Owner |
| --- | --- | --- |
| ~~**Reconciliation has no HTTP routes.** `import_statement`, matching and the two-person day close exist as services only.~~ Closed: `/staff/reconciliation` statements, matches and day close routes. | ~~Finance cannot import a bank or provider statement or close a day, so no statement is ever final ([documents.md](documents.md)) and the daily reconciliation after the switch ([launch.md](launch.md) section 6) cannot run.~~ | Payouts and reconciliation thread, to confirm |
| ~~**Payouts held for review have no staff routes.** `held_for_review` and the approve and decline events exist in `payouts.py`, but no route lists or decides them.~~ Closed: `/staff/payouts/held` and its approve and decline. | ~~A payout above the review amount, and every early withdrawal held for staff, can never be approved.~~ | Payouts thread, to confirm |
| ~~**Two-person settlement of a stranded payout.**~~ Closed: `/staff/payouts/stranded`. | ~~A payout the provider never answered for stays stuck.~~ | Payouts thread, PR #263 |
| ~~**Undoing a mistaken investment and refunding waiting money.**~~ Closed: `/staff/investment-corrections`. |  | Investing thread, PR #260 |
| ~~**Deposits credited to the wrong client.**~~ Closed: `/staff/payments/reassignments`. |  | Running thread "Fix deposits credited to the wrong client" |
| ~~**Tax certificates and URA withholding reports.**~~ Closed: tax certificates in Documents and `/staff/tax`. |  | Running thread "Build tax certificates and URA reports" |
| ~~**Sending push, SMS and email.**~~ Closed: every notification event is raised and sent; SMS through Africa's Talking, with ioTec Messaging as the fallback. |  | Notifications thread |
| ~~**Next of kin and the suitability questions.** Not built.~~ Backend closed by the next of kin and investing questions PR ([client-profile.md](client-profile.md)). | ~~Listed on the roadmap and "usually asked for by the regulator".~~ | Client profile thread |
| **Anti-money-laundering.** ioTec Verify's AML screening runs once at hand-in (once `FINVEST_IDENTITY_PROVIDER=iotec`). The provider-neutral screening module, with a 30 day rescreen and two-person cases, is built ([screening.md](screening.md)) but runs with no provider until the Sigma360 account exists. Payouts wait for a clear screen, and transaction monitoring rules open cases for staff. No Financial Intelligence Authority report is generated: staff file it on the FIA's system. | Listed on the roadmap's back office checklist and likely a licence condition. | Screening thread |
| **The real device checks** in [launch.md](launch.md) have not been run. | A launch condition. | Engineering, once Android and iPhone builds exist |
| **Web end-to-end tests** cover the main client and console journeys. The claim has none yet, though its screen now exists. Payout accounts and the identity check have theirs. | Each new screen above should add its journey. | Each screen's thread |

### Follow-ups owed from reviews

The backend reviewer keeps a list of follow-ups asked for in reviews (team memory "finvest-review-followups"). The ones that touch money before launch: the investing sweep must not cancel while a deposit is open; stop the interest run after repeated failures and show it to staff; an idempotency key on `prepare_order` and a second person to settle a dealing order; the payouts closer must not be the importer, enforced in the database; the second signature for a day close enforced in the database; caching `/health/operations`.

Transfers of investments between clients: screening holds and transaction monitoring are both built (see [investing.md](investing.md) and [screening.md](screening.md#transaction-monitoring)).

Company and group accounts: the signatories are screened as clients, but nothing screens the organisation itself, its name and registration number, against sanctions lists. That is acceptable while an organisation cannot invest ([organisations.md](organisations.md)), and must be built before investing as an organisation opens.

## Design gaps

A pass of the [design canvas](design.md) against `main` at `f60cf66` (14 November 2026, 20:00 UTC), looking past whole screens at states, copy, roles, emails and the website. Every canvas screen, email event and website page exists. What is left is listed here, most important first. Luganda and USSD are out of scope for now; the selfie on the provider's page and the PIN being phone only are deliberate.

| Gap | Canvas | On main | Owner |
| --- | --- | --- | --- |
| **Accepting the terms is not recorded.** | SignupStart ("I agree to the terms and the privacy notice"), WebDocuments ("Terms you accepted", a PDF in Documents) | The sign-up tick box is checked in the app only. The API never stores which terms, which version or when, and Documents has no agreement. | Client sign-in, with the documents module |
| **Nobody can check a document is genuine.** | WebDocuments: the signed PDF "carries a code they can check on our website" | PDFs say "contact Finvest and quote" the reference. There is no check page on finvestug.com. | Statements, with the website |
| **Large withdrawals ask only for the PIN.** | Security, "Asked again for": withdrawals over UGX 1,000,000 need a passkey | The app PIN confirms every withdrawal. Sign in and security has no "Asked again for" list. | Owner's choice first, then client sign-in |
| **A reused bank receipt is not flagged.** | AdminDeposit: "File has not been used on any other deposit" | The receipt's SHA-256 is stored but never compared, so staff are not told a file or bank transaction number was used before. A statement line still cannot confirm two payments. | Payments |
| **No counts in the console menu.** | AdminNav: Review Queue 7, Identity Checks 2, Complaints 5 and so on | The menu has labels only. | Console overview and review queue |
| **Statement options.** | WebDocuments, Make a statement: Include (all investments and goals, or one), PDF or Excel | Any period up to a year, PDF only, everything. | Statements |
| ~~**Role table differs from the canvas.**~~ Closed: the owner kept the table as built (see [decisions.md](decisions.md)). | ~~AdminTeam, "What a role can do"~~ | ~~Finance cannot see the audit log; support and compliance can reset a client's PIN and send statements; compliance decides payout accounts and transfers. Each was decided for now in [decisions.md](decisions.md); the table itself still waits for the owner.~~ | Owner |
| **About page has no team.** | SiteAbout, "The people responsible for your money", and company figures | Left out until Finvest gives names, roles and figures. | Website, once the owner provides them |
| **Investment detail.** | Investment: "Add more" and a term progress bar | Neither. "Add more" may not suit a bill whose amount is fixed at the auction. | Investing |
| **Export positions.** | AdminInvestments: "Export positions" | Reports has the daily position by account, but no list of open investments. | Investing |

## Accessibility

A WCAG 2.1 AA pass over every main screen of the client app and the console on 14 November 2026, phone and wide, with axe in the browser tests (`apps/web-e2e/tests/accessibility.*.spec.ts`, which now fail on any violation) and by hand for keyboard focus and touch targets. Contrast passed everywhere. Fixed in the same pass: on the web, the client app's radios, switches, checkboxes and tabs never told a screen reader which one was chosen (React Native Web 0.21 ignores `accessibilityState` and `accessibilityValue`; they are now `aria-*` props), spinners and progress bars had no name, a busy button had no name, two text boxes hid their focus outline, some filter chips and date fields were under 44 points, and the console's wide tables could not be scrolled by keyboard. What is left:

| Gap | Why it matters | Owner |
| --- | --- | --- |
| **Spoken names of whole cards.** An investment, payout, plan or notification card is one button whose spoken name is a summary ("91 Day Treasury Bill, Running, worth UGX 5,123,715") rather than every word shown on it. axe's "label in name" rule (WCAG 2.5.3) is switched off in the client test for this. A voice control user who says the card's title still reaches it, because each summary starts with the title. | Should be confirmed with a screen reader and voice control user before launch. | Client app |
| **Real phones not checked.** axe runs on the web build only. TalkBack on Android and VoiceOver on iPhone have not been tried; the `aria-*` props used are the ones React Native maps on both. | Most clients use the phone apps. Add to the real device checks in [launch.md](launch.md). | Engineering, once builds exist |
| **Console touch targets.** Some console controls are smaller than 44 by 44 on a phone: tab chips (36 to 40 tall) and inline links in tables. WCAG 2.1 AA has no size rule and these are over the 24 point minimum of WCAG 2.2. The identity check's "Show" link for the document number, which was 33 by 18, now takes a 44 by 44 tap. | Staff use the console on a computer. | Console |
| **Screen reader pass by a person.** No one has used either app end to end with a screen reader. axe cannot tell whether names and announcements make sense, or whether a change (a payment confirmed, an error) is announced. | The regulator may ask; clients with low vision use phones with screen readers. | Engineering |

## Built, but waiting on a provider

Each of these is blocked on something only the owner can get, and is listed again in the next section.

| Gap | State |
| --- | --- |
| **ioTec payments credentials.** The collections adapter is built (`FINVEST_PAYMENTS_PROVIDER=iotec`, see [payments.md](payments.md#iotec-pay)). It needs `FINVEST_IOTEC_CLIENT_ID`, `CLIENT_SECRET`, `WALLET_ID` and `CALLBACK_TOKEN` set in the deploy, and a first try against ioTec's test wallet. | No mobile money payment can be taken in production until they are set. |
| ~~**ioTec payouts adapter.** Only the sandbox exists.~~ Closed: built (`backend/app/modules/payouts/providers/iotec.py`); needs the same credentials as payments. | ~~No payout can be sent.~~ |
| **SMS credentials.** Both adapters are built (`FINVEST_SMS_BACKEND=africastalking`, `FINVEST_SMS_FALLBACK=iotec`), but the worker needs `FINVEST_SMS_AFRICASTALKING_USERNAME` and `FINVEST_SMS_AFRICASTALKING_API_KEY` (and the FINVEST sender id once Africa's Talking approves it), plus `FINVEST_SMS_IOTEC_CLIENT_ID` and `FINVEST_SMS_IOTEC_API_KEY` for the fallback. Both have been tested only against fakes: before launch, one real message through each must confirm the request shape and the status codes (and ioTec's `ReferenceId` lookup). | Until they are set, **no sign-up, SMS sign-in, payout account code or claim code can be delivered**, so nobody can sign up or claim an account. This is the most serious blocker. |
| ~~**ioTec Verify: the name a network holds for a number.**~~ Closed: built (`backend/app/modules/payouts/providers/iotec_names.py`); needs the Verify credentials listed below. | ~~Mobile money payout accounts wait at `awaiting_proof` for good ([payouts.md](payouts.md)).~~ |
| **Identity check provider.** Every client is reviewed by a person. | Works, but every sign-up needs staff time. |
| **Public site facts.** `registeredName`, `regulator` and `licenceNumber` in `apps/site/src/data/company.ts` are empty, and `pnpm placeholders --strict` fails until they are set. The six legal pages are written out in full (`apps/site/src/data/legal.ts`) but are drafts: each says so until Finvest's lawyers review it and its `stage` is set to `reviewed`, and the strict check fails until then. | The site cannot be opened to search engines. |

## What only the owner can provide

Credentials and accounts:

- ioTec API access: the API details, the callback signature scheme, sandbox access and production credentials, for payments, payouts, SMS and Verify. Also the ioTec questions in [decisions.md](decisions.md) (fees, settlement speed, payout limits, which account a bank payout leaves).
- The client trust account's details, to be entered by two people on the console's Settings screen, and finance's confirmation that it is the account reconciliation imports.
- The GitHub secrets and variables still missing for provisioning and deploys (listed in [infra/README.md](../infra/README.md)), including `FINVEST_EMAIL_HASH_KEY` before the next release, `FINVEST_IDENTITY_DATA_KEY`, `FINVEST_AUTHENTICATOR_KEY`, `RESEND_HOOK_SECRET` and at least two `ALERT_EMAILS`.
- The Cloudflare token's Zone WAF Edit permission, so the rate limit can be applied.
- Repository settings: secret scanning, push protection and Dependabot ([security.md](security.md) finding 8), and "Keep my email addresses private" on GitHub.
- The go-ahead for the first Provision and the first production deploy.
- ioTec Verify credentials for the worker (`FINVEST_IOTEC_VERIFY_CLIENT_ID`, `FINVEST_IOTEC_VERIFY_API_KEY`), to turn on the automatic identity checks and screening.
- The Sigma360 account for screening (chosen by the owner): its API key, the filter set to use, and a test key.

Legal and regulatory:

- The company's registered name, company number, regulator, licence number, office address and data protection registration for the website, and the lawyers' review of the drafted terms, privacy notice, risk disclosure, fees, complaints policy and cookies pages.
- Whether the regulator must be told of the change of system, and how.
- Counsel's answers in [decisions.md](decisions.md): money in transit through the trust account, data hosted in London, group goals as a collective scheme, the tax on interest and its rounding, and the old system's higher interest.
- How long records must be kept, which decides the retention jobs for the ledger, the audit trail and email events.

Launch:

- The penetration testing firm, the test dates and the signed authorisation letter.
- A sample Finvest 1.0 export, and then a real one for the trial import.
- At least three admins with a passkey or authenticator app.
- The switch date, the go or no go, approval of the invitation texts and of the recovery targets.
- The open owner questions in [decisions.md](decisions.md). Each is built with a working default, so none blocks engineering, but each should be confirmed before launch.
