# Payouts

How money leaves Finvest, and the rules in [rules.md](rules.md) that guard it. The pure rules are in `backend/app/modules/payouts/domain`. Tables, commands, the provider adapter and the screens arrive in later pull requests, and this document grows with them.

## Payout destinations

A client is paid only to their own mobile money number or bank account. A new destination goes through four steps, in order, and no step can be skipped:

| Step | Who | Status after |
| --- | --- | --- |
| Add it and confirm the SMS code | Client | Awaiting proof |
| Upload proof of ownership | Client | In review |
| Check the proof against the name on the ID | Staff | Approved, or rejected with a reason |
| Wait 24 hours from the approval | Nobody | Payable |

A rejected destination can be proved again with a better document. A removed one is never used again.

### Proof

| Destination | Proof accepted |
| --- | --- |
| Bank account | A statement dated within the last three calendar months, a letter from the bank, or a photo of the bank card showing the name |
| Mobile money number | The name the network holds for the number, read through the payment provider |

### The name lock

The name on the proof is compared with the name on the client's ID. Case, accents, punctuation and the order of the names are ignored.

| Result | Meaning | Can staff approve? |
| --- | --- | --- |
| Exact | The same names | Yes |
| Partial | One has a name the other lacks, and at least two names agree | Yes, if they judge it is the same person |
| Different | Anything else | Never |

The comparison is a hint for the staff member. It never approves a destination by itself.

### When a destination can be paid

- Not before the staff approval plus 24 hours, to the second.
- If the client signed in with an SMS or email code in place of a passkey, or reset their password, in the last 24 hours, only a destination that had already received money before that sign-in can be paid.

## Withdrawal limits

Limits are versioned by effective date. The version in force on the business day of the request applies. The business day is the Kampala calendar day (`app/shared/business_day.py`). No version in force is an error, never "no limit".

| Limit | Value at launch |
| --- | --- |
| Mobile money, one payout | UGX 5,000,000. No version may set it higher. |
| Mobile money, per day | Not set |
| Bank, one payout and per day | Not set |
| Held for staff review above | Not set |

An amount over the mobile money limit is refused with a code that tells the app to offer a bank account instead. Daily limits count every payout of the day that was not cancelled and did not fail. Amounts are never converted: a payout in another currency than the limits is refused.

## The life of a payout

The money leaves the client's holding when the payout is created and sits in a "payout in flight" ledger account for that client until the provider's answer is known.

| Status | Meaning |
| --- | --- |
| Held | Waiting for staff to approve a large withdrawal |
| Queued | Ready for the worker. Nothing has reached the provider. |
| Sent | The provider accepted it and has not said how it ended |
| Uncertain | We do not know whether the provider took it |
| Paid | The provider confirmed the money arrived |
| Failed | The provider confirmed it did not go |
| Cancelled | Stopped before anything was sent |

Each step into a final status carries exactly one ledger movement, posted in the same transaction:

| Into | Movement of the in-flight money |
| --- | --- |
| Paid | To the wallet or bank account it left from |
| Failed | To the client's "payout pending" account, and staff are alerted |
| Cancelled | Back where it came from |

The "payout pending" account is not a wallet. The client cannot spend it, invest from it or see it as a balance. It only holds a failed payout until staff send it again or return it to the investment, and it should be empty within a day.

Three rules are fixed in the transition table and tested for every combination of status and event:

- Only a queued payout may be sent. A send that certainly did not leave stays queued and may be tried again.
- A payout that is sent or uncertain is never sent again. Only reading its status back from the provider moves it.
- If the provider has no record of a payout and cannot answer, two staff members may settle it from the statement as paid or failed. It takes two different people, a reason, and an audit event. While the provider can answer, its answer is the one that counts.
- A finished payout never changes. A payout can be cancelled only while it is held.
