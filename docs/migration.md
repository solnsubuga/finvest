# Moving existing clients onto the new Finvest

Clients do nothing until they are invited. Staff move the records, and each client then claims their account.

| Step | Who | What happens |
| --- | --- | --- |
| 1. Gather the records | Staff | Export clients and investments from Finvest 1.0. Anything kept by hand, in spreadsheets or on paper, goes into one import spreadsheet with a fixed set of columns. |
| 2. Trial import | Staff | The console loads the data into a test copy and lists every problem: missing phone numbers, duplicate people, investments with no client, totals that do not add up. This is repeated until it is clean. |
| 3. Check the money | Finance | Client totals, interest to date and holdings are compared with the old system and the custodian statement. Every difference is fixed or signed off with a reason. |
| 4. Switch | Staff | The old system is frozen, the final import runs, and each investment is opened in the ledger with its original start date, rate and interest earned so far. Each client gets a new account number, and the old one keeps working as a bank reference. |
| 5. Invite | System | Every client gets an SMS and email with a personal one-time link. |
| 6. Claim | Client | They confirm their phone by code, create a password and a PIN, add a passkey, take a selfie that is matched to the ID on file, and then see their investments and confirm that they are right. |
| 7. Follow up | Staff | The console shows who has claimed, who reported a problem and who has not responded. Unclaimed accounts keep earning and mature as before. Staff can call a client, update a changed phone number after checking identity, and send a fresh link. |

Clients whose identity documents are missing or out of date are asked to redo the ID check during step 6. A single client can also be added by hand in the console, with a second person approving the opening balance.

## The import spreadsheet

One format covers Finvest 1.0 exports and records kept by hand. It is two sheets, each saved as CSV (in Excel, "CSV UTF-8"): one row per client and one row per investment. The rules are in `backend/app/modules/migration/domain/sheet.py`.

- References are compared without regard to case, so `c1` and `C1` are the same client.
- The columns are fixed and can come in any order. A missing, unknown or repeated column refuses the whole sheet.
- Dates are `YYYY-MM-DD` only. A date like 03/04/2026 is refused, because it reads as March in one country and April in another.
- Amounts are plain numbers in the product's currency, with or without thousands commas: `1,000,000` for shillings, `1,500.50` for dollars. Nothing is rounded, and a fraction of a shilling is refused.
- Rates are percentages with at most two decimal places: `12.5` or `12.5%`.
- Every import states one "as of" date, the day the old system's figures were taken. Interest figures in the sheet are up to that day.

### Clients sheet

| Column | Needed | What it holds |
| --- | --- | --- |
| `legacy_client_ref` | Yes | The client's ID in Finvest 1.0, or a reference staff choose for a hand-kept record, such as `PAPER-0012`. Used once. |
| `source` | Yes | `finvest1` or `manual`. |
| `surname`, `given_names` | Yes | As printed on the identity document. |
| `phone` | No | Any usual way of writing it. Stored as `+256772123456`. Without it the client cannot be invited until staff add one. |
| `email` | No | |
| `date_of_birth` | If verified | |
| `id_type` | If verified | `national_id`, `passport`, `driving_permit` or `refugee_id`. |
| `id_number` | If verified | |
| `id_expires_on` | No | An expired document means the client redoes the ID check when they claim. |
| `id_verified` | Yes | `yes` if Finvest 1.0 checked this person's identity document, otherwise `no`. |
| `legacy_account_number` | No | The old account number, kept as a bank reference. |
| `next_of_kin_name`, `next_of_kin_phone` | No | |

### Investments sheet

| Column | Needed | What it holds |
| --- | --- | --- |
| `legacy_investment_ref` | Yes | The investment or order ID in Finvest 1.0, or a chosen reference. Used once. |
| `legacy_client_ref` | Yes | Must match a row in the clients sheet. |
| `product_code` | Yes | `TREA-91`, `TREA-182` or `USD-BOND`. |
| `status` | Yes | `running` for an investment that is paid for and earning. `pending_payment` for an order whose payment was not yet confirmed. |
| `principal` | Yes | |
| `annual_rate_percent` | Running | The yearly rate before tax this investment started with. |
| `tax_percent` | Running | The tax on interest this investment started with. |
| `start_date` | Running | |
| `maturity_date` | Fixed terms | Must be the start date plus the product's term. Blank for the dollar product. |
| `interest_before_tax` | Running | What the old system says was earned up to the "as of" date. |
| `tax_withheld` | No | The tax held on that interest, if the old records say. |

