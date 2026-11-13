# Decisions and open questions

A running log. Add a row when a decision is made, and move an open question up when it is answered. Do not rewrite history: if a decision is reversed, add a new row that says so.

## Decided

| Date | Decision |
| --- | --- |
| 2026-11-13 | Backend is Python with FastAPI, Pydantic, SQLAlchemy 2, Alembic and PostgreSQL. |
| 2026-11-13 | Background jobs live in PostgreSQL, following the Fidiba backend. No Celery and no Redis. |
| 2026-11-13 | Client app is React Native with Expo, one codebase for Android, iPhone and web. |
| 2026-11-13 | Clients get a web version at launch, built from the same Expo code. |
| 2026-11-13 | Back office console is React. |
| 2026-11-13 | The ledger is double-entry and is the only place money moves. |
| 2026-11-13 | No wallet for now. Every payment in is for a specific investment or goal, and every payment out goes straight to mobile money or bank. |
| 2026-11-13 | ioTec for payments (ioTec Pay), SMS (Messaging) and phone number verification (Verify) for now, each behind its own adapter. |
| 2026-11-13 | Email provider is Resend. |
| 2026-11-13 | No bank partner yet. The bank sits behind an adapter that works with uploaded statements today and can take a bank API later. |
| 2026-11-13 | Early withdrawals are funded from a liquidity buffer first, then by secondary market sales. |
| 2026-11-13 | Account numbers are 10 digits: 2 for type, 7 random, 1 Luhn check digit. See [account-numbers.md](account-numbers.md). |
| 2026-11-13 | An SMS code alone never signs a client in. Password plus a second step, passkey first. Staff use password and passkey with no SMS option. |
| 2026-11-13 | Staff second step is a passkey or an authenticator app code, whichever the member of staff has set up. This replaces "password and passkey" for staff in the sign-in row above. Still no SMS option for staff. |
| 2026-11-13 | A second step is mandatory for every staff account. Either a passkey or an authenticator app counts, and passkeys are not required for any role. An account with neither is blocked from the console. |
| 2026-11-13 | Which console screens each staff role opens: the table in `apps/console/src/auth/stub.ts` is the working default, and the backend permissions module seeds roles from it. In short: Operations opens Money Movement, Reconciliation and Migration. Finance opens the Review Queue but not Complaints. Support opens Clients. Only Admin opens Team and Permissions and Settings. The owner can change it at any time. |
| 2026-11-13 | Clients can also use an authenticator app as the second step, and can sign in with any second step they have set up: passkey, authenticator app or SMS code. This extends the row above. Staff sign-in is covered by the two rows above. |
| 2026-11-13 | Working default: signing in with an authenticator app does not carry the 24 hour withdrawal limit. Only a code by SMS or email does, because the limit is there to stop someone who holds only the SIM card. The owner can change it at any time. |
| 2026-11-13 | Clients add a passkey or set up an authenticator app themselves, from Profile, Sign in and security. An authenticator app is switched on only after a code from the app confirms the key was saved. The key is shown once during set-up and the app never stores it. Adding or removing a second step needs a session opened in the last 10 minutes. An older session is asked to log in again. |
| 2026-11-13 | Clients save their own payout bank accounts and mobile money numbers, with proof of ownership. |
| 2026-11-13 | Domains: finvestug.com for the website, app.finvestug.com for the client app, admin.finvestug.com for the back office. |
| 2026-11-13 | Infrastructure is Terraform on DigitalOcean and Cloudflare. |
| 2026-11-13 | Provisioning and deployment follow Fidiba. Terraform creates every DigitalOcean resource inside one project named `finvest`, generates the database role passwords and bucket key, and writes the deployment settings into the GitHub environments. See [infra/README.md](../infra/README.md). |
| 2026-11-13 | Data is hosted in DigitalOcean London (`lon1`), for staging and production. Confirmed by the owner. |
| 2026-11-13 | Terraform state may hold the credentials Terraform generates (database admin connection, role passwords, bucket key, origin certificate key). It is the one exception to keeping secrets out of Terraform state. The state bucket is private and only the Provision workflow on `main` can read it. |
| 2026-11-13 | APScheduler keeps time for the scheduler process and nothing else. It only says when a schedule is due. PostgreSQL owns the leadership lease and each scheduled run, unique by name and fire time, and APScheduler's own job stores are not used. This adds no Celery or Redis. |
| 2026-11-13 | Decided for now, by the investing thread. The catalogue is seeded with 91 Day Treasury Bill (12.50% a year, minimum UGX 100,000), 182 Day Treasury Bill (13.75%, minimum UGX 200,000) and USD Fixed Income (7.25%, open term, minimum $500). Tax on interest is 15% for the Treasury bills and 5% for the dollar product, as on the design canvas. See [investing.md](investing.md). |
| 2026-11-13 | Decided for now. Interest to date is rounded down to the smallest unit, tax on it is rounded down, and the client earns the difference. Each day's posting is the total to date minus the total the day before, so the postings always sum to the formula. A year is 365 days in every year. |
| 2026-11-13 | Decided for now. A rate change takes effect on a day after its approval and applies only to investments that start on or after that day. A running investment keeps the rate and the tax it started with. |
| 2026-11-13 | Decided for now. The dollar product is seeded paused. It opens when paying in and out in dollars is built. |
| 2026-11-13 | Decided for now, by the dealing desk thread. The dealing desk is its own backend module, `dealing`, separate from investments. It reads what clients are owed from investments and keeps orders, bills held and allocations in its own tables. See [dealing-desk.md](dealing-desk.md). |
| 2026-11-13 | Decided for now. The desk's position compares the principal clients are owed with the cost of the bills held. Interest is left out of both sides and bills are not revalued day to day. Income or loss on a bill is recognised when cash arrives, at a sale or at maturity. |
| 2026-11-13 | Decided for now. Every order to buy or sell bills is prepared by one member of staff and approved by another, and the result from the custodian's note is recorded by one person and confirmed by a different one. Only a confirmed result posts to the ledger. |
| 2026-11-13 | Decided for now. Bills are allocated to client investments oldest payment first, each covered in full before the next. |
| 2026-11-13 | Decided for now. This details the early withdrawal row above: a withdrawal up to the buffer amount is paid from the liquidity buffer at once, a larger one is paid from a secondary market sale, and one withdrawal is never split between the two. If the buffer is below its floor, small withdrawals queue. Starting rules, from the design canvas: target UGX 150,000,000, floor UGX 100,000,000, up to UGX 5,000,000 from the buffer, and 2 working days for a withdrawal that waits for a sale. |
| 2026-11-13 | Decided for now. The liquidity buffer is not a separate bank account. It is the desk's cash beyond the client money waiting to be placed. |
| 2026-11-13 | Decided for now, by the website thread. The public website is a fully static Astro build with no server of its own. Rates are built into the pages and the visitor's browser replaces them from `GET /public/products` on the API, so a page still shows rates when the API is down. The site carries only the yearly rate after tax. See [apps/site/README.md](../apps/site/README.md). |
| 2026-11-13 | Decided for now. The website's calculator shows an estimate worked out from the rate on the page, labelled as one, and replaces it with the exact figure from `GET /public/quote` when the API answers. The site never works out a figure it presents as exact. |
| 2026-11-13 | Decided for now. The website tells search engines to stay out until launch. The build setting `PUBLIC_INDEXABLE=true` opens it, and that is the owner's call at launch. |
| 2026-11-13 | Decided for now. A paused product is listed on the website as "Coming soon" with no button to invest. The dollar product shows this way today. |
| 2026-11-13 | Decided for now. The website's "Send a message" form sends nothing itself, because the site has no server and stores nothing. It opens the visitor's own email app with the message addressed to support@finvestug.com. A form that posts to the backend can replace it once a public contact endpoint exists. |
| 2026-11-13 | Decided for now. The website's legal pages hold structure only: headings and a line on what each section will cover, under a notice that the wording comes from Finvest's lawyers. |
| 2026-11-13 | Decided for now. The website is not opened to search engines until every claim in `apps/site/scripts/claims.json` is signed off by the owner and counsel, and the registered company name, regulator and licence number are entered in `apps/site/src/data/company.ts`. `pnpm placeholders --strict` checks both. |
| 2026-11-13 | Decided for now. Production does not serve the public website until the `SITE_PUBLISHED` variable of the production environment is `true`. Publishing is the owner's decision, made at launch. Staging always serves it, and every address except the production website asks search engines to leave it out (`X-Robots-Tag`). |
| 2026-11-13 | The public website shows no placeholders (owner). Where a value is a choice, the website thread chose one, decided for now: an identity check is "usually reviewed within one working day", a complaint is resolved within 10 working days, there is no fee to open an account or to invest and the rate shown is what the client earns, and early withdrawal is "ask in the app at any time and see the exact amount before you confirm". Where the value is a fact only Finvest knows (phone number, office address, registered name, regulator, licence number, custodian bank, auditor, client and team details), nothing is made up: the line is left out until the fact is entered in `apps/site/src/data/company.ts`. |
| 2026-11-13 | Passwords are hashed with Argon2id (library `argon2-cffi`): 64 MiB of memory, 3 passes, 1 lane, run off the event loop in a bounded thread pool. The costs are settings and can only be raised. It needs no Redis or other service. |
| 2026-11-13 | Decided for now. An investment takes the rate and tax in force on the day the client confirms the review, and stays payable at those terms for 72 hours. Unpaid after that, it is cancelled. Its start date is the business day the payment was confirmed. |
| 2026-11-13 | Decided for now. A client must have passed the identity check before investing, and can have five investments waiting for payment at once. |
| 2026-11-13 | Brand is black and lime with Plus Jakarta Sans. See [design.md](design.md). |
| 2026-11-13 | Decided for now: money credited by mistake that has already moved on is not clawed back out of the client's investment. It is corrected by a new ledger entry that records a receivable against the client who got it, and the receivable is then recovered or written off (the owner chose "receivable, then recover" on a decision card in the payments thread). Every manual ledger correction, including a plain reversal, needs two different members of staff: one proposes with a reason and another approves. The database enforces this. See [ledger.md](ledger.md#corrections). The owner can revisit it once counsel or the regulator has a view. |
| 2026-11-13 | Decided for now, by the migration thread. The import spreadsheet is two CSV sheets with fixed columns, one for clients and one for investments, for Finvest 1.0 exports and hand-kept records alike. Dates are `YYYY-MM-DD` only. See [migration.md](migration.md). |
| 2026-11-13 | Decided for now. Only running investments and orders awaiting payment are imported. Redeemed investments and past transactions stay in Finvest 1.0, kept read-only. Payout bank accounts are not imported: a client adds them in the new app, with proof of ownership, like everyone else. |
| 2026-11-13 | Decided for now. A migrated client with no phone number, an unverified identity or an expired document does not block the switch. Their money comes across and keeps earning, and staff follow up. Two clients sharing a phone number or an identity document do block it. |
| 2026-11-13 | Decided for now (the owner was asked on a decision card and had said not to wait). An identity that Finvest 1.0 verified is carried over as approved at the switch, with a new account number, and the client proves who they are with a selfie matched to the ID on file when they claim. A client who was not verified, or whose document has expired, redoes the ID check. |
| 2026-11-13 | Decided for now, by the migration thread with the investing thread. A migrated investment is opened in the ledger with the interest the new formula gives for its original start date, rate and tax, not with the old system's figure. A difference between the two is listed in the finance check and signed off with a reason before the switch. It is never posted. |
| 2026-11-13 | Decided for now, by the notifications thread. A module tells a client something by calling `notifications.contract.notify` in the transaction of the change, with an event name from one catalogue and a key that makes it happen once. See [notifications.md](notifications.md). |
| 2026-11-13 | Decided for now. Every notification is kept in the app. Push, SMS and email are chosen by the client per kind of alert. Defaults: money by push and SMS, investments by push, daily earnings in the app only, account by push and email, security by all three, help and news by push. Security alerts cannot be silenced: SMS or email always stays on. |
| 2026-11-13 | Decided for now. Staff can change the words of a client message in Settings, but a change needs two people: one proposes, a different one approves. An edited message can only link to finvestug.com and cannot contain a percent sign. The database enforces the two-person rule. Both people need the `wordings.change` permission, which is separate from opening Settings and is held by the admin role only. |
| 2026-11-13 | Decided for now, by the migration thread. A trial import keeps its counts, totals, differences and problem list, with a fingerprint of each file, and not the files or any cell value. A sign-off is proposed with figures read from the newest trial and decided by a different member of staff, and the database enforces both. See [migration.md](migration.md). |
| 2026-11-13 | All work reaches `main` through pull requests. UI work includes screenshots. Commits on `main` carry no AI attribution. |
| 2026-11-13 | A payout account whose name differs from the client's ID is never approved, not even by two staff. The same holds for a mobile money number: a different registered name is refused outright. |
| 2026-11-13 | Decided for now: once a day is reconciled and signed off, the ledger is closed through it and the database refuses any entry dated on or before it. A closed day is never reopened. A mistake found later is corrected by an entry dated in an open day. Today and future days cannot be closed. See [ledger.md](ledger.md#closing-a-day). Asked for in review and built without waiting for the owner, who can revisit it. |
| 2026-11-13 | Decided for now, by the identity checks thread. A client's document number, date of birth and TIN are encrypted field by field before they reach the database, with a key the database never holds (`FINVEST_IDENTITY_DATA_KEY`). Names and the document expiry date stay readable so staff can search and expiry can be checked. And a person approves every client: a clean result from automatic checks is flagged as passed and still waits for staff, until the owner allows approval with no person involved. See [identity-checks.md](identity-checks.md). |

## Open

| Question | Who answers |
| --- | --- |
| Is money in transit through the trust account covered by the current licence? | Counsel |
| Data protection: registration and consent wording for data hosted in London. | Counsel |
| Do group goals with two approvals count as a collective scheme? | Counsel |
| ioTec: fees, sandbox, settlement speed from the ioTec wallet to our bank, payout limits, and whether Verify sends its own code. | ioTec |
| Identity check provider: Smile ID coverage and pricing for Uganda, or ioTec Verify if it can check identity documents. Until one is chosen every client is reviewed by a person. See [identity-checks.md](identity-checks.md). | Owner |
| Identity checks: may a client be approved with no person involved when every automatic check passed? Built as no since 2026-11-13 (see Decided); one switch turns it on. And confirm field-level encryption of the document number, date of birth and TIN, in use as the working default. | Owner |
| Will passkeys become compulsory for clients? | Owner |
| Confirm that signing in with an authenticator app carries no 24 hour withdrawal limit. In use as the working default since 2026-11-13 (see Decided). | Owner |
| A client signed in with an SMS code can add a passkey or an authenticator app. Should that end the 24 hour limit early? Built as no, in the app and in the sign-in API: the limit runs its full 24 hours, and the screen says so. | Owner |
| Is api.finvestug.com the backend address? | Owner |
| Confirm the role to screen table for the console. In use as the working default since 2026-11-13 (see Decided). It decides who can open Money Movement, Reconciliation and the other screens. | Owner |
| Card payments. Stripe is not available to Ugandan businesses. A local card acquirer, capped, is a possible later addition. | Owner |
| Payouts: is the UGX 5,000,000 mobile money limit per payout (built so far) or per day? Are there daily limits or bank limits? A payout above UGX 10,000,000 is held for staff approval, as a working default. Confirm the amount. | Owner |
| Payouts: is a bank payout sent by ioTec taken from the ioTec wallet or from the trust bank account? Built so far: the books show it leaving `bank.trust`. | ioTec |
| Payouts: does the 24 hour wait on a new destination run from the staff approval (built so far) or from when the client added it? | Owner |
| Payouts: is a mobile money number proved by the name the network holds for it (built so far), and can ioTec return that name? | Owner, ioTec |
| Payouts: staff may approve a name with a name left out, such as a middle name, when at least two names agree (built so far). A name spelt differently is refused. Confirm the left-out name allowance. | Owner |
| Payouts: mobile money prefixes accepted are MTN 76, 77, 78, 39 and Airtel 70, 74, 75, 20. Confirm the list. | Owner, ioTec |
| Payouts: a client can cancel a withdrawal only while it is held for review (built so far). Should they be able to cancel before it is sent? | Owner |
| Migration: a sample Finvest 1.0 export, or the old database tables, to check the import columns against. | Owner |
| Migration: Finvest 1.0 identity documents with no expiry date on file are treated as still valid (built so far). Should those clients redo the ID check instead? | Owner, counsel |
| Migration: should payout accounts and past transactions from Finvest 1.0 be brought across? Built: no. | Owner |
| Migration: when Finvest 1.0 shows a client more interest than the formula gives, is the old figure honoured? Built: no, the investment opens with the formula's figure and finance signs off the difference. | Owner, counsel |
| Investing: confirm the tax on interest (built: 15% on Treasury bills, 5% on the dollar product, from the design canvas) against what URA requires Finvest to withhold. | Owner, counsel |
| Investing: confirm the minimums. The canvas shows $500 for the dollar product in one place and $100 in another. Built: $500. | Owner |
| Investing: if the tax rate changes by law during a term, should running investments change with it? Built: no, terms are fixed at the start. | Owner, counsel |
| Investing: is the tax fraction rounded down in the client's favour acceptable to URA, or must tax round up? | Owner, counsel |
| Dealing: confirm the buffer rules (built: target UGX 150m, floor UGX 100m, up to UGX 5m from the buffer, 2 working days for a sale), and who funds the buffer at the start. | Owner |
| Dealing: who are the custodian and the primary dealer, and what is the smallest step a bid accepts? The canvas leaves the dealer blank. The lot size is a setting until this is known. | Owner |
| Dealing: may bills be carried at cost with income taken at sale or maturity (built), or do the auditors want them revalued daily? | Owner, auditors |
| Dealing: when a sale to pay an early withdrawal makes a loss, who bears it: Finvest (assumed so far, nothing posts it yet), or the client through the early withdrawal price? | Owner |
| Website: facts left off the pages until supplied. Registered company name, regulator and licence number, custodian bank, auditor, office address, phone number, the team, and company figures (year founded, clients, money invested). Enter them in `apps/site/src/data/company.ts`. | Owner |
| Website: links to the App Store and Google Play are left off until the apps are published. | Owner |
| Website: the terms of early withdrawal as told to the public (how a bill is sold, at what price, any notice period), how long an identity check takes while every check is reviewed by a person, the time to resolve a complaint, and fees. The site carries the values chosen for now (see Decided). | Owner, counsel |
| Website: sign off the claims in `apps/site/scripts/claims.json` (regulated, held in the client's name, books checked daily, and the rest). | Owner, counsel |
| Investing: is 72 hours the right time to hold a quoted rate for a bank transfer, and should the client be able to cancel an unpaid investment themselves? | Owner |
| Notifications: confirm the default channels, above all SMS on by default for money alerts. Each text is paid for. | Owner |
| Notifications: should a change to a message's words need a second person (built so far), or may one admin change them alone? | Owner |
