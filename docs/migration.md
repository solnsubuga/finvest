# Moving existing clients onto the new Finvest

Clients do nothing until they are invited. Staff move the records, and each client then claims their account.

| Step | Who | What happens |
| --- | --- | --- |
| 1. Gather the records | Staff | Export clients and investments from Finvest 1.0. Anything kept by hand, in spreadsheets or on paper, goes into one import spreadsheet with a fixed set of columns. |
| 2. Trial import | Staff | The console loads the data into a test copy and lists every problem: missing phone numbers, duplicate people, investments with no client, totals that do not add up. This is repeated until it is clean. |
| 3. Check the money | Finance | Client totals, interest to date and holdings are compared with the old system and the custodian statement. Every difference is fixed or signed off with a reason. |
| 4. Switch | Staff | The old system is frozen, the final import runs, and each investment is opened in the ledger with its original start date, rate and interest earned so far. Each client gets a new account number, and the old one keeps working as a bank reference. |
| 5. Invite | System | Every client gets an SMS and email with a personal one-time link. |
| 6. Claim | Client | They confirm their phone by code, create a password and a PIN, add a passkey, take a selfie and a photo of the front of their ID that staff compare with the ID on file (see identity-checks.md, "Claim checks"), and then see their investments and confirm that they are right. |
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

Every trial run is kept in `migration_trials`: the "as of" date, a fingerprint of each file, the counts, the totals, the investments that differ with the sheet row and currency of each, and the list of problems. The overview shows that row and currency beside each investment, so finance can find it in Finvest 1.0 and read its figures. A trial run before the row was kept shows none. The files themselves and their cell values are not kept, so the record holds no personal data. The newest run is the one the checks read. The old system's totals are kept in `migration_control_totals` each time finance enters them. The newest entry for a date is the one that counts, and only once a different member of staff has confirmed it in `migration_control_totals_confirmations`. Until then no total line is compared and the switch is blocked. So one person can never change what the trial is compared with, which would otherwise make a difference disappear without a sign-off.

A sign-off is two rows. `migration_sign_offs` holds the proposal with the figures read from the newest trial, never figures sent by the browser. `migration_sign_off_decisions` holds the approval or rejection. The database refuses a decision by the person who proposed it, a second decision, a confirmation of totals by the person who entered them, and any change or deletion of these rows. A rejection needs a reason. The overview keeps showing a rejected sign-off, with that reason, until someone proposes it again, so the person who proposed it can see why. Only an approved sign-off clears a difference. The code is in `backend/app/modules/migration/service.py`.

### Routes and who may use them

The console calls these under `/staff/migration`. The code is in `backend/app/modules/migration/api.py`.

| Route | What it does | Permission |
| --- | --- | --- |
| `GET /staff/migration` | The latest trial, the comparison, what differs with its sign-off, and what blocks the switch | `migration.view` |
| `GET /staff/migration/trials?date_from=&date_to=` | Every trial for "as of" days in the range, oldest first, with its counts, file fingerprints and how many totals and investments differ. For following a parallel run day by day | `migration.view` |
| `POST /staff/migration/trials` | Run a trial import from the two sheets and an "as of" date | `migration.import` |
| `PUT /staff/migration/control-totals` | Enter the old system's totals for a date | `migration.sign_off` |
| `POST /staff/migration/control-totals/{id}/confirmation` | Confirm the totals someone else entered | `migration.sign_off` |
| `POST /staff/migration/sign-offs` | Propose a sign-off for one difference, with a reason | `migration.sign_off` |
| `POST /staff/migration/sign-offs/{id}/decision` | Approve or reject a sign-off someone else proposed | `migration.sign_off` |
| `POST /staff/migration/switch` | Propose the switch with the two sheets of the latest trial | `migration.import` |
| `POST /staff/migration/switch/{id}/decision` | Approve or reject the switch someone else proposed. An approval creates every client's account | `migration.sign_off` |

Operations and admins hold `migration.import`. Finance and admins hold `migration.sign_off`, so a sign-off takes two of them. Counts and amounts travel as whole numbers in the smallest unit.

### The switch

The switch is proposed by one member of staff and approved by a different one, the same way as a sign-off.

