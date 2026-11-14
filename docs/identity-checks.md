# Identity checks

How a client proves who they are before they can pay in. The screens are on the [design canvas](design.md): choose a document, scan the front and back, confirm the details, a guided selfie, then a status screen. The back office has an identity checks review screen.

The code is `backend/app/modules/kyc` and migrations `0050_kyc` and `0051_kyc_sealed_details`.

## What a client hands in

| Document | Photos | Checked by |
| --- | --- | --- |
| National ID | Front, back, selfie | The provider, then a person |
| Passport | Photo page, selfie | The provider, then a person |
| Driving permit | Front, back, selfie | The provider, then a person |
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

Staff can approve, reject, ask for photos again or escalate. One member of staff decides alone (the owner's decision, an exception to the maker and checker rule). Every staff decision needs a reason and is written to the audit trail in the same transaction. A client who is asked for a photo retakes only that photo and does not start again. Their check lists the photos asked for in `retake_photos` and, of those, the ones already taken again in `retaken`, so the app knows what is left after a restart.

## Automatic checks

A provider is asked for three things: that the document is genuine and not expired, that the selfie is a live person who matches the document, and sanctions and politically exposed person screening.

- A person approves every client. When all three ran and passed, the check goes to staff flagged `automatic_checks_passed`, so it is quick to confirm.
- Approval with no person involved is built but switched off (`automatic_approval` in `record_provider_result`). It is the owner's decision to turn it on, and it is listed as open in [decisions.md](decisions.md). With it on, a client is approved automatically only when all three ran and passed and the document has not expired since.
- Anything else, including a failed check, sends the client to a person with the reasons listed. A provider never rejects a client on its own.
- The expiry date is checked again at approval. Staff cannot approve a document that expired while the check waited.
- Finvest itself refuses to approve a second client on an identity document that an approved client already holds. The database enforces this with a unique index on a keyed fingerprint of the document.

## Account number

The 10-digit [account number](account-numbers.md) is issued in the transaction that approves the client. If the approval fails, the number is not kept. An approved check always has a personal account number (starting with 10) from the registry, and no other check has one. The database enforces this.

## Carried over from Finvest 1.0

At the switch (see [migration.md](migration.md)), a client whose identity Finvest 1.0 checked, on a document that has not expired, is carried over already approved. The migration module calls `record_carried_over_check(session, sealer, SYSTEM, *, client_id, document_type, document_country, details, legacy_reference, reason, clock)` in the worker, in the switch's own transaction. It issues a new personal account number and records `kyc.check.carried_over` in the audit trail with the reason and the Finvest 1.0 reference.

- The check has `source = finvest1` and the client's Finvest 1.0 reference, which is used once. It was never submitted and has no photos, no provider and no automatic checks. Staff see it marked `carried_over`.
- Finvest 1.0 did not always record when a document expires, so the expiry may be unknown. An expired document is refused with `document_expired`: that client does the full check when they claim.
- Calling it again with the same reference for the same client returns the same check and issues nothing. The same reference for another client, a client who already has a check, and a document an approved client already holds are refused.
- The client is not told at the switch. They hear when they claim the account.
- Only the system on the worker's connection may insert such a check, and only approved and with no staff member as the decider. Clients still insert only their own drafts and can never set the source. Like every decided check, it never changes.

## Claim checks

A carried-over check has no photos. So when the client claims their account, they take a selfie and a photo of the front of the identity document on file. One member of staff with `identity_checks.review` compares the two with each other and with the carried-over check, which they open like any other identity check. They then say whether the photos match. The code is `app/modules/kyc/claims.py`, and the tables are `kyc_claim_checks` and `kyc_claim_photos` (migration 0053).

- A claim check starts as a draft and goes to `needs_review` when handed in. From there staff mark it `matched` or `not_matched`, which are final, or send it back to the client (`awaiting_client`) for named photos with a message. Every decision needs a reason and is written to the audit trail.
- Only a client whose own approved check was carried over can start one. The photos are stored and kept exactly as identity photos are, under `kyc-claims/<claim check>/<photo>`.
- The decision is published as `kyc.claim_check_decided`, with the client, the claim check and `matched`, in the decision's own transaction. The migration module moves the claim on from that in the worker, by adding its consumer to `claims.DECIDED_CONSUMERS`. This module never writes the migration tables.
- When staff send photos back, the client gets the same `kyc_retake_requested` notice as for an identity check, once for each hand-in. It carries no link, because notification links have no kind for a claim; the app opens the claim.
- `not_matched` is final, and a client has one claim check. A client refused in error is not reopened here. The migration module moves their claim to staff follow-up, and staff contact the client and check their identity outside the app, as for any other claim problem (step 7 in [migration.md](migration.md)).
- A trigger holds the same transitions and the columns each actor may change. The worker only reads.

| Route | What it does |
| --- | --- |
| `GET /client/claim-check`, `POST /client/claim-check` | The client's claim check, or `null`; start it |
| `PUT /client/claim-check/photos/{front,selfie}` | One photo, multipart field `photo`, the same limits as identity photos |
| `POST /client/claim-check/submit` | Hand it in |
| `GET /staff/claim-checks`, `GET /staff/claim-checks/{id}` | The queue and one claim check (`identity_checks.view`) |
| `POST /staff/claim-checks/{id}/photo-links` | 5-minute links to the photos, audited |
| `POST /staff/claim-checks/{id}/match`, `/no-match`, `/ask-for-photos` | The decisions (`identity_checks.review`; matching needs a fresh confirmation) |

## Personal details are sealed

The document number, date of birth and TIN are encrypted before they are saved (`app/infrastructure/sealing.py`, AES-256-GCM). The database, its backups and anyone reading a table see only sealed bytes.

- Each sealed value is tied to its check and column, so a value copied to another row does not open.
- "One approved client per identity document" works without the number: the table holds an HMAC of country, document type and number, and the unique index is on that.
- Surname, given names and the expiry date are not sealed. Staff find clients by name, payouts compare the verified name, and expiry is checked at approval.
- The key is `FINVEST_IDENTITY_DATA_KEY`: 32 random bytes in base64url, one per environment, given to the API and the worker only. Generate it with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and keep it in the secrets store with a backup. Losing the key loses the sealed details. Changing it makes saved checks unreadable, so rotation needs a planned re-seal, which is not built.
- Migration `0051` refuses to run if details were already saved in plain columns, and refuses to downgrade once sealed details exist. Neither can be converted without the key.

`verified_name(session, client_id)` gives other modules the name on an approved client's document, and nothing before approval.

`screening_subject(session, sealer, client_id)` gives the screening module who to screen: the names, date of birth, document type and issuing country on a check that has been handed in or approved, and never the ID number. It returns nothing for a draft or a rejected check. While staff have sent a check back for photos, it gives the details as they stand. Nationality is not recorded. The issuing country is the nationality for a passport, but for a national ID it only shows the client lives in Uganda. Only the worker and staff can read it, because a client cannot read another client's check.

## What the database enforces

- The transition table above is repeated in a trigger. A client's transaction cannot approve or reject, and cannot change a check that is with staff. The system's transitions also need the worker's database role, so the API cannot approve by naming itself the system.
- An approved or rejected check never changes.
- The trigger also names the columns each actor may change. A client writes the document and details of their own draft and nothing else, and hands in a check only with no provider answer on it. Staff move a check from one state to the next and never edit it in place or alter what the client confirmed. The worker writes the provider's reference and answer, never the details.
- A client's transaction sees only that client's check and photos. Staff transactions see every check. Which staff may review is a permission checked in the application.
- Only the client adds or replaces photos, and only while the check is a draft or staff asked for that photo again. A replaced photo stays replaced, so what staff saw during a review cannot be swapped afterwards.
- Nothing is deleted. Identity records are kept for the period the regulator requires.

## Provider

No identity provider is chosen yet. The module talks to an `IdentityProvider` port (`domain/provider.py`) with three calls: submit a check, find a submission by its idempotency key, and read a result. A timeout is treated as uncertain, so the worker asks the provider what it holds before sending again.

| Provider | Status |
| --- | --- |
| `manual` | Built, and the default. It checks nothing, so every client goes to a person. Nobody is approved by a check that did not happen. |
| `development-stub` | Built, for a developer's own machine. It checks nothing: everyone "passes", except a document number ending in 0, whose selfie comes back unsure. Its answers go to a person, and the settings refuse to combine it with automatic approval, so it can never approve a client. It is refused at start-up where `FINVEST_ENVIRONMENT` is `production`, because staff would see passes for checks nobody ran. |
| Smile ID | Candidate. Coverage and pricing for Uganda to confirm. |
| ioTec Verify | Candidate. Decided for phone number verification. Whether it can check identity documents is to confirm with ioTec. |

## Talking to the provider

The API never calls the provider. Submitting a check saves a job in the same transaction, and the worker does the rest (`app/modules/kyc/jobs.py`).

| Job | What it does |
| --- | --- |
| `kyc.send_check` | Sends one submission, keyed `<check>:<submission>`. Accepted: saves the reference and queues the read. Refused: the check goes to a person (`provider_refused`). |
| `kyc.read_result` | Reads the result and applies it. Not ready yet: asked again with a growing delay, for up to 20 minutes. |
| `kyc.sweep_unanswered` | Every 5 minutes. A check still waiting 30 minutes after it was submitted goes to a person (`provider_no_answer`). |

How a send can end:

- **It did not arrive.** Tried again, for up to 5 minutes.
- **It may have arrived.** Never sent again on a guess. The worker asks the provider what it holds for that key. If the provider holds it, the result is read as usual. If it does not, the check goes to a person (`provider_not_received`).
- **Nobody can tell.** The job stops as an uncertain outcome and is never run again. The sweep gives the check to a person.

A result that arrives after the check moved on changes nothing.

Settings, all optional while the provider is `manual`: `FINVEST_IDENTITY_PROVIDER` (`manual` or `development-stub`), `FINVEST_IDENTITY_AUTOMATIC_APPROVAL` (off; the owner's decision), and `FINVEST_IDENTITY_DATA_KEY`, which any provider other than `manual` requires in the worker.

## Routes

The client's own check, under `/client/identity-check`, with the client's session:

| Route | What it does |
| --- | --- |
| `GET` | The check, or `null` before a document is chosen. Nothing about providers, flags or who decided. |
| `PUT /document` | Start the check, or change the document while it is a draft. |
| `PUT /photos/{kind}` | Send one photo (`front`, `back` or `selfie`) as `multipart/form-data`, field `photo`: JPEG, PNG or WebP, at most 10 MiB. It replaces the photo of that kind. |
| `PUT /details` | Save the details the client confirmed. |
| `POST /submit` | Hand the check in. Says which photos or details are missing if it is not ready. |

The review queue, under `/staff/identity-checks`. Reading needs the permission `identity_checks.view`. Deciding needs `identity_checks.review`. Admin, Operations and Compliance hold both.

| Route | What it does |
| --- | --- |
| `GET` | Checks that are not decided, the ones needing a person first. |
| `GET /{id}` | One check, with the document number masked (`CM90••••••••4PL`) and the decisions allowed in its state. |
| `POST /{id}/document-number` | The whole document number. Each call is an audit event. |
| `POST /{id}/photo-links` | Links to the photos that last 5 minutes. Each call is an audit event. |
| `POST /{id}/approve`, `/reject`, `/ask-for-photos`, `/escalate` | The decisions. Each needs a reason or a message to the client. |

The API stores each photo in the shared private storage (`app.infrastructure.storage`) under `kyc/<check>/<photo>`, never the browser. Everything that can refuse a photo runs first: the check must take a photo of that kind now, the type and size must be allowed, and the file must start the way a file of its type does. Then the file is stored, outside any transaction, and only then is its row saved with its size and SHA-256. If saving the row fails, the file is deleted. Every photo is kept, replaced ones too, so a check holds at most 30 in all: one more is refused with `too_many_photos` before anything is stored. Read links are never stored or logged.

The client is told, in the same transaction as the decision, when their check is approved (by staff or by the worker), rejected, or sent back for new photos: the events `kyc_approved`, `kyc_rejected` and `kyc_retake_requested`, linked to the check. The approval and rejection are keyed by the check, and a request for photos by the check and its submission, so a check sent back twice tells the client twice. Escalating a check, or sending it to a person, tells the client nothing.

In the same transaction, an approval or rejection is published as `kyc.check_decided`, keyed by the check, with the client, the check and `approved`. A module that needs to hear, such as migration for a client claiming an account who redoes the full check, adds its consumer to `service.DECIDED_CONSUMERS`.

Without `FINVEST_IDENTITY_DATA_KEY` the routes answer `unavailable`, and so do the photo routes when file storage is not configured.

## In the client app

`apps/client/src/app/id-check` follows the canvas order: choose the document and country, the front, the back (not for a passport), the details, then the selfie, which hands the check in. Home shows what the check needs next until it is approved, Profile links to it, and the invest, goal, plan and bank transfer screens open it when they are refused for an unapproved client. After approval the screen shows the account number. The client is notified when the check is approved or rejected and when staff ask for photos again, never when it is escalated, and the notification opens the ID check screen.

- The device's own camera or file chooser takes each photo, and the client looks at it before it is sent. There is no live frame detection or liveness guidance yet.
- The client types the details. Nothing reads them from the photo yet.
- During a retake the API does not say which photos were sent since staff asked, so the app remembers it for the session, and handing in confirms it.
- In a demo build (`EXPO_PUBLIC_DEMO_AUTH=1`) an in-memory stand-in answers, and the last digit of the document number stands in for staff: 1 asks for a new selfie, 2 rejects, 3 approves.

## Not built yet

- The console calling these routes. It still uses sample data.
- Replacing the identity data key. Do not rotate it yet: a sealed value records which key sealed it and an old key can still be read, but nothing seals the rows again, and the document fingerprints are made with the current key only, so the one-document rule would stop matching earlier clients.
- Checking that the name matches the name registered on the client's mobile money number.
- Company, joint and group accounts. Only personal accounts are issued.
