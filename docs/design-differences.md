# Design differences from the canvas

Where the build differs from the design canvas on purpose, and what was built to close the gaps the full app QA of 15 November 2026 found (docs/qa, findings F-009, F-012, F-024, F-025). Rows marked "for now" were decided by the build threads and wait for the owner's answer on a decision card. The options put to the owner are in the project's `qa-design-differences/choices.md`.

## Built to match the canvas

| Screen | What was added | Finding |
| --- | --- | --- |
| Client Portfolio (phone) | Progress through the term on each running investment | F-024 |
| Client Invest list | When interest is paid, on each product | F-024 |
| Client Product | A facts table: kind, term, smallest amount, interest, rate after tax, start | F-024 |
| Client Maturity choice | What each choice comes to, and what happens if nothing changes | F-024 |
| Client Activity | Signed amounts (plus in, minus out), search and Get a statement on the phone | F-024 |
| Client Goal detail | Next saving, Edit plan, Withdraw (when one saving is running) | F-024 |
| Client Goals | A link to automatic saving | F-024 |
| Client Insights | Put it to work, which opens Invest | F-024 |
| Console Maturities | Summary tiles (today, two weeks ahead, what clients chose, held) and Export day by day | F-025 |
| Console Overview | Export the day's figures | F-025 |
| Every rate label | Rounded down, so it never overstates (10.625% shows as 10.62%) | F-012 |

Already on main when checked, though the QA table listed them: the investment detail's "today" figure and term progress (PR 608), Rates history, the Dealing desk's orders and allocation sections, and the empty state of Maturities' day by day table.

## Kept as built (canvas changed), for now

- **Early withdrawal is whole only** (F-009). Part withdrawals come after launch.
- **Amounts are typed, not slid.** Easier with a keyboard and a screen reader.
- **Wide sidebar** is grouped and shows Signed in as.
- **Maturity choice on Review** has the choices, not a switch.
- **Invest** has no banner or filter chips; **pay** has no Review, Pay, Done stepper; network tiles stay grey.
- **Bank transfer receipt** is uploaded as a file; no photo reading.
- **Documents** are cards; **Security** has no "Asked again for" section.
- **Goals**: reworded group card, no group tile on step 1, chips for the plan, a first saving form for funding, no Pause (goals have no schedule to pause).
- **Insights** shows six months.
- **Console**: Review queue as rows with Open; announcements in Settings; regulator report in Reports; bank transfer check at the top of Reconciliation; Close the month in Finance; no New report to FIA (filed on the FIA system); no New role (roles are fixed); no Fees card (no client fees); no dollar rate row (dollars paused).

## Built after the first pass

- Console Support: Invested and Payout pending in About this client. Read from the client's ledger balances (the same figures the client page shows), only for staff who hold `clients.view`.
- Console Complaints: Export register. `POST /staff/complaints.csv` (complaints.view) writes every complaint as CSV and records `complaints.register_exported` in the audit trail. It leaves out the client's own words and the resolution note.
- Console Rates: Last bill auction tile. The Bank of Uganda's results the website already shows now live in the shared `packages/market` (`@finvest/market`), updated by hand after each auction. Bills carry the published before-tax yield for staff, and the tile shows it with the auction date. The website still shows only the after-tax fields.
- Console Rates: a Last bought tile beside it. `GET /staff/dealing/last-buys` (rates.view) gives the yearly yield, before tax, of Finvest's own latest settled purchase for each product, and the tile shows the newest one.
- Console Finance: the withholding tax payees file was already there, as "Download for URA (CSV)" on each month (`GET /staff/tax/months/{month}/payees.csv`).

## Still owed

- Console Finance: a Tax return file, and whether the payees file matches URA's own format. Both need URA's specification and the owner.
