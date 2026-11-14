# Screening and compliance cases

Sanctions and politically exposed person (PEP) screening, transaction monitoring, and the compliance cases staff close with two people. The code is `backend/app/modules/screening`, the tables are migrations `0180_screening` to `0182_transaction_monitoring`, and the console reads it on the Compliance screen.

## In short

- Every client is screened when they hand in their identity check, again every 30 days once approved, and before a payout unless they were screened clear in the last 24 hours. The call at hand-in lands with the identity checks thread's next PR; until then a newly approved client's first screen comes from the nightly sweep.
- The provider is behind a port, so it can be swapped. The owner chose Sigma360. Until its account exists, no provider is set up and every screen is recorded as **not run**; the Compliance screen says screening is off.
- A screen that finds a listed entity that may be the client opens a **case**. Two members of staff close it: one proposes the outcome, a different one approves it.
- An entity already on a case for the client (still open, ruled out, accepted as a PEP, or confirmed as sanctioned) is recognised the next time and opens no new case.
- Every confirmed deposit and every payout is checked against monitoring rules, and a rule that fires opens a case the same way.
- Clients never see any of this. Telling a client they matched a list could tip them off.

## The provider port

`domain/provider.py`. A provider is asked about one person at a time and answers with the listed entities it thinks may be them.

| What we send | From |
| --- | --- |
| Surname and given names | The client's handed-in or approved identity check (`kyc.service.screening_subject`) |
| Date of birth | The same. It is sealed in the identity check and opened only in the worker. |
| Country that issued the document | The same. For a passport it is the nationality; for a Ugandan national ID it says only that the client lives in Uganda. There is no nationality field. |

The ID number is never sent.

What comes back is a list of matches. Each has the provider's own ID for the listed entity (stable across screens), the listed name, a category (`sanctions`, `pep`, `adverse_media`, `other`), a strength as a whole percentage, and the lists it was found in. Which lists are searched and how close a name must be to count is the provider's configuration (a Sigma360 "filter set"), not ours.

A provider that does not answer raises `ProviderUnavailable` and the job asks again later. Screening only reads lists, so asking twice is harmless and the job is not an external effect. A provider that refuses a subject raises `ProviderRefused` and the screen is recorded as unavailable with its code.

| Setting | Values |
| --- | --- |
| `FINVEST_SCREENING_PROVIDER` | `none` (default): nothing is screened and each screen is recorded as not run. `development-stub`: for a developer's own machine; surname `SANCTIONED` matches a sanctions list and `POLITICIAN` a PEP list. Refused in production. The Sigma360 adapter adds `sigma360`. Set it on the API and the worker alike: the API reads it to decide whether a payout waits for a screen. |

A provider needs `FINVEST_IDENTITY_DATA_KEY`, because the date of birth is sealed with it. The worker refuses to start without it.

## A screen

`screening_runs`. A screen is asked for with `contract.request_screening(session, client_id=, reason=, idempotency_key=, now=)` in the caller's transaction. It saves a pending run and a job together. The same key asks once.

| Reason | Who asks | Key |
| --- | --- | --- |
| `onboarding` | The identity check, when the client hands it in | Chosen by the identity check |
| `rescreen` | The daily sweep | `rescreen:<client>:<day>` |
| `payout` | Payouts, before money leaves | Chosen by payouts |
| `staff` | A member of staff, from the Compliance screen | `staff:<staff>:<Idempotency-Key>` |

The worker (`jobs.py`, `screening.screen_client`) reads who to screen, calls the provider with no transaction open, and records one of:

| Status | Meaning |
| --- | --- |
| `clear` | Nothing found |
| `possible_match` | At least one listed entity found. A case opens if any of them is new for this client. |
| `unavailable` | The provider refused (`failure_code` says why), or never answered within 6 hours (`provider_no_answer`) |
| `not_run` | No provider is set up |
| `no_subject` | The client has no handed-in identity check to screen |

### The daily sweep

`screening.rescreen_due` runs at 02:15 Kampala time. It gives up on screens waiting more than 6 hours, then pages through approved clients (`kyc.service.approved_client_ids`, which includes clients carried over from Finvest 1.0) and asks for a screen of each one whose last finished screen is 30 days old or more, or who was never screened. A client already waiting for a screen is skipped. It asks for at most 5,000 screens a run, so a first run over every client is spread over days.

30 days is decided for now. Lists change daily, so it is the floor rather than the aim: Sigma360's ongoing monitoring, which alerts when a listed entity changes, can replace or supplement it once the account exists.

## Where a client stands

