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

Four checks decide. The identity provider answers two: `document_genuine`, that the document is genuine and is the one the client described, and `selfie_match`, that the selfie matches the face on it. `liveness` says the selfie was captured by a liveness check that saw a live person ([Liveness](#liveness)); it comes from the selfie handed in. `screening` is sanctions and politically exposed person screening. All four must pass for `automatic_checks_passed`.

- With the screening module on (`FINVEST_SCREENING_PROVIDER` set, Sigma360 in production), `screening` is the client's standing there, and any screening answer from the identity provider is ignored. Clear passes. A possible or confirmed match fails. A politically exposed person, a screen that could not run, or no screen at all is `unsure`. A screen still running holds the result: the worker reads again (`screening_pending`) until the screen finishes, and a screen that never finishes leaves the check to the sweep, which gives it to a person.
- With the screening module off, `screening` is whatever the identity provider said. Regula does not screen, so with Regula and no screening module every client goes to a person flagged `screening_not_run`.

- A client whose checks all ran and passed is approved with no person involved, by the owner's decision (2026-11-14). This needs the four checks to pass, the network's name for their phone to match, and the document to still be valid. It is on with Regula and ioTec Verify unless `FINVEST_IDENTITY_AUTOMATIC_APPROVAL=false`, which sends every client to staff flagged `automatic_checks_passed` instead. It is never on with the development stub, which checks nothing.
- Anything else, including a failed check, sends the client to a person with the reasons listed. A provider never rejects a client on its own.
- The name the network holds for the client's phone number is looked up too and shown to staff ([Phone number name](#phone-number-name)). It is not one of the four checks: a different name never fails the check, but it blocks approval, and approval with no person needs it to match.
- The expiry date is checked again at approval. Staff cannot approve a document that expired while the check waited.
- Finvest itself refuses to approve a second client on an identity document that an approved client already holds. The database enforces this with a unique index on a keyed fingerprint of the document.

## Account number

The 10-digit [account number](account-numbers.md) is issued in the transaction that approves the client. If the approval fails, the number is not kept. An approved check always has a personal account number (starting with 10) from the registry, and no other check has one. The database enforces this.

## Clients with paper records

Finvest's existing clients were kept on paper, so nothing is carried over as verified. Each of them takes the full ID check when they claim their account, like any new client (the owner's decision, 2026-11-14; see [migration.md](migration.md)). The table still has the `source`, `legacy_reference` and claim check columns and tables from migrations 0052 and 0053, which are never rewritten. Nothing writes them now, and the code and routes that did were removed.

## Personal details are sealed

The document number, date of birth and TIN are encrypted before they are saved (`app/infrastructure/sealing.py`, AES-256-GCM). The database, its backups and anyone reading a table see only sealed bytes.

- Each sealed value is tied to its check and column, so a value copied to another row does not open.
- "One approved client per identity document" works without the number: the table holds an HMAC of country, document type and number, and the unique index is on that.
- Surname, given names and the expiry date are not sealed. Staff find clients by name, payouts compare the verified name, and expiry is checked at approval.
- The key is `FINVEST_IDENTITY_DATA_KEY`: 32 random bytes in base64url, one per environment, given to the API and the worker only. Generate it with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and keep it in the secrets store with a backup. Losing the key loses the sealed details. Changing it makes saved checks unreadable, so rotation needs a planned re-seal, which is not built.
- Migration `0051` refuses to run if details were already saved in plain columns, and refuses to downgrade once sealed details exist. Neither can be converted without the key.

`verified_name(session, client_id)` gives other modules the name on an approved client's document, and nothing before approval.

## How a name is shown

The check keeps the surname and given names exactly as printed on the document, usually in capitals ("NAKATO", "AMARA JOY"). That is the KYC record: screening, the phone name check and the client's data export use it as it is, and payouts compare it in a way that forgives case and order. Everywhere a person reads the name it is shown given names first, in proper case: "Amara Joy Nakato" (`app/modules/kyc/domain/names.py`). Only a word written all in capitals or all in small letters is re-cased, so "McDonald" stays as written.

- `verified_name`, `identities` and `AccountHolder.name` give the shown name: the console's client pages, closures, organisations, screening and payouts, statements, receipts, the tax certificate and reassignments.
- `GET /client/session` carries `display_name` ("Amara Nakato") and `first_name` ("Amara") once the check is approved, null before (`kyc.own_name`). The app greets the client by them on Home, in the wide sidebar and on the PIN screen.
- The staff review screen shows the raw `surname` and `given_names` beside the shown `name`.

Handing a check in also asks the screening module for a sanctions and PEP screen in the same transaction, once a screening provider is set up ([screening.md](screening.md)). The staff check carries the client's standing as `screening` (`clear`, `possible_match`, `confirmed_match`, `unavailable`, `pending`, `not_run`, or null if never screened). Anything but `clear` is a reason to look before approving. The decision stays with the one reviewer, and a possible match is decided on the screening case by two people.

`screening_subject(session, sealer, client_id)` gives the screening module who to screen: the names, date of birth, document type and issuing country on a check that has been handed in or approved, and never the ID number. It returns nothing for a draft or a rejected check. While staff have sent a check back for photos, it gives the details as they stand. Nationality is not recorded. The issuing country is the nationality for a passport, but for a national ID it only shows the client lives in Uganda. Only the worker and staff can read it, because a client cannot read another client's check.

`approved_client_ids(session, after=, limit=)` pages through approved clients, ordered by client id. It is for the screening module's rescreening sweep, in a worker transaction.

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
| `regula` | Built: Regula's cloud, below (the owner's choice, 2026-11-14); it can move onto Finvest's own servers by changing two addresses. The owner's choice (2026-11-14). Off until `FINVEST_IDENTITY_PROVIDER=regula` and its addresses are set. |
| `iotec` | Built: ioTec Verify, below. Kept for now, but not to be used: the owner chose Regula, with no ID number lookup at NIRA. |
| `development-stub` | Built, for a developer's own machine. It checks nothing: everyone "passes", except a document number ending in 0, whose selfie comes back unsure. Its answers go to a person, and the settings refuse to combine it with automatic approval, so it can never approve a client. It is refused at start-up where `FINVEST_ENVIRONMENT` is `production`, because staff would see passes for checks nobody ran. |