For a `pending_payment` row every column after `principal` stays blank.

Only running investments and pending orders are imported. Investments that were already redeemed stay in Finvest 1.0, which is kept read-only.

## The trial import

A trial saves nothing. It reads both sheets, checks every row and checks the rows against each other, and lists every problem at once with the sheet, the spreadsheet row, the column and a code. The list never contains the cell's value, so it holds no personal data. The rules are in `backend/app/modules/migration/domain/trial.py`.

An **error** must be fixed in the spreadsheet, and the final import is refused while one remains. A **warning** does not block the import: the row comes across, and staff follow it up or the client is asked during the claim.

| Errors | Warnings |
| --- | --- |
| A needed cell is empty, or a date, amount, rate, phone number or email cannot be read | No phone number |
| A reference used twice | Identity not verified in 1.0, document expired, or no expiry date on file |
| Two clients with the same phone number, the same identity document or the same old account number | Two clients sharing an email address, or a name and date of birth |
| An investment whose client is not in the clients sheet | A client with no investments |
| An unknown product, an amount of zero, a rate outside 0 to 50% | An amount below today's minimum for the product |
| A start date after the "as of" date, a maturity date that is not the start date plus the term, tax greater than interest | An investment past its maturity date that was not yet paid out |
| A verified identity with no document type, number or date of birth | A client under 18 |

The checks for the same person entered twice look only at rows that have no error of their own, so a duplicate can first appear on the next trial, after an unrelated cell in one of the two rows is fixed. Run the trial again after every round of fixes.

A row with an error is left out of the trial's totals, together with the investments of a client whose row has one. The totals are the number of clients, verified identities, clients who will redo the ID check, clients with no phone, running investments, pending orders, money invested and interest before tax, per currency. The finance check compares them with the old system.

## The finance check

Finance enters the old system's own totals for the "as of" date: clients, verified identities, running investments, pending orders, and money invested and interest before tax per currency. Each is compared with the trial import, and each line either agrees or differs. The rules are in `backend/app/modules/migration/domain/finance.py`.

Interest is also checked investment by investment. The new system opens every running investment with what its own formula says was earned by the "as of" date, using the investment's original start date, rate and tax. That keeps the daily postings equal to the formula on every later day. Where the old system's interest or tax figure differs from the formula, the investment is listed with both figures. The difference is never posted to the ledger. An investment whose tax rate is not the one its product withholds is listed in the same way.

The switch cannot be scheduled while the import has an error, or while any total or investment that differs has no sign-off. A sign-off names the line or the investment, records the exact figures it accepts, gives a reason, and takes two people: one member of staff proposes it and a different one approves it. It counts only while those figures stand. If the file is exported again and either figure changes, the difference needs a new sign-off and the old one is reported as out of date. A sign-off left over for something that no longer differs also blocks the switch, so a reason written for an earlier file is never carried forward by accident.

### What is kept

Every trial run is kept in `migration_trials`: the "as of" date, a fingerprint of each file, the counts, the totals, the investments that differ and the list of problems. The files themselves and their cell values are not kept, so the record holds no personal data. The newest run is the one the checks read. The old system's totals are kept in `migration_control_totals` each time finance enters them, and the newest entry counts.

A sign-off is two rows. `migration_sign_offs` holds the proposal with the figures read from the newest trial, never figures sent by the browser. `migration_sign_off_decisions` holds the approval or rejection. The database refuses a decision by the person who proposed it, a second decision, and any change or deletion of these rows. The code is in `backend/app/modules/migration/service.py`.

At the switch each investment is opened with one ledger entry: the principal to the client's holding, interest after tax to the client's accrued interest, the tax to the tax payable account, and the three together against a migration opening balances account, which finance clears against the custodian and bank position.

## What Finvest 1.0 was

For reference when writing the import: a Django and Django REST Framework backend with Celery, and a React, Vite and shadcn frontend. The flow was KYC, pick a product, create an order with a bank slip upload or mobile money, an admin verifies the deposit, the order becomes an investment, interest accrues daily, and redemption and transfer need admin approval. Its code is no longer in this repository or its history, so the column names above come from this description and not from the old database. They must be checked against a real export before the first trial.
