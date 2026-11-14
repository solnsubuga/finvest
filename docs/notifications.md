# Notifications

How Finvest tells a client that something happened: in the app's notification centre, and by push, SMS and email. The screens are on the [design canvas](design.md): Notifications in the client app, and "Messages to clients" under Settings in the back office.

The code is `backend/app/modules/notifications` and migration `0090_notification_inbox`.

**Not built yet: nothing is sent by push, SMS or email.** Today a notification appears in the app's notification centre only. Do not rely on `notify` to text or email anyone until the sending work has merged and this paragraph is gone.

**Who owns which words.** This module's catalogue (`domain/wording.py`) holds every notice to a client, and staff may change it with a second person's approval. `domain/templates.py` holds only the messages that carry a code or a set-up link (sign-in and signup codes, password reset, the staff set-up link, and the link that invites a Finvest 1.0 client to claim their account). Those are fixed in code, so no change in Settings can turn them into a phishing message. A link in any of them, by email or by text, must point to the console or the client app, or the worker refuses to send it. Neither adds text of the other kind.

**How an email looks.** Every email Finvest sends, from a sign-in code to a withdrawal notice to a monthly statement, is drawn with the parts in `backend/app/shared/email_layout.py`: the black band with the wordmark, a white card with one heading, and the footer that says Finvest never asks for a password, PIN or code. The design is section 9, "Emails", of the [design canvas](design.md). A code sits in a large grey box, a "Not you?" warning in a black box, and a link behind one button, written out below it for a mail app that hides the button. A notice email shows the notification's title and body and an Open Finvest button to the app, where the amount is shown. Emails have no images, so opening one fetches nothing.

## Raising an event from another module

A module tells a client something by calling one function, in the same database transaction as the change it reports:

```python
from app.modules.notifications import contract as notifications

await notifications.notify(
    session,
    event_name="payout_sent",
    client_id=payout.client_id,
    idempotency_key=payout.id,
    variables={"amount": payout.amount, "destination": "MTN Mobile Money"},
    link=notifications.Link("payout", payout.id),
    now=clock.now(),
)
```

- **Same transaction.** The notification commits or rolls back with the change. A client is never told about a payment that was not saved.
- **Once.** `idempotency_key` is unique per client and event. Use the ID of the thing that happened, such as the deposit or payout ID. A key shared by many clients, such as a day or a statement period, still tells each of them. Calling again for the same client, event and key writes nothing and returns `None`, so a retried job is safe.
- **Variables.** Give exactly the variables the event lists, and nothing else. A value is text, a whole number, `Money` or a `date`. Pass `Money`, not a formatted string: amounts are written the same way everywhere, such as `UGX 1,000,000` and `$5.74`. A float is refused.
- **Text values come from Finvest, never from a request.** A text value is taken from a fixed list or from Finvest's own records: a product name, "MTN Mobile Money", "a new phone". Never pass something the caller of an API sent, such as a user agent or a typed name, because it would be written into a Finvest SMS. As a second fence, a link to anywhere but finvestug.com is removed from a text value, braces become brackets, and a long value is cut at 240 characters.
- **It cannot fail your transaction over words.** Whatever the wording and the values, the texts are cut to what the table holds. Only a programming error raises: an unknown event, the wrong set of variables, a float or an empty value.
- **Net figures only.** An amount shown to a client is after tax. No event carries a rate.
- **No amounts in the words.** Push alerts and SMS show on a locked phone, and email sits in an inbox others may read, so no default title, body or SMS names the amount. Push and email send the in-app words, so the notification centre does not name it either. The amount is shown on the screen the notification links to. Events still carry `amount`, so the words can use it if the owner decides otherwise. A unit test holds every default to this.
- **Link.** Optional. It names the screen the app opens when the notification is tapped, by kind and ID: `deposit`, `payout`, `investment`, `identity_check`, `payout_destination`, `statement`, `security`, `conversation`, `complaint`.
- **Who may call.** The transaction must act for that client, for a member of staff, or for the system in the worker. The API is never trusted as the system, so a route with nobody signed in cannot notify.