### Regula

`app/modules/kyc/regula.py`. Regula's Document Reader and Face services run in Regula's cloud, by the owner's choice (2026-11-14), so ID photos and selfies are sent to Regula over https. The same code runs against Regula installed on Finvest's own servers, by pointing the two addresses there. The cloud addresses and how they are signed in to come with Regula's contract: Regula's public `api.regulaforensics.com` and `faceapi.regulaforensics.com` are demo services, and the settings refuse them, with their nightly ones, in production. One submission is two requests, each answered straight away:

| Check | Regula request | Pass | Fail | Otherwise |
| --- | --- | --- | --- | --- |
| `document_genuine` | Document Reader, `POST /api/process`, scenario `FullAuth`, with the front (`page_idx` 0) and back (`page_idx` 1) in white light | Security features, document type and image quality all checked and fine (`detailsOptical.security`, `docType`, `imageQA` all 1), and the document number or personal number Regula read is the number the client typed, ignoring case, spaces and punctuation | A security feature failed (an altered or forged ID), or the document is not one Regula recognises | `unsure`. A document Regula did not finish processing is left out and shows as not run. |
| `selfie_match` | Face, `POST /api/match`: the selfie as a live image (type 3) against the front of the ID as a printed document (type 1) | Similarity of at least 90% | Never | `unsure`, with the similarity rounded down as the score |

- Neither request changes anything at Regula, so sending again is harmless. A timeout, a dropped connection, a 5xx, 429 or 403 (a missing or expired licence) is deferred and the worker tries again. A refusal (400 or 422) of the document request refuses the submission (`regula_refused`), and it goes to a person. A refused face match is left out.
- Both verdicts are kept in the reference the worker saves (`regula:<tag>:doc=pass,face=pass@96`), and the result is read back from it. The reference holds no details. The tag sent to Regula is a hash of the submission's key.
- The addresses are `FINVEST_REGULA_DOCUMENT_READER_URL` and `FINVEST_REGULA_FACE_URL`: Regula's cloud addresses from the contract, or, self-hosted, such as `http://regula-docreader:8080`. The settings take https anywhere, or plain http only to a container name, localhost or a private address: never plain http over the internet. Still to confirm with Regula: how the cloud signs requests in (the Document Reader specification has a licence in the request body, `systemInfo.license`, and no API key header).
- Liveness is Regula's own ([Regula liveness](#regula-liveness)), and its capture is the selfie the face match compares. Face confirmations stay off with Regula until they take a session reported from the app.
- To confirm on a running Regula: the `/api` prefix on both paths, that `similarity` comes back in percent as the specification says (a fraction would always be below 90 and go to a person, never pass), and the text field types for the Ugandan national ID's NIN. Anything not recognised is `unsure`, never a pass.
- Nothing Regula sends back is logged or kept beyond the verdicts and the similarity, because it carries the client's details.

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

