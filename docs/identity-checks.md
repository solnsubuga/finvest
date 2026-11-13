# Identity checks

How a client proves who they are before they can pay in. The screens are on the [design canvas](design.md): choose a document, scan the front and back, confirm the details, a guided selfie, then a status screen. The back office has an identity checks review screen.

The code is `backend/app/modules/kyc` and migration `0050_kyc`.

## What a client hands in

| Document | Photos | Checked by |
| --- | --- | --- |
| National ID | Front, back, selfie | The provider, then a person if it is unsure |
| Passport | Photo page, selfie | The provider, then a person if it is unsure |
| Driving permit | Front, back, selfie | The provider, then a person if it is unsure |
| Refugee ID | Front, back, selfie | Always a person |

The client also confirms the details read from the document: surname, given names, document number, date of birth and expiry date, and a tax number (TIN) if they have one. A client must be 18 or older and the document must not have expired.

Photos are JPEG, PNG or WebP, at most 10 MB each. The files live in private object storage under `kyc/<check>/<photo>`. The database holds only the key, size and SHA-256 of each one. A photo that is taken again replaces the earlier one on screen, and the earlier row is kept as evidence.

## Lifecycle

Each client has one check. It moves through these states.

| State | Meaning | Who moves it on |
| --- | --- | --- |
| `draft` | The client is adding photos and details | The client, by submitting |
| `submitted` | Automatic checks are running at the provider | The worker, when the provider answers |
| `needs_review` | A person must decide | Staff |
| `with_compliance` | The reviewer escalated it | Staff |
| `awaiting_client` | Staff asked for named photos again, with a message | The client, by retaking those photos and submitting |
| `approved` | Final. The account number is issued. | Nobody |
| `rejected` | Final | Nobody |

Staff can approve, reject, ask for photos again or escalate. Every staff decision needs a reason and is written to the audit trail in the same transaction. A client who is asked for a photo retakes only that photo and does not start again.

## Automatic checks

A provider is asked for three things: that the document is genuine and not expired, that the selfie is a live person who matches the document, and sanctions and politically exposed person screening.

- A client is approved automatically only when all three ran and passed.
- Anything else, including a failed check, sends the client to a person with the reasons listed. A provider never rejects a client on its own.
- Finvest itself refuses to approve a second client on an identity document that an approved client already holds. The database enforces this with a unique index.

## Account number

The 10-digit [account number](account-numbers.md) is issued in the transaction that approves the client, by a person or by the automatic checks. If the approval fails, the number is not kept. An approved check always has a number from the registry, and no other check has one. The database enforces both.

## What the database enforces

- The transition table above is repeated in a trigger. A client's transaction cannot approve or reject, and cannot change a check that is with staff. The system's transitions also need the worker's database role, so the API cannot approve by naming itself the system.
- An approved or rejected check never changes.
- A client's transaction sees only that client's check and photos. Staff transactions see every check. Which staff may review is a permission checked in the application.
- Only the client adds or replaces photos, and only while the check is a draft or staff asked for that photo again. A replaced photo stays replaced, so what staff saw during a review cannot be swapped afterwards.
- Nothing is deleted. Identity records are kept for the period the regulator requires.

## Provider

No identity provider is chosen yet. The module talks to an `IdentityProvider` port (`domain/provider.py`) with three calls: submit a check, find a submission by its idempotency key, and read a result. A timeout is treated as uncertain, so the worker asks the provider what it holds before sending again.

| Provider | Status |
| --- | --- |
| `manual` | Built, and the default. It checks nothing, so every client goes to a person. Nobody is approved by a check that did not happen. |
| Development stub | Next pull request, with the provider jobs. For local use and tests only. |
| Smile ID | Candidate. Coverage and pricing for Uganda to confirm. |
| ioTec Verify | Candidate. Decided for phone number verification. Whether it can check identity documents is to confirm with ioTec. |

## Not built yet

- HTTP routes for the client app and the console. They need sign-in, which is not on `main` yet.
- Upload and signed download links for object storage.
- The jobs that send a check to the provider and read the result. They need the job queue.
- Notifications when a check is approved or needs a new photo.
- Checking that the name matches the name registered on the client's mobile money number.
- Company, joint and group accounts. Only personal accounts are issued.
