# Client profile: next of kin and the investing questions

What a client tells Finvest about themselves beyond the identity check: who to contact if Finvest cannot reach them, and what kind of investor they are. Both belong to the KYC module (`backend/app/modules/kyc`), which owns client profiles. The screens are Next of kin and About your investing on the [design canvas](design.md).

## Next of kin

A client names up to five people. For each: their full name as on their ID, the relationship (`spouse`, `child`, `parent`, `sibling`, `other_relative`, `friend` or `other`), a mobile number and an ID number, and a share of what the client holds.

- **Shares are whole basis points.** 10,000 is 100%. The shares of everyone named add up to exactly 10,000, so there is never a fraction or a rounding question. A list may be empty, which removes everyone.
- **A save replaces the whole list.** The app sends every person each time. Nobody is named twice (by ID number), and a client cannot name their own mobile number.
- **Sealed.** Name, mobile number and ID number are sealed with the identity data key (`app/infrastructure/sealing.py`), each bound to its row and column. The database and its backups hold no readable copy. Relationship and share are readable. Without `FINVEST_IDENTITY_DATA_KEY` the routes answer `unavailable`.
- **What the database enforces** (migration `0230_kyc_next_of_kin`): a constraint trigger checks at commit that the shares add up to 10,000 or the list is empty, and that the list's own count matches. A save first locks the client's list row, so two saves from two devices run one after the other and never mix. A client reads and changes only their own list; staff read every list and change none; the worker sees nothing.
- **On the record.** `kyc.next_of_kin.saved` records how many people were saved, never who. Staff opening a client's next of kin is recorded as `kyc.next_of_kin.viewed`, and staff see ID numbers masked.

The shares say who Finvest would speak to first and in what proportion the client wishes their holdings to go. They are a record of the client's wishes, not a legal will, and paying anything out to a next of kin is a manual process for compliance with the documents the law requires.

## The investing questions

Four questions, each answer worth points:

| Question | Answers (points) |
| --- | --- |
| `purpose`: what are you investing for? | `emergency_fund` (0), `planned_purchase` (1), `grow_savings` (2), `retirement_or_children` (3) |
| `need_back`: when might you need this money back? | `within_3_months` (0), `3_to_12_months` (1), `1_to_3_years` (2), `over_3_years` (3) |
| `early_need`: if you needed it before the end of the term? | `would_withdraw_early` (0), `would_use_other_savings` (2), `would_wait` (3) |
| `experience`: have you invested before? | `none` (0), `savings_or_sacco` (1), `bills_or_bonds` (2), `shares_or_unit_trusts` (2) |

The total gives a profile, and each profile a longest suitable term:

| Profile | Points | Longest term |
| --- | --- | --- |
| `careful` | 0 to 3, or any total when the money may be needed within 3 months | 182 days |
| `balanced` | 4 to 7 | 364 days |
| `long_term` | 8 to 11 | Any |

There are no wrong answers and the profile refuses nothing: it is used to suggest investments that suit the client. Answers are asked again a year after they were given (`due` in the response). Every set of answers is kept (migration `0231_kyc_risk_answers`), never changed or removed by any role, so the record shows what Finvest knew when it suggested an investment. The answers are about plans, not identity, so they are not sealed. A client gives at most five sets in any 24 hours (`MAX_ANSWERS_A_DAY`); the sixth is refused as `rate_limited`, so a loop in the app cannot grow the record without end. Sets are taken one at a time per client, so two sent at once cannot both pass the cap.

## Routes

| Route | Who | Use |
| --- | --- | --- |
| `GET /client/next-of-kin` | Client | The people named, in order, with `updated_at` (null before the first save). |
| `PUT /client/next-of-kin` | Client | Replace the whole list: `{"people": [{full_name, relationship, phone, id_number, share_bps}]}`. Every problem is reported at once, by `field_path` `["body", "people", index, field]`, or `["body", "people"]` with `shares_must_total_100` or `too_many`. |
| `GET /client/risk-profile` | Client | The question and answer codes, the latest answers with their profile and `longest_term_days`, and `due`. |
| `PUT /client/risk-profile` | Client | Answer every question: `{"answers": {"purpose": "...", ...}}`. |
| `GET /staff/clients/{id}/profile` | `clients.view` | Next of kin with ID numbers masked (null without the data key), and every set of answers, newest first. |

## Freezing the account

A client who loses their phone, or thinks someone else knows their password, freezes their own account from Profile, Freeze my account. It takes effect at once:

- The account moves from `active` to `locked`, so nobody can log in, the client included, and every session the client holds ends (`revoked_reason = 'frozen'`).
- Nothing in the ledger moves. Investments and automatic plans carry on as they would under a staff lock.
- No fresh sign-in is asked for: freezing only protects the account, and someone who has just lost their phone may not have their second step.
- No message is sent, because Finvest sends a locked client no messages ([client-accounts.md](client-accounts.md#locking)).

Unfreezing is never done from the app. The client gets in touch, and staff use the existing two-person unlock: one person checks it is really them and proposes the unlock, a different person approves it ([client-accounts.md](client-accounts.md)). The client page shows the freeze, with the client's reason, above the lock section.

Migration `0235_client_freezes` adds `identity_client_freezes`, one row each time a client froze their account, never changed or removed. The only way in is `identity_freeze_client`, a SECURITY DEFINER function that takes the client from the actor settings, so a client freezes only themselves; the API holds no write on the table. It records the freeze and locks the client in the same transaction, and the status guard on `identity_clients` accepts `active` to `locked` only with a freeze recorded in that transaction. Freezing an account that is already locked, closed or not yet claimed is refused with `conflict`. Each freeze is the audit event `identity.client.frozen`.

| Route | Who | Use |
| --- | --- | --- |
| `POST /client/account-freeze` | Client | `{"reason": "..." or null}`, at most 500 characters. Answers 201 with `frozen_at`. The session that asked has ended by the time the answer arrives. |

## Not built yet

- The investing screens do not yet filter or mark products by profile, and investing does not ask for the questions before a first investment. The client app asks for them from Profile and before the first investment.

## A copy of my data

`GET /client/data-export` gives the signed-in client one JSON file (`finvest-my-data-<date>.json`, format `finvest-client-data/1`) with everything Finvest holds about them that they may see: the account, the identity check and its details (including the ID number), next of kin, every set of answers to the investing questions, investments with after-tax figures, goals, automatic investing plans, payout accounts (masked), payouts, notification channels and the latest closure request. It never holds tax rates, provider answers, review flags or who on staff did what.

It is put together by `backend/app/modules/client_data`, which owns no tables and reads through the owning modules' functions in the client's own transaction. It needs a sign-in from the last 10 minutes and the identity data key, is sent with `Cache-Control: no-store`, and each download is recorded as `client_data.exported`. The file is named for the day in Kampala. Investments and payouts are capped at the most recent 100 each, and `truncated` says `true` for a list that reached the cap, so the client knows to ask support for older ones.
