# Roadmap

## Build order

| Phase | What ships |
| --- | --- |
| 0. Foundations | Project setup, sign-in, permissions, ledger, audit trail, console shell with roles |
| 1. Money in and out | Identity checks, paying for an investment by MTN, Airtel or bank transfer with receipt, payouts, review queue, reconciliation |
| 2. Investing | Products, investment starts on confirmed payment, daily interest, portfolio, maturity and rollover, dealing desk, statements |
| 3. Migration and launch | Move Finvest 1.0 clients across, run both in parallel, penetration test, switch over |
| 4. Goals and growth | Goals, saving rules, automatic investing, insights, sending investments, group goals |

Goals are phase 4 on purpose. They sit on top of payments, the ledger and investing, and those three have to be proven first.

## Feature checklist

Every item below has a screen on the [design canvas](design.md).

### Client app

| Feature | Why it matters |
| --- | --- |
| Web version | Finvest 1.0 is a web app. Existing clients on laptops need a responsive web version, built from the same Expo code. About five wide layouts (home, invest, portfolio, activity, documents), and everything else reuses the phone screens in a centred column. |
| Maturity choice | Choose roll over or pay out to mobile money or bank, and change that choice before the maturity date |
| Linked bank accounts and mobile money numbers | Saved payout destinations, each in the client's name, confirmed by SMS code, with proof of ownership checked by staff and a 24 hour wait before the first payout |
| Transaction detail and receipt | Tapping any activity row opens a receipt that can be shared or downloaded |
| Next of kin and beneficiaries | Standard for investment accounts and usually asked for by the regulator |
| Risk and suitability questions | A short questionnaire before the first investment |
| Company, joint, SACCO and group onboarding | Signing up a group and its signatories |
| Empty, error and offline states | First-time screens with no money, failed payments, no network, and app maintenance |
| Devices and sessions | See and sign out other devices, and re-approve a new phone |
| Tax certificates | Withholding tax certificates for clients who file returns, without showing the rate in the app |
| Account closure and dormant accounts | Required flows, even if rarely used |
| Low-data access | Luganda language option, and possibly a USSD balance check for clients without smartphones |
| US dollar product | How a client funds and withdraws dollars, and where the exchange rate is shown |

### Back office

| Feature | Why it matters |
| --- | --- |
| Treasury and dealing desk | Clients' payments start an investment as soon as they are confirmed, but someone still has to place that pooled money into real Treasury bills at auction or on the secondary market and allocate holdings back to clients. Staff need the daily position, what to buy, and allocation. |
| Liquidity for early withdrawal | A liquidity buffer pays small early withdrawals at once, and larger ones are funded by selling on the secondary market. The dealing desk tracks the buffer against a target. |
| Maturity and rollover processing | A daily run with exceptions for staff, the same way deposits work |
| Anti-money-laundering | Sanctions and politically exposed person screening, transaction monitoring rules, and reports to the Financial Intelligence Authority |
| Rates and pricing approval | A rate change is proposed by one person and approved by another, with an effective date |
| Finance and accounting | Fee income, withholding tax owed to URA, and an export to the accounting system |
| Complaints register | A regulated record separate from general support chat |
| Migration from Finvest 1.0 | See [migration.md](migration.md) |
