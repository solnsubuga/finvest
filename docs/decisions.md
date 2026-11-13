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
| 2026-11-13 | Clients save their own payout bank accounts and mobile money numbers, with proof of ownership. |
| 2026-11-13 | Domains: finvestug.com for the website, app.finvestug.com for the client app, admin.finvestug.com for the back office. |
| 2026-11-13 | Infrastructure is Terraform on DigitalOcean and Cloudflare. |
| 2026-11-13 | Provisioning and deployment follow Fidiba. Terraform creates every DigitalOcean resource inside one project named `finvest`, generates the database role passwords and bucket key, and writes the deployment settings into the GitHub environments. See [infra/README.md](../infra/README.md). |
| 2026-11-13 | Terraform state may hold the credentials Terraform generates (database admin connection, role passwords, bucket key, origin certificate key). It is the one exception to keeping secrets out of Terraform state. The state bucket is private and only the Provision workflow on `main` can read it. |
| 2026-11-13 | Brand is black and lime with Plus Jakarta Sans. See [design.md](design.md). |
| 2026-11-13 | All work reaches `main` through pull requests. UI work includes screenshots. Commits on `main` carry no AI attribution. |

## Open

| Question | Who answers |
| --- | --- |
| Is money in transit through the trust account covered by the current licence? | Counsel |
| Data protection: registration, consent wording, and whether personal data may be stored outside Uganda. This decides the hosting region. | Counsel |
| Do group goals with two approvals count as a collective scheme? | Counsel |
| ioTec: fees, sandbox, settlement speed from the ioTec wallet to our bank, payout limits, and whether Verify sends its own code. | ioTec |
| Identity check provider: Smile ID coverage and pricing for Uganda. | Provider |
| Will passkeys become compulsory for clients? | Owner |
| Is api.finvestug.com the backend address? | Owner |
| Card payments. Stripe is not available to Ugandan businesses. A local card acquirer, capped, is a possible later addition. | Owner |