`contract.latest_outcome(session, client_id)` answers with an outcome, when they were last screened, the case it concerns and whether they are an accepted PEP:

| Outcome | When |
| --- | --- |
| `confirmed_match` | Two members of staff closed a case as `sanctioned`. It outweighs every later screen. |
| `possible_match` | A screening case is open |
| `clear` | The last screen found nothing, or found only entities already decided for this client |
| `pending`, `unavailable`, `not_run` | The last screen is waiting, failed, or could not be run |

None means the client was never screened. The identity check review shows anything other than `clear` as a flag.

## Before a payout

Migration `0181_payout_screening`. When a payout is created, payouts asks `contract.payout_clearance(session, client_id, now)`:

| Clearance | When | What the payout does |
| --- | --- | --- |
| `clear` | A screen finished in the last 24 hours found nothing new, and no case is open | Goes on as before: queued, or held for staff review if it is large |
| `screen` | No screen in the last 24 hours | Starts `screening`, and a screen is asked for with the key `payout:<payout>` |
| `wait` | A screening case is open, a screen is under way, or a screen moments ago could not run (provider refused, or no identity check) | Starts `screening` |
| `blocked` | Two members of staff confirmed a sanctions match | Starts `screening` and stays there |

A payout in `screening` has its money reserved and nothing sent. The client app shows it as processing, never as being screened, so a client is not tipped off.

Whenever a client's standing may have changed (a screen finished, a case closed), screening publishes `screening.standing_changed`, and payouts' consumer `payouts.release_screened` moves the client's waiting payouts on once they are clear: to the queue, or to staff review if the payout is above the review amount in force that day. A payout still waiting can be found on the Compliance screen (`GET /staff/compliance/payouts`), and staff may decline it from the review queue's decline route, which returns the money to where it came from, as for a held payout. A person never releases one: only a clear screen does.

Nothing else asks again for a screen that could not run (the provider refused, or never answered), so the worker's hourly pass `payouts.retry_screening` tries `release_screened` for every client with a payout waiting. Within a day of the failed screen the client still waits; after that the pass asks for a fresh screen, and the payout moves on once it comes back clear. A payout still waiting after 24 hours is logged as `payout_waiting_for_screening` with its ID, and staff see it on the Compliance screen with how long it has waited.

A client whose identity check has no date of birth to send, such as one carried over from Finvest 1.0 without it, is recorded `no_subject` and their payouts wait once a provider is on. Staff then complete the client's identity details and screen them again from the Compliance screen.

With no screening provider set up there is no screen to wait for, so `screen` counts as `clear`. An open case or a confirmed sanctions match still holds the payout. Decided for now.

What the database enforces: `screening_payout_clearance(client, at)` holds the rule. A payout is never created held or queued for a client who is blocked, has an open screening case or is being screened. Only the worker moves a payout out of `screening` to queued or held, and only under the same condition. Staff may move it only to cancelled, in their own name. A client may ask the function only about themselves.

## Transaction monitoring

Migration `0182_transaction_monitoring`. The rules are `domain/monitoring.py`: pure, whole numbers only, and in Uganda shillings. Dollar transactions are recorded but no rule reads them, while the dollar product is paused.

`monitoring_transactions` is screening's own record of each confirmed deposit and each payout asked for: the client, the amount, and when. The rules read a client's earlier ones from it, so screening never reads another module's tables for history.

| What | How it arrives | When it counts from |
| --- | --- | --- |
| A deposit | Screening consumes `payments.deposit_confirmed` (`screening.monitor_deposit`) and reads the deposit from payments | When it was confirmed |
| A payout | Payouts calls `contract.watch_payout` when a payout is created, which queues `screening.monitor_payout`; the worker reads the payout from payouts | When it was asked for |

Each job carries only an ID. A transaction is recorded once, whatever the job does twice, and one client's transactions are recorded one at a time, so two deposits confirmed together see each other.

| Rule | Code | Fires when | Raised once per |
| --- | --- | --- | --- |
| Large deposit | `large_deposit` | One deposit of UGX 20,000,000 or more | Deposit |
| Split deposits | `split_deposits` | Deposits each under UGX 20,000,000 that together reach it within 7 days. Raised by the deposit that crosses it. | Client and week |
| Many deposits | `many_deposits` | The 5th deposit within 24 hours | Client and day |
| Unusual deposit | `unusual_deposit` | A deposit of UGX 5,000,000 or more that is at least 5 times the client's median deposit over the last 180 days, once they have 3 earlier deposits in that time | Deposit |
| Quick withdrawal | `quick_withdrawal` | A payout of at least half of what came in during the last 7 days, when that was UGX 5,000,000 or more | Payout |

