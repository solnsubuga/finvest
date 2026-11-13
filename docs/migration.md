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

## What Finvest 1.0 was

For reference when writing the import: a Django and Django REST Framework backend with Celery, and a React, Vite and shadcn frontend. The flow was KYC, pick a product, create an order with a bank slip upload or mobile money, an admin verifies the deposit, the order becomes an investment, interest accrues daily, and redemption and transfer need admin approval. Its code is no longer in this repository.