- **Proposing.** Operations send the two sheets of the latest trial again. They must be the same files byte for byte, checked by fingerprint, and nothing may block the switch. The trial's "as of" day must be over, because interest is opened up to that day. Every client must have a phone number, because the phone is how a client claims the account. A client without one holds the switch until staff add the number to the sheet and run the trial again. Only one proposal waits at a time.
- **Keeping the sheets.** The sheets are kept sealed with the identity data key in `migration_switch_files`, for the approval and for the worker that carries the investments over. The worker removes them when it is done. No other migration table holds anything from a sheet.
- **Approving.** Finance check everything again: the same trial is still the latest, nothing blocks it, and the sheets read the same with today's products and tax rates. The approval then creates every client's account, with no password, in the approver's transaction. It records each Finvest 1.0 reference with its new account in `migration_carried_clients`. Either every client gets an account or none does. A phone number that already has an account in Finvest 2.0 refuses the whole approval, names the clients sheet rows, and keeps nothing. A rejection needs a reason, and the switch can then be proposed again.
- **What the database holds it to.** The decision is by someone other than the proposer. At most one switch is ever approved. A client is carried over only in the transaction that approved the switch, by the person who approved it. Each of these tables is insert-only, except that the worker deletes the sealed sheets.

The overview shows the newest proposal under `switch`, with its state, who decided it and how many accounts it created. Orders still awaiting payment are counted by the trial and never opened: the client places the order again after claiming.

### Carrying each client over

The approval queues one job, `migration.carry_over`, and the worker carries each client over in a transaction of its own, acting as the system (`backend/app/modules/migration/carry_over.py`):

1. **Identity.** When Finvest 1.0 checked a document that has not expired, it is recorded as an approved check with a new account number. A document that expired since the trial, or one that was never checked, means the client does the full ID check when they claim.
2. **Investments.** Each running investment is opened with its original start date, rate and tax, and the interest earned up to the trial's "as of" day, against the opening balances account of its currency. That interest must equal what the finance check showed. If it does not, nothing of the client is kept.
3. **Claim and link.** The client's claim is created and a one-time link is sent by text and, when there is an address, by email. The link points to the client app's `/claim` page with the token after `#`. The message expires with the link, so an invitation is never sent after the link stops working.

A client whose claim exists is done, so a run that stops part way carries on where it was. When any step is refused, for example an identity document that already belongs to a Finvest 2.0 client, nothing of that client is kept. The code is noted in `migration_carry_problems` and the run moves on. The overview lists those clients under `switch.not_carried`, by Finvest 1.0 reference, code and attempt, with where resolving them stands. The sealed sheets stay while any client is noted, since they are the only copy of that client's identity and investments in Finvest 2.0. The worker removes them only once every client is carried over or closed.

Staff resolve a noted client with two people (`backend/app/modules/migration/resolutions.py`, migration `0076_migration_carry_resolutions`). Someone with `migration.import` puts the cause right by hand, then proposes one of two actions with a reason through `POST /staff/migration/switch/{switch_id}/resolutions`. Someone else with `migration.sign_off`, confirmed in the last few minutes, approves or rejects it through `POST /staff/migration/switch/resolutions/{resolution_id}/decision`.

* **Retry.** The approval queues the carry-over again, and the worker tries the client once more with every check. Refused again, the client is noted as the next attempt, so each attempt keeps its own note, and staff may propose again.
* **Close.** The switch no longer carries the client over, and staff look after them by hand. The approval queues the carry-over too, so the sheets go as soon as nobody else is waiting.

A note takes one proposal at a time and at most one approval. A rejection needs a reason and leaves the client noted. The claim link points to `FINVEST_CLIENT_APP_ORIGIN`, or to the one allowed client origin when that is unset. The old account number as a bank reference is not carried over yet.

At the switch each investment is opened with one ledger entry: the principal to the client's holding, interest after tax to the client's accrued interest, the tax to the tax payable account, and the three together against a migration opening balances account, which finance clears against the custodian and bank position.

## Claiming an account

