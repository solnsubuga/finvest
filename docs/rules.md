# Rules for money, security and compliance

These rules are not preferences. A change that breaks one is wrong even if it works.

## Money

- No floating point anywhere. Amounts are integers in the smallest currency unit: whole shillings for UGX, cents for USD.
- Only the ledger moves money. Every movement is a journal entry with at least two lines that sum to zero.
- Journal entries are never edited or deleted. A mistake is fixed with a reversing entry. The database enforces this: application roles have insert only on ledger tables.
- Balances can never go negative. The database enforces it, not just the code.
- Every request that moves money carries an idempotency key.
- Provider calls never happen inside a database transaction. The intent is saved as a job in the same transaction and a worker sends it.
- An uncertain provider outcome goes to reconciliation. It is never retried blindly. A provider callback is a hint, and the status is read back from the provider before the ledger changes.
- Interest is principal times rate times days over 365, posted once a day as ledger entries, so the client total always ties to the ledger.
- Tax is calculated and held in its own ledger account. Clients see net figures only. Tax rates are never shown in the client app.
- Every change to rates, fees and limits has an effective date, a proposer and an approver.
- There is no wallet. No spendable client balance exists. The only place client money can sit is a "payout pending" ledger account after a failed payout, and it should be cleared within a day.
- Overpayments and unmatched deposits are refunded or matched by staff. They are never kept as credit.

## Security

- Client sign-in is never a texted code alone. A new device or the web needs the mobile number and password, then a second step. The second step is any one of the methods the client has set up: a passkey, an authenticator app, or a code sent by SMS or email. The strongest one is offered first and the client can choose another.
- On a phone that is already signed in, the app PIN or fingerprint opens the app and approves payments. The PIN never signs in a new device.
- Passwords are at least 10 characters, checked against leaked password lists, and stored with Argon2id.
- Passkeys use WebAuthn, so the server keeps only a public key.
- Signing in with a code sent by SMS or email limits withdrawals to known accounts for 24 hours. So does any password reset. A passkey or an authenticator app does not, because neither depends on the SIM card.
- Risky actions need extra confirmation: a new payout destination, a large withdrawal, a new device.
- A new payout destination must be in the client's name, is confirmed by SMS code, needs proof of ownership checked by staff, and waits 24 hours before its first payout.
- Every staff account must have a second step, with no exceptions for any role. Staff sign in with a password and then a passkey or a code from an authenticator app. Each member of staff sets up either or both, and whichever they have set up is accepted. A passkey is not required. An account with neither set up cannot sign in or open the console. There is no SMS or email code option for staff. Permissions are by role, and sensitive actions need two people.
- Personal data and documents are encrypted at rest. Documents are served only through short-lived signed links.
- Secrets live in a secrets manager, never in the repository. Terraform state, kept in a private bucket, is the one recorded exception.
- The audit trail records who did what and why, and cannot be edited.
- Logs carry request IDs and error codes, never personal data or account details.
- An independent penetration test happens before launch.

## Compliance

- Sanctions and politically exposed person screening and transaction monitoring are built into onboarding and payouts, not added later.
- Records are kept for the period the regulator requires.
- Reports for the regulator are generated from the ledger, so they always agree with the books.
- Complaints are kept in a regulated register, separate from general support chat.

## To confirm with counsel

- That money in transit through the trust account is what the current licence already covers.
- Uganda's Data Protection and Privacy Act: registration, consent wording, and conditions on storing personal data outside Uganda. This affects where we host.
- Whether group goals with two approvals count as a collective scheme.