### Regula liveness

`app/modules/kyc/regula_liveness.py`, on the Face service. By the owner's choice (2026-11-14, "Regula liveness plus our five poses"), Regula's liveness is the first step of the guided selfie: its capture is the straight-on selfie, and the client then takes the four turns ([Guided selfie](#guided-selfie)). Taking a camera selfie after it replaces the live one, so `liveness` is then not run.

Regula's liveness runs in the app with Regula's own capture and guidance, not on a page. So:

1. The worker asks Regula nothing to start a session. The attempt's ID is the session's tag, and the attempt's page is `regula-liveness:<tag>`. The app sees that address, and runs Regula's liveness with that tag instead of opening a page.
2. When Regula's session ends, the app sends the transaction ID it was given: `POST /client/identity-check/liveness/{attempt}/transaction`, with `transaction_id`. This works only for the client's own open attempt, and only once. Sending the same ID again is harmless, but a different one is a `conflict`, and so is a transaction already used by another attempt. The table is `kyc_liveness_transactions` (migration 0372), and its rows are never changed or deleted.
3. On its next read, the worker gets the result from Regula itself: `GET /api/v2/liveness?transactionId=`. The app's word is never taken. The session must carry the attempt's tag (`regula_wrong_session` otherwise), and its `status` must be 0, confirmed. A 1 is `not_live`, and anything else is `regula_unclear`, never a pass. Regula saves a session a moment after it ends, so a 404 is simply read again.
4. When the session is live, the capture is downloaded from the `portrait` link. It is downloaded only from the Face service's own host, or from a host in `FINVEST_REGULA_CAPTURE_HOSTS` over https. The type is read from its bytes, and it may be at most 10 MiB. Otherwise the attempt fails with `no_capture`, and the client tries again. The capture becomes the selfie as with any liveness provider. A new selfie retires the pose frames, so the worker retires them too.

- Still to confirm against Regula's cloud: whether `portrait` is a link or the image itself, and the host it points to, and how the cloud signs requests in.

In the client app (`apps/client/src/identity/regula/`):

- With the guided selfie, the selfie step starts with a live check when the API offers one and the build knows Regula's Face service (`EXPO_PUBLIC_REGULA_FACE_URL`, https only, the same address as `FINVEST_REGULA_FACE_URL`). Otherwise it is the guided camera selfie, which the client can also choose at any time.
- When the attempt opens with a `regula-liveness:<tag>` page, the app runs Regula's liveness with that tag at once, one session per attempt: Regula's Face SDK on a phone (`@regulaforensics/face-sdk` with `face-core-basic`, a native module, so a development build), and Regula's `<face-liveness>` web component in a browser, served from `/regula` and loaded only on this screen. Either talks to Regula's Face service directly.
- When the session ends with a transaction, the app sends it once and reads `GET /liveness` every few seconds until the attempt passes or fails. Whatever the SDK says about the person is ignored. A transaction that could not be sent is kept on the screen to send again. A session the client closed, or that could not run, is not opened again by itself.
- A passed attempt goes on to the four head turns.
- The web component fetches its WebAssembly worker from `wasm.regulaforensics.com`, so the web app's CSP must allow that host (`script-src`, `worker-src`, `connect-src`) as well as the Face service address (`connect-src`), and keep `'wasm-unsafe-eval'`. The demo build, with no Face service, has a stand-in for Regula's screen.

## Guided selfie

With Regula (`FINVEST_IDENTITY_PROVIDER=regula`), the selfie is the guided one, by the owner's decision (2026-11-14). The client takes the straight-on selfie, then a frame for each of four poses: turn left, turn right, look up, look down. The app checks each frame on the phone and guides the client. The API checks it again before keeping it, because the phone can be wrong or tampered with. The rules are in `domain/poses.py`, the frames in `poses.py`, and the table is `kyc_pose_frames` (migration 0371).

- The API sends each frame to Regula's face quality check (`app/modules/kyc/regula_quality.py`, `POST /api/detect` on the Face service), outside any transaction. It changes nothing at Regula, so a frame that could not be read answers `unavailable` (503) and the client simply sends it again. Nothing is stored until a frame passes.
- A frame passes with exactly one face. The face must be at least a quarter of the image's width, with its middle within a fifth of the image's middle. It must not be blurry, too dark, too bright or glaring, as Regula judges those, and its eyes must be open (when turned away, only both eyes seen closed counts). The angles must be right too: the selfie within 10° of straight on both ways; a turn at least 20° of yaw, a nod at least 12° of pitch, with the other angle within 15°. The two turns go opposite ways, and so do the two nods. Which sign is left depends on the camera, so only opposite signs are required. This is checked at upload against the frame kept for the opposite pose, and again at hand-in.
- A refused frame answers `validation_failed` with one problem per reason, on the field `photo` for the selfie or `frame` for a pose: `no_face`, `several_faces`, `face_too_small`, `not_centred`, `blurry`, `too_dark`, `too_bright`, `glare`, `eyes_closed`, `look_straight`, `turn_more`, `hold_steady`, `wrong_way`, `not_read`. Each is a prompt the app shows before the client takes the frame again.
- Only a characteristic Regula found wanting asks for a retake. One it could not tell about, or does not assess, does not, so a characteristic Regula does not support never blocks every client. The face match and liveness prove who is there; the frames are for staff and for good images.
- Frames follow the selfie. They are taken after it (`selfie_first`), and only while it may change. A new selfie retires them all, so a set is always taken together. A frame retaken for one pose retires the earlier one. Retired frames are kept and nothing is deleted; the database guard holds the same rules. At most 60 frames per check.
- In the client app (`apps/client/src/identity/GuidedCapture.tsx`, route `/id-check/poses`) the selfie and each pose use the live front camera with an oval, an arrow for the pose and a prompt that says what to change. The face is found on the device and never sent: Google ML Kit through VisionCamera on a phone, MediaPipe's face landmarker in the browser (served by the app from `/face`). The picture is taken by itself once the face has been right for 0.7 seconds. The phone asks a little more than the API (a turn of 25°, a nod of 15°, the other angle within 12°, the selfie within 8°, the face a third of the width and within 0.15 of the middle), and checks the second of a pair went the other way from the first as it measured it. Light, glare and blur are read live in the browser only; on a phone the API judges them. A refusal shows the API's code as a retake prompt; a 503 offers to send the same picture again. With no face detector the client takes it with a button, and with no live camera the phone's own camera app takes it. The rules are `src/identity/guidance.ts`.
- `PUT /client/identity-check/poses/{pose}` sends a frame. The client's check has `guided_selfie`, `poses` (the poses kept) and `pose_<pose>` in `missing`. A check is handed in only with all four, and `poses_not_apart` if a pair went the same way. Staff get the frames, with their angles, from the photo links (`poses`).

## Face confirmations

Before a signed-in client changes their own password or mobile number, a passkey or authenticator app proves the device, and a live selfie proves the person ([sign-in.md](sign-in.md#changing-how-you-sign-in)). The selfie runs on the same liveness provider, and the face it captured is compared with the face on file by the same face match the hand-in uses. Only a live person whose face matches confirms the change.

The face on file (`service.approved_selfie`) is the current selfie of the client's approved check. A client with none, or with liveness off, is not asked: the passkey or app alone confirms the change.

How an attempt goes (`app/modules/kyc/face_confirmations.py`, `face_confirmation_jobs.py`, `domain/face_confirmations.py`):

1. The client starts an attempt (`POST /client/session/confirmation/selfie`). It belongs to their identity session. The client's check is locked first, so two starts at once take turns. Any attempt still waiting is retired. A client may start 5 in a day.
2. The worker asks the liveness provider for a page (`kyc.start_face_confirmation`) and saves it sealed. `GET /client/session/confirmation/selfie/{id}` gives the client its address while the attempt is `open`.
3. The worker reads the page every 5 seconds (`kyc.read_face_confirmation`). Live: the capture is stored as `kyc/<check>/face-confirmations/<attempt>` and kept, and the attempt is `matching`.
4. The worker sends the capture and the face on file to the face match (`kyc.match_face_confirmation`), then reads it every 5 seconds (`kyc.read_face_match`). Called identical at 80 or above: `passed`. Anything less: `failed` with `face_not_matched`, never a question for a person. The client tries again, or asks support, who can change a number with two staff.
5. A passed attempt confirms a password or number change on the same session for 10 minutes. An attempt still waiting after 15 minutes is `expired`.

| Status | Meaning |
| --- | --- |
| `starting` | Saved; the worker is asking for a liveness page. |
| `open` | The page is ready for the client. |
| `matching` | A live person; the capture is being compared with the face on file. |
| `passed` | Live and matching. Confirms a change on this session for 10 minutes. |
| `failed` | `not_live`, `no_capture`, `iotec_ended`, `iotec_unclear`, `face_not_matched`, `match_not_run`, `iotec_refused`, or `provider_uncertain` for a face match that may have arrived. |
| `not_started` | The liveness provider refused, or may have opened a page but never confirmed it. |
| `expired` | Waiting for 15 minutes, or replaced by a newer attempt. |

- A start or a face match that may have arrived is never sent again: ioTec cannot be asked by our key, so the attempt ends, and the client starts a new one under a new key. Reads are simply repeated.
- The database holds the rules too (migration 0391): a client starts an attempt only against their own approved check; only the worker records an answer; an attempt only moves forward; a client may only retire their own waiting attempt; a finished attempt never changes; nobody deletes one.
- Each attempt's start and end are audit events (`kyc.face_confirmation.*`, resource `kyc_face_confirmation`). A pass keeps its score; a failure keeps none.
- The face match adapter is `app/modules/kyc/iotec_face_match.py`: `POST /api/face-match` with the capture as `Photo1` and the face on file as `Photo2`, read back from `GET /api/face-match/{id}`. The development stub matches every face, and is refused in production with the rest of the stub.

## Phone number name

When a client hands in their check, the worker asks ioTec Verify for the name the mobile network holds for the client's own phone number, the one they sign in with. It is the same lookup that proves a mobile money payout number ([payouts.md](payouts.md#mobile-money-numbers)), with the same adapter and the same name rule (`compare_names`). The code is `app/modules/kyc/phone_names.py` and `phone_name_jobs.py`, the rules `domain/phone_names.py`, and the table `kyc_phone_names` (migration 0370).

- Staff see the result on the check as `phone_name`: `checking`, `matches`, `partly_matches` (the same names with one left out), `different`, `no_name` (the network holds none, or barred the number) or `not_answered`, with the network's name once it gave one, `current_number` (false once the client's number has changed since) and `blocks_approval`. It is `null` when nothing was looked up.
- A different name on the client's current number blocks approval (the owner's decision). Approve is left out of `allowed`, and approving anyway is refused with `conflict` on `phone_name` (`phone_name_different`). Staff may still reject, or ask the client to move to a number registered in their own name (a phone change, made by two staff) and hand the check in again, which looks the new number up.
- Every other result is a hint for the person who decides. It never rejects a client on its own. Approval with no person involved needs `matches`: anything else goes to a person flagged `phone_name_not_confirmed`.
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

Settings, all optional while the provider is `manual`: `FINVEST_IDENTITY_PROVIDER` (`manual`, `regula`, `iotec` or `development-stub`), `FINVEST_IDENTITY_AUTOMATIC_APPROVAL` (unset: on with `regula` and `iotec`, off otherwise; `false` turns it off; the owner's decision), and `FINVEST_IDENTITY_DATA_KEY`, which any provider other than `manual` requires in the worker. `regula` also needs `FINVEST_REGULA_DOCUMENT_READER_URL` and `FINVEST_REGULA_FACE_URL`. `iotec` also needs `FINVEST_IOTEC_VERIFY_CLIENT_ID` and `FINVEST_IOTEC_VERIFY_API_KEY` (from the ioTec Verify portal, Management, Settings, Configuration). The API and the worker both read these settings and refuse to start without them: the API records which provider a submission goes to, and only the worker sends. The worker also needs the `FINVEST_OBJECT_STORAGE_*` settings, which the API already has, to read the photos. `FINVEST_IOTEC_VERIFY_BASE_URL` points it elsewhere, for a sandbox.

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
| `POST /liveness/{id}/transaction` | Report the session Regula's liveness ran in the app for an open attempt, once ([Regula liveness](#regula-liveness)). |

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
- Trying Regula's liveness on a real phone and against Regula's cloud. The app side is built ([Regula liveness](#regula-liveness)) but has only been run against stand-ins.
- Face confirmations with Regula. They are off until they take a session reported from the app.
- The pose frames in the console. The client app takes the guided selfie ([Guided selfie](#guided-selfie)).
- The Regula containers in `infra/`, with their licence. That is the infrastructure thread's.
- Removing the ioTec Verify identity adapter (NIRA lookup, face match and AML), now that Regula replaces it. ioTec stays for SMS, mobile money and phone names.