Each migrated client has one claim in `migration_claims`, created by the switch. It holds the client's ID, their Finvest 1.0 reference, whether they redo the full ID check, the step they have reached, and the reminders sent. It never holds a name, phone number or document: those stay with the identity module. The steps, and who may take each, are in `backend/app/modules/migration/domain/claim.py`. A trigger holds the database to the same table, so a step is never skipped or taken by the wrong kind of actor. The service is `backend/app/modules/migration/claims.py`.

| Step | Moved by |
| --- | --- |
| Not invited to invited | The switch, or staff after checking a phone number |
| Invited to signed in | The client, when the link is used and a password set |
| Signed in to identity submitted | The client |
| Identity submitted to identity confirmed, or back to signed in | The identity checks module |
| Identity confirmed to confirmed, or to problem reported | The client |
| Problem reported to identity confirmed | Staff, with a reason |

A link is a 32-byte random token sent once by SMS and email. Only its SHA-256 hash is stored in `migration_claim_links`. A link works once, for 72 hours. A fresh link cancels the one before it, and the database allows one live link per client. A visitor opening a link is not signed in, so the API looks it up through `finvest_migration_claim_link(hash)`, which returns only that link's claim. The visitor is told only that a link no longer works. Staff can see why: unknown, used, cancelled, expired or already claimed. Reminders go weekly to a client who has not used their link, up to eight. After that, staff call the client and can still send a link by hand.

### Claim routes

The routes are in `backend/app/modules/migration/claims_api.py`. The first two need no sign-in: the link is the proof.

| Route | What it does |
| --- | --- |
| `POST /client/claim/start` | Opens the link and texts a code to the phone on file. It changes nothing. |
| `POST /client/claim/complete` | Checks the link again, then the code. It sets the password, makes the account the client's, signs them in and moves the claim to signed in. |
| `GET /client/claim` | The signed-in client's step and which identity check they need. |
| `POST /client/claim/identity` | Hands in the identity step: the claim check's selfie and ID front, or the full ID check, in the same transaction as moving the claim to identity submitted. The photos and details are taken first with the identity checks module's own routes (`/client/claim-check` or `/client/identity-check`). |
| `POST /client/claim/confirm` | The client says the investments shown are right. Final. |
| `POST /client/claim/problem` | The client says something carried over is wrong. |

A bad link, a spent one, an expired one and an account that is already claimed all get the same `not_found`, so a link tells a visitor nothing about anyone else. A link that expires between the two steps is refused before the code is checked. Signing in commits in the identity module's own transaction. If moving the claim fails after that, the client stays signed in and the claim stays at invited. The reminder run, which comes with the switch, will skip a client whose account is no longer unclaimed. The identity checks module publishes each decision (`kyc.claim_check_decided`, `kyc.check_decided`), and the worker moves the claim on (`backend/app/modules/migration/jobs.py`): a match confirms who the client is, anything else sends them back to signed in. A decision for anyone whose claim is not waiting for it changes nothing. A claim check that does not match is final in the identity checks module, so staff follow that client up by hand for now (see the Open list in decisions.md).

### In the client app

The link opens `apps/client/src/app/claim.tsx`, which reads the token after `#`, wipes it from the address bar, and asks for the texted code and a new password on one screen, as the server checks them together. A link that no longer works says so and offers a call back. Signed in, the client is led by `/moving` (`apps/client/src/app/moving`), and Home shows a card for it until the investments are confirmed, in place of the ID check card. The selfie step takes the ID front and a selfie with `/client/claim-check`, then hands them in with `POST /client/claim/identity`. A client who takes the full ID check uses the usual ID check screens, and the last step there hands the check in through `POST /client/claim/identity` instead of the identity check's own submit, so the claim moves with it (`apps/client/src/claim/handInIdCheck.ts`). Photos staff ask for again are sent with `POST /client/claim-check/submit`, since the claim is already waiting. A claim check that does not match tells the client we will contact them.

## What Finvest 1.0 was

For reference when writing the import: a Django and Django REST Framework backend with Celery, and a React, Vite and shadcn frontend. The flow was KYC, pick a product, create an order with a bank slip upload or mobile money, an admin verifies the deposit, the order becomes an investment, interest accrues daily, and redemption and transfer need admin approval. Its code is no longer in this repository or its history, so the column names above come from this description and not from the old database. They must be checked against a real export before the first trial.
