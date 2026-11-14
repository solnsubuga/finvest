# Changing how a client signs in

How a signed-in client changes their password, app PIN, mobile number and email address from Sign in and security in the app, and the stronger check each change needs. Signing in itself, recovery and resets are described in [architecture.md](architecture.md) and [client-accounts.md](client-accounts.md).

## Changing how you sign in

Every change needs a passkey or the authenticator app used on this session in the last ten minutes. A texted or emailed code never counts, and neither does the code that proved the number at sign up, because whoever holds the SIM could have it. A log in with a passkey or the app counts from when it happened. A session that logged in with a code confirms again with `POST /client/session/confirmation/passkey` (after `/passkey/options`) or `POST /client/session/confirmation/authenticator`, which record `reverified_at` on the session, as staff do before an approval. A client with only SMS codes sets up a passkey or the app first, or asks support to change their number with the existing two-person change.

This is the owner's choice on a decision card. The design canvas also shows a selfie for a password or number change. That step comes later, in its own change.

A refusal is 403 `confirm_it_is_you`. The app shows Confirm it is you and then sends the same change again by itself.

| Route | Change |
| --- | --- |
| `PUT /client/password` | `{current_password, new_password}`. A wrong current password is 401 `invalid_credentials`, and a weak new one is a field problem on `new_password`. Every other session ends (`revoked_reason = 'password_changed'`). The client is told by SMS and email (`password_changed`). |
| `POST /client/phone-change` | `{phone}`. Texts a code to the new number (`phone_change_code`) and answers `challenge_id`. At most five a day for one client, and three in fifteen minutes for one client or one number. The same number as now is `same_as_now`. |
| `POST /client/phone-change/{challenge_id}/confirmation` | `{code}`. Moves the account to the new number through `identity_change_own_phone`. Every other session ends, codes already sent stop working, and for 24 hours withdrawals go only to accounts the client already uses, as when staff change a number. The old number and the email on file are told (`own_phone_changed`). A number that has an account is 409 `phone_taken`. |
| `PUT /client/email` | `{email}`. Sets the address through `identity_change_own_email`. It is not proved until the link sent to it is opened, so statements and receipts go nowhere meanwhile. The address it replaced is told (`email_changed`). |
| `PUT /client/pin` | Unchanged route. Choosing a new PIN now works after a log in or a confirmation in the last ten minutes. |

## What the database enforces

Migration `0236_client_own_changes`. The API still holds no UPDATE on `identity_clients.phone` or `.email`.

- `identity_change_own_phone` and `identity_change_own_email` are SECURITY DEFINER functions that take the client from the actor settings, so a client changes only their own account, and only while it is active.
- The number moves only to the one on a `phone_change` code that this client spent in the same transaction, so a code cannot be replayed later.
- The trigger from migration 0163 clears `email_verified_at` whenever the address changes.
- A downgrade refuses while number change codes or client re-confirmations exist.

Each change is on the audit trail: `identity.password.changed`, `identity.phone.changed_by_client`, `identity.email.changed` and `identity.session.reconfirmed`.
