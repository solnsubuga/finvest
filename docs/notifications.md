# Notifications

How Finvest tells a client that something happened: in the app's notification centre, and by push, SMS and email. The screens are on the [design canvas](design.md): Notifications in the client app, and "Messages to clients" under Settings in the back office.

The code is `backend/app/modules/notifications` and migration `0090_notification_inbox`.

**Not built yet: nothing is sent by push, SMS or email.** Today a notification appears in the app's notification centre only. Do not rely on `notify` to text or email anyone until the sending work has merged and this paragraph is gone.

**Who owns which words.** This module's catalogue (`domain/wording.py`) holds every notice to a client, and staff may change it with a second person's approval. `domain/templates.py` holds only the messages that carry a code or a set-up link (sign-in and signup codes, password reset, the staff set-up link). Those are fixed in code, so no change in Settings can turn them into a phishing message. Neither adds text of the other kind.

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
| `investment_started` | Investments | `amount`, `product` | Investments |
| `investment_maturing` | Investments | `product`, `date` | Investments |
| `investment_matured` | Investments | `amount`, `product` | Investments |
| `interest_earned` | Investments | `amount`, `count` | Daily earnings |
| `kyc_approved` | Identity checks | none | Account |
| `kyc_retake_requested` | Identity checks | none | Account |
| `kyc_rejected` | Identity checks | none | Account |
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

SMS is on by default only for money and security, because each text is paid for.

## Email delivery events

Resend reports what happened to each email (sent, delayed, delivered, failed, bounced, complained). Its webhooks reach us through the shared router at hooks.fidiba.com, which routes each event by the tag every Resend email carries: `fidiba_environment`, set from `FINVEST_EMAIL_FIDIBA_ENVIRONMENT` on the worker (`app_finvest_prod` in production). Without the variable, mail still sends but its events are not routed back.

The router posts each event, unchanged, to `POST /webhooks/resend` on the API (`https://api.finvestug.com/webhooks/resend` in production):

1. The route checks the Svix signature (`svix-id`, `svix-timestamp`, `svix-signature`) with the hook secret from `RESEND_HOOK_SECRET`, the router's `whsec_...` secret, not the one in Resend's dashboard. A missing or wrong signature, or one more than five minutes old, gets 401. With no secret set, the route answers 404.
2. It saves the event in `notification_email_events` under its `svix-id` and queues `notifications.email_event`, in one transaction, then answers 204. A retry of the same event saves nothing and queues nothing. Types we do not act on (opens, clicks, contact and domain events) get 204 and are dropped. A signed body we cannot read gets 422.
3. The worker sets `delivery` and `delivery_at` on the message with that Resend id. Delivery only moves forward: a late `email.sent` never undoes `email.delivered`. If no message has that id yet, because the send's result is not saved yet, the job retries for ten minutes and then keeps the event as unmatched.
4. A bounce Resend does not call `Transient`, and any spam complaint, adds the address to `notification_email_suppressions`. From then on `notifications.send` does not email that address: the message ends `failed` with `failure_code = 'suppressed'`.

Only facts are kept. Each address is stored as the sha256 of its lower-cased form, never in the clear, and the subject and the rest of the body are dropped on arrival. The API can record an event and cannot read one back. Nothing lifts a suppression yet; when an address needs it, that is a console action still to build.

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

An announcement carries the words staff wrote when sending it, so it has no wording to edit. Sending announcements is not built yet. When it is, an announcement's words are held to the same link rule and need two people, like a change of wording.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `GET /client/notifications` | Client | A page, newest first, with the unread count. `before` and `limit` page through it. |
| `POST /client/notifications/read` | Client | Marks the named notifications read, or all of them when `ids` is left out |
| `GET /client/notification-preferences` | Client | Channels for each kind of alert |
| `PUT /client/notification-preferences` | Client | Changes the kinds of alert named and leaves the rest |
| `GET /staff/notification-wordings` | Staff with `settings.view` | Every message, its words, a preview with sample values, and any change waiting |
| `POST /staff/notification-wordings/{event}/proposals` | Staff with `wordings.change` | Proposes new words, with a reason |
| `POST /staff/notification-wordings/proposals/{id}/decision` | Staff with `wordings.change` | `approved` or `rejected` by someone else, `withdrawn` by the proposer |
| `POST /webhooks/resend` | The hooks.fidiba.com router, by Svix signature | One Resend delivery event. See "Email delivery events". |
