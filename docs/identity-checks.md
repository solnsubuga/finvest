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

A provider is asked for three things: that the document is genuine and not expired, that the selfie matches the document, and sanctions and politically exposed person screening. A fourth, `liveness`, says the selfie was captured by a liveness check that saw a live person ([Liveness](#liveness)). It comes from the selfie handed in, not from the identity provider, so all four must pass for `automatic_checks_passed`.

- A person approves every client. When all three ran and passed, the check goes to staff flagged `automatic_checks_passed`, so it is quick to confirm.
- Approval with no person involved is built but switched off (`automatic_approval` in `record_provider_result`). It is the owner's decision to turn it on, and it is listed as open in [decisions.md](decisions.md). With it on, a client is approved automatically only when all three ran and passed and the document has not expired since.
- Anything else, including a failed check, sends the client to a person with the reasons listed. A provider never rejects a client on its own.
- The name the network holds for the client's phone number is looked up too and shown to staff ([Phone number name](#phone-number-name)). It is not one of the four checks: a different name never fails the check, but it blocks approval, and approval with no person needs it to match.
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

Handing a check in also asks the screening module for a sanctions and PEP screen in the same transaction, once a screening provider is set up ([screening.md](screening.md)). The staff check carries the client's standing as `screening` (`clear`, `possible_match`, `confirmed_match`, `unavailable`, `pending`, `not_run`, or null if never screened). Anything but `clear` is a reason to look before approving. The decision stays with the one reviewer, and a possible match is decided on the screening case by two people.

`screening_subject(session, sealer, client_id)` gives the screening module who to screen: the names, date of birth, document type and issuing country on a check that has been handed in or approved, and never the ID number. It returns nothing for a draft or a rejected check. While staff have sent a check back for photos, it gives the details as they stand. Nationality is not recorded. The issuing country is the nationality for a passport, but for a national ID it only shows the client lives in Uganda. Only the worker and staff can read it, because a client cannot read another client's check.

`approved_client_ids(session, after=, limit=)` pages through approved clients, carried-over ones included, ordered by client id. It is for the screening module's rescreening sweep, in a worker transaction.

## What the database enforces

- The transition table above is repeated in a trigger. A client's transaction cannot approve or reject, and cannot change a check that is with staff. The system's transitions also need the worker's database role, so the API cannot approve by naming itself the system.
- An approved or rejected check never changes.
- The trigger also names the columns each actor may change. A client writes the document and details of their own draft and nothing else, and hands in a check only with no provider answer on it. Staff move a check from one state to the next and never edit it in place or alter what the client confirmed. The worker writes the provider's reference and answer, never the details.
- A client's transaction sees only that client's check and photos. Staff transactions see every check. Which staff may review is a permission checked in the application.
- Only the client adds or replaces photos, and only while the check is a draft or staff asked for that photo again. A replaced photo stays replaced, so what staff saw during a review cannot be swapped afterwards.
- Nothing is deleted. Identity records are kept for the period the regulator requires.

## Provider

The module talks to an `IdentityProvider` port (`domain/provider.py`) with three calls: submit a check, find a submission by its idempotency key, and read a result. A timeout is treated as uncertain, so the worker asks the provider what it holds before sending again.

| Provider | Status |
| --- | --- |
| `manual` | Built, and the default. It checks nothing, so every client goes to a person. Nobody is approved by a check that did not happen. |
| `iotec` | Built: ioTec Verify, below. Off until `FINVEST_IDENTITY_PROVIDER=iotec` and its credentials are set. |
| `development-stub` | Built, for a developer's own machine. It checks nothing: everyone "passes", except a document number ending in 0, whose selfie comes back unsure. Its answers go to a person, and the settings refuse to combine it with automatic approval, so it can never approve a client. It is refused at start-up where `FINVEST_ENVIRONMENT` is `production`, because staff would see passes for checks nobody ran. |
| Smile ID | Not built. A candidate if ioTec Verify falls short. |

### ioTec Verify

`app/modules/kyc/iotec.py`, against `https://verify-api.iotec.io`. It sends through the ioTec Verify connection shared with the payout name check (`app/infrastructure/iotec_verify.py`), which holds the `Client-Id` and `X-Api-Key` headers, https only, the one retry of a request that never arrived, and how a send is sorted. One submission is up to three ioTec requests, each sent in the background with the submission's key as its `referenceId`:

| Check | ioTec request | Pass | Otherwise |
| --- | --- | --- | --- |
| `document_genuine` | ID verification at NIRA (`/api/idv/verify-person-information`): the NIN, names, date of birth and expiry the client confirmed. Ugandan national IDs only. | NIRA says the details match and the card is valid | `fail` when NIRA says they do not match; `unsure` for anything else |
| `selfie_match` | Face match (`/api/face-match`): the selfie against the front of the document | ioTec calls them identical with a confidence of at least 80% | `unsure`, with the confidence as the score |
| `screening` | AML screening (`/api/aml-screening`): sanctions and politically exposed person lists, by full name and date of birth, allowing one letter's difference | No hits | `fail` on any hit, which staff compare |

- The three request IDs together are the reference (`iotec:idv=…,face=…,aml=…`). The worker reads all three and waits while any is still running.
- A request that certainly never reached ioTec (no connection, 401, 403, 429) is tried once more straight away. A check ioTec refused, still could not be sent, or that failed at ioTec is left out, so it shows as `<check>_not_run` and a person looks. Staff must not approve a client on `screening_not_run`: the screening has to be run first, by sending the check back for a fresh submission. A passport, driving permit or refugee ID has no NIRA lookup, so its `document_genuine` is always not run.
- Once one request is accepted the others are never sent again, so nothing is paid for twice. If none was accepted and one may have arrived, the send is uncertain. ioTec cannot be asked for a request by our key, so `find` holds nothing, and that check goes to a person (`provider_not_received`).
- If nothing was accepted and nothing arrived, the whole submission is tried again; the sweep hands the check to a person after 30 minutes.
- A face match confidence outside 0 to 1 is not understood, so it has no score and is `unsure`. The base URL must be https.
- The worker reads the selfie and front from private storage, with the digest saved for each, before sending anything. A photo it cannot read sends nothing and is tried again.
- The face match compares two photos and does not prove a live person on its own. Liveness is a separate step before hand-in ([Liveness](#liveness)), and its capture is the selfie the face match compares. The settings still refuse ioTec with `FINVEST_IDENTITY_AUTOMATIC_APPROVAL`, because approving with no person involved is the owner's decision ([decisions.md](decisions.md)).
- To confirm against ioTec's sandbox: the exact values of `matchingStatus` and `cardStatus`, which the API reference does not list. Anything not recognised is `unsure`, never a pass.
- Nothing ioTec sends back is logged or kept beyond the verdicts and the face match score, because it carries the client's details.

## Liveness

A face match compares two photos, so a printed photo or a screen held up to the camera can pass it. A liveness check runs the camera on the provider's own page and answers whether a live person was there. The face it captured then becomes the check's selfie, so the face match at hand-in compares the live capture with the document, not a photo the client chose.

How an attempt goes (`app/modules/kyc/liveness.py`, `liveness_jobs.py`, `domain/liveness.py`):

1. The client starts an attempt (`POST /client/identity-check/liveness`) while the selfie is theirs to change: a draft, or a check sent back for a new selfie. Any attempt still waiting is retired, so only the newest counts. The attempt and the job `kyc.start_liveness` are saved together.
2. The worker asks the provider for a session, keyed by the attempt's ID, and saves its page sealed. The attempt is `open`, and `GET /client/identity-check/liveness` gives the client the page's address. Only that client is given it.
3. The client opens the page and does what it asks. The worker reads the attempt every 10 seconds (`kyc.read_liveness`, one job per read) until the provider answers or the attempt is 30 minutes old, when it shows as `expired`.
4. Live: the worker downloads the capture, stores it as `kyc/<check>/<photo>` and, in one transaction, retires the current selfie and adds the capture as the selfie with `source = 'liveness'`. If the check was handed in meanwhile, nothing changes and the file is deleted (`check_moved_on`). Not live: the attempt is `failed`, and the client may try again.

| Status | Meaning |
| --- | --- |
| `starting` | Saved; the worker is asking the provider for a session. |
| `open` | The page is ready for the client. |
| `passed` | A live person; the capture is the selfie (unless `failure_code` is `check_moved_on`). |
| `failed` | Not a live person, or nothing usable came back (`not_live`, `no_capture`, `iotec_ended`, `iotec_unclear`). |
| `not_started` | The provider refused (`iotec_refused`, `iotec_no_page`), or may have opened a session but never confirmed it (`provider_uncertain`). |
| `expired` | Open for 30 minutes, or replaced by a newer attempt. |

- A start that certainly did not arrive is tried again. One that may have arrived is not sent again on a guess: ioTec cannot be asked by our key, so the attempt ends as `not_started` and the client starts a new one, under a new key.
- A client has 10 attempts per check. A live capture counts toward the 30 photos a check may hold.
- A client may still send a camera selfie instead. At hand-in, `liveness` is `pass` for a live selfie, `fail` for a camera selfie after a failed attempt, and not run (`liveness_not_run`) otherwise. Staff see each as a flag, and see which selfie was live.
- The database holds the rules too (migration 0200): only the worker adds a `liveness` photo, only as a selfie and only while the photo trigger of migration 0050 allows the selfie to change; a client sends only `client` photos; an attempt only moves forward, and a client may only retire a waiting one.
- Each attempt's start and its end are audit events (`kyc.liveness.*`, resource `kyc_liveness`).

### ioTec Faces

`app/modules/kyc/iotec_liveness.py`, with the same credentials as ioTec Verify. `POST /api/face-verification` with the attempt's ID as `referenceId` and no email, phone or name: ioTec would email the link to an address, but the app opens the page itself (`verificationUrl`). `GET /api/face-verification/{id}` until `faceVerificationStatus` is `Verified` or `Failed`; a request that ended at ioTec (`Failed`, `Expired`, `Cancelled`) is not live. On `Verified` the capture is `profileImageUrl`.

- The client is only sent to, and the capture only downloaded from, an https address on `iotec.io` or a subdomain. The API key is only sent to the API's own host. The capture's type is read from its bytes, and it may be at most 10 MiB.
- ioTec's callback (`/api/face-verification/callback`) is not used. Reading is cheap and a callback is only a hint.
- To confirm against ioTec's sandbox: that a request with no email still returns `verificationUrl`; that `profileImageUrl` is on an ioTec host and needs no key; whether ioTec Faces can return the client to the app when done; and what ioTec Faces asks of the client (it may also ask for the ID card, which this flow does not use).
- The development stub passes on the first read with a stand-in capture, and its page is a placeholder.

## Phone number name

When a client hands in their check, the worker asks ioTec Verify for the name the mobile network holds for the client's own phone number, the one they sign in with. It is the same lookup that proves a mobile money payout number ([payouts.md](payouts.md#mobile-money-numbers)), with the same adapter and the same name rule (`compare_names`). The code is `app/modules/kyc/phone_names.py` and `phone_name_jobs.py`, the rules `domain/phone_names.py`, and the table `kyc_phone_names` (migration 0370).

- Staff see the result on the check as `phone_name`: `checking`, `matches`, `partly_matches` (the same names with one left out), `different`, `no_name` (the network holds none, or barred the number) or `not_answered`, with the network's name once it gave one, `current_number` (false once the client's number has changed since) and `blocks_approval`. It is `null` when nothing was looked up.
- A different name on the client's current number blocks approval (the owner's decision). Approve is left out of `allowed`, and approving anyway is refused with `conflict` on `phone_name` (`phone_name_different`). Staff may still reject, or ask the client to move to a number registered in their own name (a phone change, made by two staff) and hand the check in again, which looks the new number up.
- Every other result is a hint for the person who decides. It never rejects a client on its own. Approval with no person involved, which is off, needs `matches`: anything else goes to a person flagged `phone_name_not_confirmed`.
- The lookup is asked for in the transaction that hands the check in, with its job, for the number on the client's account at that moment. Only a Ugandan number (+256) is looked up. One that got no answer is asked again at the next hand-in, up to three lookups for each number on one check, as each costs a fee. One that found a name, or none, is not repeated for that number.
- The send is never repeated after it may have arrived, and a send that certainly never arrived is tried again by its job, as for payout numbers. Only the worker records what ioTec said, and an answer is final. Nothing logs the number or the name.
- It runs with the payout name lookup's setting: `FINVEST_PAYOUT_NAME_PROVIDER=iotec` with the ioTec Verify credentials, for both the API and the worker. With the default, `none`, nothing is looked up.

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

Settings, all optional while the provider is `manual`: `FINVEST_IDENTITY_PROVIDER` (`manual`, `iotec` or `development-stub`), `FINVEST_IDENTITY_AUTOMATIC_APPROVAL` (off; the owner's decision), and `FINVEST_IDENTITY_DATA_KEY`, which any provider other than `manual` requires in the worker. `iotec` also needs `FINVEST_IOTEC_VERIFY_CLIENT_ID` and `FINVEST_IOTEC_VERIFY_API_KEY` (from the ioTec Verify portal, Management, Settings, Configuration). The API and the worker both read these settings and refuse to start without them: the API records which provider a submission goes to, and only the worker sends. The worker also needs the `FINVEST_OBJECT_STORAGE_*` settings, which the API already has, to read the photos. `FINVEST_IOTEC_VERIFY_BASE_URL` points it elsewhere, for a sandbox.

## Routes

The client's own check, under `/client/identity-check`, with the client's session:

| Route | What it does |
| --- | --- |
| `GET` | The check, or `null` before a document is chosen. Nothing about providers, flags or who decided. |
| `PUT /document` | Start the check, or change the document while it is a draft. |
| `PUT /photos/{kind}` | Send one photo (`front`, `back` or `selfie`) as `multipart/form-data`, field `photo`: JPEG, PNG or WebP, at most 10 MiB. It replaces the photo of that kind. |
| `PUT /details` | Save the details the client confirmed. |
| `POST /submit` | Hand the check in. Says which photos or details are missing if it is not ready. |
| `GET /liveness` | Whether the selfie can be taken with a liveness check (`available`), why a new attempt cannot start now (`problem`), the attempts left and the latest attempt, with its page while it is open. |
| `POST /liveness` | Start a liveness attempt. `unavailable` when no liveness provider is set. |

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

- The device's own camera or file chooser takes each photo, and the client looks at it before it is sent. Where liveness is available, the selfie step starts a liveness check instead, opens the provider's page, and waits for the answer; a camera selfie stays as a fallback.
- The client types the details. Nothing reads them from the photo yet.
- During a retake the API does not say which photos were sent since staff asked, so the app remembers it for the session, and handing in confirms it.
- In a demo build (`EXPO_PUBLIC_DEMO_AUTH=1`) an in-memory stand-in answers, and the last digit of the document number stands in for staff: 1 asks for a new selfie, 2 rejects, 3 approves. The demo offers the live selfie: its page is a placeholder, it answers on the third read, and a number ending in 4 is not a live person.
- The live selfie (`src/identity/LiveSelfie.tsx`) starts an attempt, offers the page once it is open, and reads the attempt every 3 seconds until it ends, or when the client taps "I have finished". The page opens in the browser, because ioTec Faces cannot yet be shown inside the app. A failed or unfinished check offers another try, and "Take a photo instead" falls back to the camera. The console marks a live selfie "Taken live" and names the liveness flags.

## Not built yet

- The console calling these routes. It still uses sample data.
- Replacing the identity data key. Do not rotate it yet: a sealed value records which key sealed it and an old key can still be read, but nothing seals the rows again, and the document fingerprints are made with the current key only, so the one-document rule would stop matching earlier clients.
- Company, joint and group accounts. Only personal accounts are issued.
