# Changing how a client signs in

How a signed-in client changes their password, app PIN, mobile number and email address from Sign in and security in the app, and the stronger check each change needs. Signing in itself, recovery and resets are described in [architecture.md](architecture.md) and [client-accounts.md](client-accounts.md).

## Changing how you sign in

Every change needs a passkey or the authenticator app used on this session in the last ten minutes. A texted or emailed code never counts, and neither does the code that proved the number at sign up, because whoever holds the SIM could have it. A log in with a passkey or the app counts from when it happened. A session that logged in with a code confirms again with `POST /client/session/confirmation/passkey` (after `/passkey/options`) or `POST /client/session/confirmation/authenticator`, which record `reverified_at` on the session, as staff do before an approval. A client with only SMS codes sets up a passkey or the app first, or asks support to change their number with the existing two-person change.

This is the owner's choice on a decision card. A password or number change also needs a live selfie on the same session in the last ten minutes, as the design canvas shows ("Passkey + selfie"), when the client has a face on file: the selfie of their approved identity check, or of the claim staff matched for a client carried over at the switch. A client with no face on file, or with liveness off, changes with the passkey or app alone. An email change needs the passkey or app only. See [identity-checks.md](identity-checks.md#face-confirmations).

A refusal is 403 `confirm_it_is_you` (passkey or app first) or 403 `confirm_with_selfie`. The app shows Confirm it is you, takes the selfie when asked, and then sends the same change again by itself. So it can ask before sending, `GET /client/session` gives `confirmed_at` and `confirmed_until` (when the passkey or app was last used on this session, and until when that counts), and `GET /client/session/confirmation/selfie` gives `needed`, `confirmed`, `attempts_left` and the latest attempt.

| Route | Selfie |
| --- | --- |
| `GET /client/session/confirmation/selfie` | Whether this session needs a selfie for a password or number change, whether it has one, why a new one cannot start (`no_selfie_on_file`, `too_many_attempts`), and the latest attempt. |
| `POST /client/session/confirmation/selfie` | Starts an attempt (201). Read it until it is `open`, open its `url`, then read it until it ends. 422 with `no_selfie_on_file` or `too_many_attempts` (5 a day). 503 when liveness is off. |
| `GET /client/session/confirmation/selfie/{id}` | One of the client's own attempts: `status`, `url` while open, `failure_code`, `expires_at`, and `counts_until` once passed. |

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

## Logging in with a passkey alone

The owner asked for it on 2026-11-15: the log in screen has "Log in with a passkey", which signs a client in with no number or password. A passkey is held only by the client's device and the device checks their fingerprint, face or screen lock before it signs, so it counts as both steps, as a password then a passkey would. The session it opens is a passkey session: it counts as confirming it is you for ten minutes, and it limits nothing for 24 hours.

| Route | |
| --- | --- |
| `POST /client/login/passkey-only/options` | No body. Answers `attempt_id` and `public_key`, options that list no passkeys, so the device offers any Finvest passkey it holds. The challenge is stored with no person (purpose `passkey_login`) for five minutes. 30 in fifteen minutes from one caller address. 503 `unavailable` when client passkeys are off. |
| `POST /client/login/passkey-only/verify` | `{attempt_id, credential}`. Spends the challenge once, finds the passkey by its credential ID, checks the device's `userHandle` names the same client, checks the signature and that the device verified the person, and opens the session: a cookie for a browser, a token for the phone app. A passkey that is unknown, removed, or belongs to a closed, locked or frozen account is 401 `passkey_failed`, all alike. A spent, crossed or late challenge is 410 `expired`. |

Passkeys added before this change were asked to be discoverable ("resident") where the device can, which every phone and computer passkey is. A security key that saved a passkey it cannot list still works as the second step after the password. Staff never log in this way: they always type their email and password first.

## Passkeys in the phone apps

The Android and iPhone apps add passkeys and log in with them through `react-native-passkey`, which asks the phone's own passkeys (Credential Manager on Android, AuthenticationServices on iPhone) with the same JSON options and answers the web uses. A passkey made in a browser on finvestug.com, and synced by Google or Apple, works in the app too, and the other way round.

A phone lets an app use passkeys for finvestug.com only when finvestug.com vouches for the app, so three things must all be in place before an app build offers passkeys:

| What | Where | Value |
| --- | --- | --- |
| The app's ids | `apps/client/app.json` | `com.finvestug.app` on both, and `webcredentials:finvestug.com` as the iPhone app's associated domain. |
| The website's association files | `https://finvestug.com/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json`, written by the site build (`apps/site/src/lib/app-association.ts`) | The site is built with `PUBLIC_APPLE_TEAM_ID` (Apple team ID) and `PUBLIC_ANDROID_CERT_SHA256` (the SHA-256 fingerprint of the Android app signing certificate, comma separated for more than one). Without them each file lists no app. |
| The API's accepted origins | `FINVEST_CLIENT_PASSKEY_APP_ORIGINS` | `https://finvestug.com` for the iPhone app, and `android:apk-key-hash:<the same fingerprint as unpadded base64url>` for the Android app. |

Then the phone build is made with `EXPO_PUBLIC_NATIVE_PASSKEYS=1`. Without it, or on a phone too old for passkeys (before Android 9 or iOS 16), the app offers no passkey and keeps the authenticator app and the SMS code, as before. Closing the phone's prompt, or a phone with no Finvest passkey, counts as nothing used.