Nothing personal goes into a job payload or a log. The words live in the `notifications` table, which a row policy fences to the client they belong to.

## Events

| Event | Raised by | Variables | Kind of alert |
| --- | --- | --- | --- |
| `payment_confirmed` | Payments | `amount`, `method` | Money |
| `payment_failed` | Payments | `amount`, `method` | Money |
| `payout_processing` | Payouts | `amount`, `destination` | Money |
| `payout_sent` | Payouts | `amount`, `destination` | Money |
| `payout_failed` | Payouts | `amount`, `destination` | Money |
| `payout_declined` | Payouts | `amount`, `destination` | Money |
| `investment_started` | Investments | `amount`, `product` | Investments |
| `investment_maturing` | Investments | `product`, `date` | Investments |
| `investment_matured` | Investments | `amount`, `product` | Investments |
| `investment_withdrawal_requested` | Investments | `amount`, `product` | Investments |
| `investment_withdrawal_paid` | Investments | `amount`, `product` | Investments |
| `investment_sent` | Investments | `amount`, `product`, `person` | Investments |
| `investment_received` | Investments | `amount`, `product`, `person` | Investments |
| `investment_transfer_held` | Investments | `product`, `person` | Investments |
| `investment_transfer_declined` | Investments | `product`, `person` | Investments |
| `investment_transfer_failed` | Investments | `product`, `person` | Investments |
| `interest_earned` | Investments | `amount`, `count` | Daily earnings |
| `goal_top_up_due` | Goals | `goal`, `date` | Investments |
| `goal_reached` | Goals | `goal` | Investments |
| `kyc_approved` | Identity checks | none | Account |
| `kyc_retake_requested` | Identity checks | none | Account |
| `kyc_rejected` | Identity checks | none | Account |
| `organisation_invited` | Organisations, from the worker | `person` (never the organisation's name, which staff have not seen yet) | Account |
| `organisation_returned` | Organisations | `organisation` | Account |
| `organisation_rejected` | Organisations | `organisation` | Account |
| `organisation_approved` | Organisations | `organisation` | Account |
| `payout_destination_approved` | Payouts | `destination` | Account |
| `payout_destination_refused` | Payouts | `destination` | Account |
| `statement_ready` | Statements | `period` | Account |
| `new_sign_in` | Sign-in | `device` | Security |
| `password_changed` | Sign-in | none | Security |
| `second_step_changed` | Sign-in | `change` | Security |
| `support_reply` | Support | none | Help |
| `complaint_received` | Support | `reference`, `date` | Help |
| `complaint_closed` | Support | `reference` | Help |
| `announcement` | Support | `title`, `message` | News |

`method` and `destination` are a name a client recognises, such as "MTN Mobile Money" or "Stanbic account ending 4821". Never a full account number. `product` is the product's name. `interest_earned` is one notification a day for all of a client's investments, not one per investment.

To add an event, add it to `domain/events.py` with its words in `domain/wording.py` and a row here, in the same pull request as the code that raises it.

## Channels

Every notification is kept in the app. Push, SMS and email are the client's choice for each kind of alert.

| Kind of alert | Default channels |
| --- | --- |
| Money | Push, SMS |
| Investments | Push |
| Daily earnings | None. In the app only. |
| Account | Push, email |
| Security | Push, SMS, email |
| Help | Push |
| News | Push |

Security alerts cannot be silenced. A client can change their channels, but SMS or email must stay on, because the point of a security alert is to reach a client whose phone or app may be in someone else's hands. PostgreSQL enforces this as well as the API.

If a client chose email but not SMS for security alerts, and their email address is suppressed (it bounced for good or reported our mail as spam), or they have no email address, the alert goes by SMS instead, so it still reaches them outside the app. Other kinds of alert do not fall back: they wait in the app.

SMS is on by default only for money and security, because each text is paid for.

## Sending beyond the app

`notify` writes the notification and, in the same transaction, queues one fan-out job that carries the notification's ID and nothing else. The worker does the rest (`delivery.py`):

1. **Fan-out** reads the notification and the client's channels for its kind of alert. If push is chosen and the client has a phone registered, it queues a push job. If SMS or email is chosen, it asks identity where to reach the client (`client_contact`) and queues the text or the email through `queue_message` under the `client_notice` template. Everything it queues commits with the job, so it happens once.
2. **Push** sends the title and body to the client's phones through Expo, with no transaction open. The app gets the notification's ID and its link, so a tap opens the right screen.
3. **SMS and email** are sent by the same jobs as sign-in codes, with the same retries, and their content is cleared once sent.

Rules:

- **Once per notification.** Only a new notification queues a fan-out, and every key is the notification's ID and the channel: `fan-out:<id>`, `push:<id>`, `notice:<id>:sms`, `notice:<id>:email`.
- **A push that may have arrived is not sent again.** Expo has no idempotency key. If the answer is lost, the job stops as uncertain rather than show the client the same alert twice. The notification is in the app either way. SMS and email are not held back by it.
- **Late is not sent.** A push still waiting after 6 hours, or a text or email after 12, is dropped. The app still has it.
- **A shared phone.** When two clients sign in on the same phone, only the one who registered it last is sent pushes. Signing in again hands it back.
- **A phone Expo no longer knows** (the app was removed) is marked `not_registered` and not used again until the app registers it.
- **Nobody is messaged after closing their account.** Identity answers nothing for a client who is locked or closed, so no SMS or email is queued.
- **A statement or receipt is not emailed twice.** The documents module emails `statement_ready` and `receipt_ready` itself, with the PDF attached, so the fan-out sends those by push and in the app only.
- A client has at most 10 phones registered.

### Phones

The app registers its Expo push token after each sign-in with `PUT /client/push-devices` and gives it up at sign-out with `POST /client/push-devices/remove`. The token is sent in the body, so it is never in an address or an access log. A client sees only their own phones, and staff see none. Only the API adds a phone. The worker may only mark one removed.

### Settings

The worker reads `FINVEST_PUSH_BACKEND`: `none` (the default) holds pushes until they expire, `local-console` prints them on a developer's machine and refuses to start anywhere deployed, and `expo` sends them. `FINVEST_PUSH_EXPO_ACCESS_TOKEN` is needed only if the Expo project turns on enhanced push security.

Not built yet: reading Expo's delivery receipts, which report a phone that stopped taking pushes some minutes after the send.

## Email delivery events

Resend reports what happened to each email (sent, delayed, delivered, failed, bounced, complained). Its webhooks reach us through the shared router at hooks.fidiba.com, which routes each event by the tag every Resend email carries: `fidiba_environment`, set from `FINVEST_EMAIL_FIDIBA_ENVIRONMENT` on the worker (`app_finvest_prod` in production). Without the variable, mail still sends but its events are not routed back.

The router posts each event, unchanged, to `POST /webhooks/resend` on the API (`https://api.finvestug.com/webhooks/resend` in production):

1. The route checks the Svix signature (`svix-id`, `svix-timestamp`, `svix-signature`) with the hook secret from `RESEND_HOOK_SECRET`, the router's `whsec_...` secret, not the one in Resend's dashboard. A missing or wrong signature, or one more than five minutes old, gets 401. With no secret set, the route answers 404.
2. It saves the event in `notification_email_events` under its `svix-id` and queues `notifications.email_event`, in one transaction, then answers 204. A retry of the same event saves nothing and queues nothing. Types we do not act on (opens, clicks, contact and domain events) get 204 and are dropped. A signed body we cannot read gets 422.
3. The worker sets `delivery` and `delivery_at` on the message with that Resend id. Delivery only moves forward: a late `email.sent` never undoes `email.delivered`. If no message has that id yet, because the send's result is not saved yet, the job retries for ten minutes and then keeps the event as unmatched.
4. A bounce Resend does not call `Transient`, and any spam complaint, adds the address to `notification_email_suppressions`. From then on `notifications.send` does not email that address: the message ends `failed` with `failure_code = 'suppressed'`. A module that emails a client itself, such as documents, asks `contract.is_suppressed(session, address)` first, from the worker.

Only facts are kept. Each address is stored as the sha256 of its lower-cased form, never in the clear, and the subject and the rest of the body are dropped on arrival. The API can record an event and cannot read one back.

### How addresses are hashed

An address is never stored in the suppression list, its lifts or the delivery events. Each keeps a hash of the lower-cased address, so an address can be matched and never read. A plain sha256 of an address can be undone by hashing a list of likely addresses, so the hash is moving to a keyed one: HMAC-SHA256 under `FINVEST_EMAIL_HASH_KEY`, taken over the plain hash (`domain/email_events.keyed_hash`). Because it is taken over the plain hash, every hash already stored can be keyed without the address it came from, so no suppression is lost on the way. The key is held by the API and the worker only, comes only from the environment, and the API and worker refuse to start in production without it. Changing it would orphan every stored hash.

The move takes three releases, so each deploy runs beside the release before it:

1. **Both kept (migration `0093_email_keyed_hashes`).** `recipients_keyed`, `address_key` on suppressions and `address_key` on lifts sit next to the plain columns. Every new row fills both. Every read still uses the plain columns.
2. **Older rows keyed (migration `0094_email_keyed_backfill`, `keyed_backfill.py`), then keyed read.** The worker job `notifications.key_email_hashes` fills the keyed columns of older rows from the plain ones, 500 rows of each table per transaction, and queues the next page in the same transaction while any are left. The scheduler starts it hourly, at minute 41, and a run with nothing left does nothing. Each update fills only an empty column, and PostgreSQL refuses a change to a keyed hash once set, so a repeated page changes nothing. Rows are taken with `SKIP LOCKED`, so two runs share the work. The job fails with `email_hash_key_missing` if the key is not set, and never logs it. It logs `email_hashes_keyed` with counts only; `left=0` means every row is keyed. The migrator never holds the key. Reads and matches move to the keyed columns in a later release, after a run reports `left=0` in production.
3. **Plain dropped.** A migration that says `zero_downtime` drops the plain columns.

### Lifting a suppression

An address on the suppression list gets no email. When a client whose address bounced has fixed their inbox, staff can send to it again, with two people (`suppression_lifts.py`, migration `0153_email_suppression_lifts`):

| Route | Who | Use |
| --- | --- | --- |
| `GET /staff/email-suppressions` | `clients.view` | The newest 200 suppressions: the address hash, bounced or complained, when, the latest suppressing event, whether it still applies, and any lift waiting. |
| `POST /staff/email-suppressions/lookup` | `clients.view` | Whether one address is suppressed. The address is sent in the body, hashed, and never stored or logged. |
| `POST /staff/email-suppressions/{address_hash}/lifts` | `email_suppressions.lift` | Propose a lift, with a reason. Only for an address that is suppressed now and never complained. |
| `POST /staff/email-suppressions/lifts/{id}/decision` | `email_suppressions.lift` | `approved` or `rejected` by someone else, `withdrawn` by the proposer, with a reason. |

`email_suppressions.lift` is held by admin, operations and support.

- **A complaint is never lifted.** A spam complaint is the person telling their mail provider they do not want our email. An address that ever complained, before or after a lift, is refused with `complained`, by the API and by PostgreSQL. The list shows `complained: true` so the console offers no lift.
- **Resend keeps its own list.** Resend suppresses the address on its side too, so lifting ours does not by itself make Resend deliver: the next email would be dropped there. After approving a lift, the approver removes the address from the suppression list in the Resend dashboard. This is a manual step for now. A later job may call Resend's API for it, from the worker and outside any transaction.
- **Only active staff decide.** PostgreSQL refuses a decision by a member of staff who is no longer active.
- **Nothing is erased.** The suppression row and its events stay. An approved lift outweighs the suppressing events before it.
- **A new bounce suppresses again.** `is_suppressed` finds the address's latest lasting bounce or complaint in `notification_email_events` (a GIN index on `recipients`) and compares it with the latest approved lift. A bounce Resend calls transient never suppresses.
- **Two people.** The database refuses a lift decided by its proposer, and one open lift per address.
- **Hashes only.** Staff transactions may read the suppression list and the events' hashes, delivery, permanence and time. Nothing else about an event, and nothing without a staff actor.

## Words

Each event has a title and a body, used in the app, in a push notification and in an email, and a shorter text for SMS. The built-in words are in `domain/wording.py`.

Staff can change them under Settings in the back office. Seeing the words needs the `settings.view` permission. Changing them needs `wordings.change`, which only the admin role holds for now. A change needs two people: one proposes it with a reason, and a different one approves or rejects it. The proposer can withdraw their own. PostgreSQL enforces that the person who decides is not the person who proposed, and that a decided change is never altered. Every step is written to the audit trail.

An edited wording must pass these rules:

- It uses only the placeholders of its event, written as `{amount}`, and each at most once in the title, once in the body and once in the SMS text.
- The title is at most 60 characters. The body and the SMS text are at most 300.
- It links only to finvestug.com. A link anywhere else is refused, so nobody can turn a Finvest message into a phishing message.
- It contains no percent sign, so it cannot show a rate.
- The SMS text uses only basic letters, digits and punctuation, so one message is not billed as several.

A notification keeps the words it was written with. Changing the words later does not rewrite what a client was already told. Notifications are never deleted.

An announcement carries the words staff wrote when sending it, so it has no wording to edit.

## Announcements

Staff can tell every client something, such as an office closure, as an `announcement` notification in the news category. Each client's channel choices for news apply.

- **Two people.** Sending is a permission of its own, `announcements.send`, apart from changing a wording, so fewer people can tell every client at once. Only the admin role holds it for now. One member of staff with it proposes a title and a message, with a reason. A different one approves or rejects it, and a rejection says why. The proposer can withdraw their own. PostgreSQL enforces that the person who decides is never the person who proposed, that a rejection has a reason, and that a decided announcement never changes. Every step is written to the audit trail.
- **The same word rules as a wording.** The title is at most 60 characters. The message is at most 291 characters, so that "Finvest: " and the message fit one SMS send. Both may link only to finvestug.com, contain no percent sign and no braces, and the message uses only basic letters, digits and punctuation.
- **Sending.** Approving queues the sending in the same transaction. The worker tells active clients 500 at a time, in ID order, with one job per page. Each page commits together with its job and queues the next page. The notification key is the announcement's ID, so a page that runs twice tells nobody twice. Clients who are locked or closed, and accounts carried over from Finvest 1.0 that have not been claimed, are not told.
- **Nothing is deleted.** The list shows the latest 50 announcements with how many clients each has reached.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `GET /client/notifications` | Client | A page, newest first, with the unread count. `before` and `limit` page through it. |
| `POST /client/notifications/read` | Client | Marks the named notifications read, or all of them when `ids` is left out |
| `GET /client/notification-preferences` | Client | Channels for each kind of alert |
| `PUT /client/notification-preferences` | Client | Changes the kinds of alert named and leaves the rest |
| `PUT /client/push-devices` | Client | Sends this phone the client's pushes. `token` is the Expo push token, `platform` is `ios` or `android`. |
| `POST /client/push-devices/remove` | Client | Stops sending to this phone, at sign-out |
| `GET /staff/notification-wordings` | Staff with `settings.view` | Every message, its words, a preview with sample values, and any change waiting |
| `POST /staff/notification-wordings/{event}/proposals` | Staff with `wordings.change` | Proposes new words, with a reason |
| `POST /staff/notification-wordings/proposals/{id}/decision` | Staff with `wordings.change` | `approved` or `rejected` by someone else, `withdrawn` by the proposer |
| `GET /staff/announcements` | Staff with `settings.view` | The latest announcements, any waiting for a second person, and how many clients each has reached |
| `POST /staff/announcements` | Staff with `announcements.send` | Proposes a title and a message, with a reason |
| `POST /staff/announcements/{id}/decision` | Staff with `announcements.send` | `approved`, which starts sending, or `rejected` with a reason, by someone else, or `withdrawn` by the proposer |
| `POST /webhooks/resend` | The hooks.fidiba.com router, by Svix signature | One Resend delivery event. See "Email delivery events". |