The owner kept all thresholds as built on 2026-11-14 ([decisions.md](decisions.md)), and counsel reviews them later. UGX 20,000,000 follows the Financial Intelligence Authority's large cash transaction threshold, which that review confirms. The median, not the mean, is the usual deposit, so one big deposit does not hide the next.

A rule that fires opens a `monitoring` case with the rule as its code and what the rule saw (transaction IDs and figures) as its detail. Staff close it with two people as `no_concern` or `report_to_fia`. A monitoring case does not hold payouts: a large payout already goes to staff review, and holding money on a rule's guess would tip a client off as surely as telling them.

Known limits: a deposit moved to the right client by a reassignment stays in the first client's record too, and a deposit recorded out of order (confirmed earlier but processed later) is not counted by the one recorded before it.

## Cases

`compliance_cases`. Only the worker opens a case: a screen with new matches, or a transaction monitoring rule. A case is closed by two people, from the owner's standing maker and checker rule:

1. A member of staff with `compliance_cases.change` proposes an outcome with a reason.
2. A different member of staff approves it, with their second step from the last few minutes, and the case closes with that outcome in the same transaction. Or they send it back with a note, and the case stays open for a new proposal.
3. The proposer may withdraw their own proposal before anyone decides it.

| Source | Outcomes |
| --- | --- |
| Screening | `not_a_match`: the listed entity is someone else. `pep_accepted`: the client is a PEP (or family or close associate) and stays a client, with the enhanced due diligence done written in the reason. `sanctioned`: the client is on a sanctions list. Payouts stay held and a report is owed to the Financial Intelligence Authority. |
| Monitoring | `no_concern`: looked into and nothing to report. `report_to_fia`: a suspicious transaction report is owed to the Financial Intelligence Authority. |

A case tells later screens that its entities are already dealt with for this client, whether it is still open or closed as `not_a_match`, `pep_accepted` or `sanctioned`, so finding them again opens no new case. A `sanctioned` client stays `confirmed_match`.

Filing the report to the Financial Intelligence Authority is not built: staff file it on the FIA's own system and write the reference in the reason.

Waiting proposals appear in the approvals inbox as `compliance_case`.

## What the database enforces

- A screen starts pending, asked for by whoever is signed in: a client only for themselves, staff naming themselves for a screen by hand. Only the worker finishes one, once.
- Only the worker records matches, and nothing changes them.
- Only the worker opens a case, and a screening case only for a screen of the same client with new matches.
- A proposal is made by the active member of staff signed in, for an open case, with an outcome that fits the case. It is decided once. The approver and the sender-back are never the proposer, and only the proposer withdraws.
- A case closes only with a proposal for it approved in the same transaction by the person signed in, with the same outcome. A closed case never changes.
- Nothing is ever deleted or truncated.
- Staff read everything on the API's role; clients read nothing.

## Routes

All under `/staff/compliance`. Reading needs `compliance.view` (Admin, Compliance). Everything else needs `compliance_cases.change` (Admin, Compliance).

| Route | What it does |
| --- | --- |
| `GET /summary` | Whether screening is on, open cases, proposals waiting, screens in the last 30 days, and cases closed since 1 January (Kampala) as `report_to_fia` |
| `GET /rules` | The monitoring thresholds and the rescreening interval, read from `domain/monitoring.py` and `domain/standing.py` so the screen never shows a copy that has gone stale |
| `GET /cases?status=open&source=` | Cases, open ones oldest first. A monitoring case carries `amount`: the payment that raised it, or for split deposits their total |
| `POST /cases.csv` | The register, a POST because each export is recorded: every case newest first, with the client's account number and name, how it closed and when, as a CSV file. Each export is an audit event, `compliance.register_exported`. More than 10,000 cases is `422 too_many_cases`. |
| `GET /cases/{id}` | One case, the screen and its matches, every proposal, where the client stands, and what the reader may do |
| `POST /cases/{id}/proposals` | Propose an outcome. `409 proposal_waiting` or `case_closed`; `422 outcome_does_not_fit_case`. |
| `POST /proposals/{id}/approve` | Close the case. Needs a recent second step. The proposer gets `403`. |
| `POST /proposals/{id}/send-back` | With a note. The proposer gets `403`. |
| `POST /proposals/{id}/withdraw` | The proposer only. |
| `GET /clients/{id}` | A client's standing, screens and cases |
| `POST /clients/{id}/screenings` | Screen the client again now. Takes an `Idempotency-Key`. One person's step: it only reads lists. |
