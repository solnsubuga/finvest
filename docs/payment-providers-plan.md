# Plan: how Finvest takes mobile money and card payments

**Status: a plan, not built. Paused by the owner on 14 November 2026 ("Let us pause and first plan").** Nothing here is decided until the owner approves it. The owner's latest leaning is mobile money on Yo! Payments and cards on Pesapal, and the owner has asked whether Finvest can connect to MTN and Airtel directly instead. This plan sets out the options, recommends an order, and then describes each piece in detail. Facts about each provider come from public documentation and official code libraries, read on 14 November 2026. Items marked "confirm" must be checked with the provider before anyone relies on them.

## The options

What Finvest needs from a mobile money provider, from [rules.md](rules.md) and how payments work today ([payments.md](payments.md)):

1. A prompt straight to the client's own number from inside the app, so the client pays only from their own number and automatic investing can ask for a payment by itself.
2. A way to read a payment back by our own reference, so an unclear answer is settled by asking, never by sending again.
3. Payouts to mobile money.
4. A Bank of Uganda licence, and a statement each day for reconciliation.

| Option | 1. Prompt to own number | 2. Read back by our reference | 3. Payouts | 4. Licence and statement | Cost | Effort and time |
| --- | --- | --- | --- | --- | --- | --- |
| **ioTec Pay** (built, live) | Yes | By ioTec's ID; by ours when the send's answer was lost | Yes | Says licensed (confirm); statement uploaded | Not published | None: running today |
| **Yo! Payments** | Yes | Yes | Yes (needs an access letter, maybe signed requests) | BoU PSO licence (2022, confirm current); statement by API | Old public rates 3% in, UGX 250 to 1,000 per payout; negotiable | One adapter for both networks. Days to build, then Yo!'s business sign-up |
| **MTN MoMo Open API, direct** | Yes (`RequestToPay`) | Yes: we choose the `X-Reference-Id` | Yes (`Disbursement`), to MTN numbers only | MTN is licensed; statements from MTN's merchant portal (confirm an API) | Lowest: the network's own rate (about 1.5% in, an estimate) | One adapter. MTN's business onboarding before live keys, usually weeks |
| **Airtel Money Open API, direct** | Yes (USSD push) | Yes, by our transaction ID | Yes, to Airtel numbers only; each payout carries a PIN encrypted with Airtel's key | Airtel is licensed; statements from Airtel (confirm) | Lowest, as MTN | One adapter. Airtel's onboarding, usually weeks |
| **Pesapal** | No: a hosted page where the payer types any number | No for a lost send | No payout API | BoU PSO and PSP licences | Not published | Not suitable for mobile money |
| **Pegasus, pawaPay, Flutterwave** | Pegasus and pawaPay yes; Flutterwave adds a redirect in Uganda | pawaPay yes; others unconfirmed | Yes | Pegasus licensed; pawaPay's Uganda licence not found | pawaPay about 1% plus the network's fee | Alternatives to Yo! if its terms are poor |

For cards there is one practical choice today: **Pesapal's hosted page**, which keeps card numbers away from Finvest (PCI DSS SAQ A).

### Going direct with MTN and Airtel

- **What it gives:** the lowest fees, no third party holding the money, and on MTN the best possible read back, since we choose each payment's reference before sending.
- **What it costs:** two contracts and two onboardings, two wallets to keep funded for payouts, two statements in the daily close, two sweeps to the trust account, and Airtel's encrypted payout PIN to keep as a secret. MTN's callbacks are not known to be signed, which is fine because callbacks are only hints here.
- **Code:** the payments and payouts ports already take one adapter per provider, and the first design had separate MTN and Airtel adapters. A network can be switched on its own, so MTN and Airtel can move at different times.

## Recommended order

| Step | What | Why |
| --- | --- | --- |
| 0 | Keep ioTec running as it is | It is built and deployed. Nothing changes for clients until a replacement is live. |
| 1 | Start the sign-ups now: Yo! Payments, MTN MoMo Open API, Airtel Money Open API and Pesapal | Onboarding is the slowest part and runs in parallel with everything else. |
| 2 | Build the Yo! adapter for money in and payouts, and switch mobile money to Yo! | One adapter covers both networks and gets off ioTec quickly. |
| 3 | Build cards on Pesapal | A new way to pay, independent of mobile money. |
| 4 | Move MTN to direct when MTN gives live access, then Airtel when Airtel does | Lower fees. Clients see no change, because the prompt looks the same. |

If the owner prefers to skip Yo!, step 2 becomes "wait for MTN and Airtel", and ioTec stays until both are live. If Yo!'s terms are poor, Pegasus or pawaPay can take its place in step 2 behind the same adapter port.

### What to ask MTN and Airtel

1. The business onboarding list for the Open API's collections and disbursements products in Uganda, and how long it takes.
2. Their rates for money in and payouts, and any monthly fee.
3. Limits per payment and per day for a business wallet.
4. How settlement to the trust account works, how fast, and at what cost.
5. Whether a daily statement can be pulled by API.
6. Whether callbacks can be signed, and from which addresses they come.
7. For Airtel: how the payout PIN and its encryption key are issued and rotated.

## Decisions for the owner

Each is put to the owner on a decision card.

| Decision | Recommended |
| --- | --- |
| Mobile money route | Yo! now, MTN and Airtel direct later, one network at a time |
| Yo! for payouts too | Yes, so money in pays money out from one account |
| Cards | Pesapal's hosted page |
| Who pays the card charge | The client, shown in shillings before paying, if Pesapal allows it |
| Card limits and refunds to the card | As in [Limits for cards](#limits-for-cards), each needing the owner's own yes |

The sections below describe Yo! and Pesapal in detail. A direct MTN or Airtel adapter follows the same steps as Yo!, with the network's own calls.

## In short, if the recommended order is approved

- **Mobile money (MTN and Airtel) goes through Yo! Payments.** The prompt goes straight to the number on the client's account from inside the app, as it does with ioTec today. The client never leaves the app or types a number, so the rule that a client pays only from their own number holds before any money moves.
- **Cards (Visa and Mastercard) go through Pesapal's hosted page.** Card numbers are typed only on Pesapal's page. Finvest's servers and apps never see them, which keeps Finvest in the smallest card security scope (PCI DSS SAQ A, confirm).
- **Bank transfer is unchanged.**
- **ioTec Pay collections are switched off** once Yo! is live. Whether Yo! also takes over mobile money payouts is on a decision card (recommended: yes). SMS and the phone-name check stay on ioTec for now.
- Every payment is still for one investment or one goal saving. There is still no wallet.
- Yo! and Pesapal each hold money until it is moved or settled to the trust bank account, so each is a pot in reconciliation, closed every day.

## Yo! Payments at a glance

| Topic | What Yo! documents |
| --- | --- |
| Transport | One XML document per request, `POST` over HTTPS with `Content-Type: text/xml`. Live `https://paymentsapi1.yo.co.ug/ybs/task.php` (and `paymentsapi2` as the second address), sandbox `https://sandbox.yo.co.ug/services/yopaymentsdev/task.php`. |
| Sign in | `APIUsername` and `APIPassword` inside every request. An address allow-list may apply (error -21). Needs a Yo! Payments Business account. |
| Collecting | `acdepositfunds` with `Account` (`256…`), `Amount`, `Narrative`, `ExternalReference` (our deposit ID), `InstantNotificationUrl`, `FailureNotificationUrl` and `NonBlocking=TRUE`, which answers at once with `PENDING` and Yo!'s `TransactionReference`. The payer gets the PIN prompt on their phone. |
| Paying out | `acwithdrawfunds`, the same shape. Needs an API access letter from Yo!. Where Yo! turns on key signing, each payout carries a one-time nonce and an RSA signature over the request's fields. |
| Reading the result | `actransactioncheckstatus` by Yo!'s reference **or by our own reference** (`PrivateTransactionReference`), so a payment can be found even when the answer to the send was lost. `TransactionStatus` is `SUCCEEDED`, `FAILED`, `PENDING` or `INDETERMINATE` (Yo!'s own team settles it, usually within a day). |
| Notifications | A form post on success (`date_time`, `amount`, `narrative`, `network_ref`, `external_ref`, `msisdn`, `signature`) and one on failure. Each is **signed with RSA and SHA-1**, checked against Yo!'s public certificate. No timestamp or nonce, so a notice can be replayed: still a hint only. |
| Reconciliation | `acacctbalance` gives the balance. `acgetministatement` gives the transactions and charges for a time window, each with our reference, so a daily statement can be pulled by API instead of uploaded. |
| Idempotency | Not documented. Yo! may refuse a likely duplicate (-6, -8), but nothing promises it keys on our reference. So we never send the same deposit twice, as today. |
| Amounts | The libraries send decimals. We send whole shillings. Confirm Yo! takes an integer amount as is. |
| Charges | Old public pages say 3% on money in and UGX 250 to 1,000 per mobile money payout. Expect a negotiated rate. |
| Licence | Bank of Uganda Payment System Operator licence (Large Funds Transfer), January 2022. Confirm it is current on the Bank of Uganda's list. |

## Mobile money through Yo!, step by step

The same steps as today's ioTec deposit ([payments.md](payments.md#a-mobile-money-deposit-step-by-step)). Only the adapter changes, so every rule in [rules.md](rules.md) holds.

1. The client picks MTN or Airtel and taps Pay. `request_deposit` saves the deposit as `created` with the account's own number and a job to send it, under the client's idempotency key.
2. The worker marks it `sending`, commits, and calls `acdepositfunds` with `NonBlocking=TRUE`, our deposit ID as `ExternalReference`, and the notification addresses. No transaction is open during the call.
3. `PENDING` with a reference: the deposit is `awaiting_approval` and the payer approves on their phone. `FAILED` or an error code that certainly means nothing was taken (bad amount -4, unsupported network -2, declined 26): rejected. A timeout, a dropped connection, -1, -22, -31 or anything unreadable: **uncertain, never sent again.**
4. The worker reads the status by our reference. `SUCCEEDED` for exactly the amount asked confirms the deposit and posts the entry. `FAILED` fails it. `PENDING` waits. `INDETERMINATE` holds it `uncertain` while Yo!'s team settles it, and the existing sweep keeps reading for 24 hours, then reconciliation settles it from Yo!'s statement.
5. A notification from Yo! is checked against Yo!'s certificate, recorded once keyed by `external_ref` and status, and only queues a status read, as every callback does today.

Unlike ioTec, Yo! can be asked about a payment by our own reference, so even a send whose answer was lost is read back and settled, never guessed.

### What changes in the code

- `providers/yo.py` implements the existing `CollectionsProvider` port, with a shared client in `app/infrastructure/yo` for payouts to use. XML is built with an XML library (never by joining strings), TLS certificates are always checked (Yo!'s own PHP library turns that off), and amounts are whole shillings.
- `FINVEST_PAYMENTS_PROVIDER=yo`. Settings `FINVEST_YO_API_USERNAME` (variable), `FINVEST_YO_API_PASSWORD` (secret), `FINVEST_YO_URL`, `FINVEST_YO_TIMEOUT_SECONDS`, and, for payouts, `FINVEST_YO_PAYOUT_PRIVATE_KEY` (secret) if Yo! turns on signing. Yo!'s public certificates are committed, since they are public.
- Callbacks at `/provider-callbacks/collections/yo` and `/provider-callbacks/collections/yo/failure`.
- A sandbox mapping like ioTec's: in Yo!'s sandbox the amount chooses the result (2944 fails, 8390 is indeterminate for a payment in).
- Charges are booked as ioTec's are: Finvest's expense, read from the statement's `CHARGES` entries, rounded up to a whole shilling, debit `fees.provider.yo`, credit `yo.wallet`.
- The payment channels setting keeps MTN and Airtel pay in. No client app change is needed for mobile money beyond the provider's name on receipts.
- Automatic investing keeps working, because Yo! prompts the phone without a page.

### Ledger and the daily close for Yo!

| Event | Debit | Credit |
| --- | --- | --- |
| Mobile money payment confirmed | `yo.wallet`, reference `mtn` or `airtel` | `client.awaiting_start`, the client, reference the investment |
| Yo!'s charge | `fees.provider.yo` | `yo.wallet` |
| Payout paid (if payouts move to Yo!) | Payout in flight, as today | `yo.wallet` |
| Sweep to the trust account (two people) | `bank.trust` | `yo.wallet`, any difference to `fees.provider.yo` |

The Yo! account replaces the ioTec wallets in the day close. Its statement is pulled with `acgetministatement` each night (in pages, since Yo! returns a limited number of rows per call) and imported like any other, so staff no longer upload it. Whether Yo! keeps MTN and Airtel money as separate balances decides whether this is one pot or two (confirm).

### Switching from ioTec

1. Build and test the Yo! adapter against Yo!'s sandbox and the fake, behind the existing port.
2. On switch day set `FINVEST_PAYMENTS_PROVIDER=yo`. ioTec deposits already open are carried through by their status reads, which still go to ioTec.
3. Sweep the collected money left in the ioTec wallet to the trust account, by two people. If payouts stay on ioTec, the wallet keeps a payout float and staff top it up from the trust account.
4. Once nothing is open on ioTec collections, remove the ioTec collections adapter.

## Cards through Pesapal

### Pesapal at a glance

| Topic | What Pesapal documents |
| --- | --- |
| Addresses | Sandbox `https://cybqa.pesapal.com/pesapalv3`, live `https://pay.pesapal.com/v3`. JSON over HTTPS. |
| Sign in | `POST /api/Auth/RequestToken` with the consumer key and secret. The token lasts about 5 minutes and is sent as a Bearer token. |
| Taking a payment | `POST /api/Transactions/SubmitOrderRequest` with our reference (`id`, up to 50 characters), `currency`, `amount`, `description`, `callback_url`, `notification_id`, `billing_address` (email or phone). The answer has `order_tracking_id` and `redirect_url`, the hosted page the payer opens. |
| Amounts | `amount` is a decimal number in major units, not an integer in minor units. |
| Currencies | Uganda shillings and US dollars (Pesapal's Uganda page). |
| Notifications (IPN) | Registered once with `POST /api/URLSetup/RegisterIPN`, which returns the `ipn_id` sent as `notification_id`. A notification carries only `OrderTrackingId`, `OrderMerchantReference` and `OrderNotificationType`. **It is not signed**, and Pesapal's addresses may change without notice. We answer with a small JSON body. |
| Reading the result | `GET /api/Transactions/GetTransactionStatus?orderTrackingId=`. `status_code` 0 invalid, 1 completed, 2 failed, 3 reversed. It also gives `amount`, `currency`, `payment_method` (such as "Visa"), `payment_account` (the masked card, such as `476173**0010`) and `confirmation_code`. No pending code is documented. |
| Refunds | `/api/Transactions/RefundRequest` with `confirmation_code`, `amount`, `username`, `remarks`. Completed payments only, one refund per payment, never more than was paid. Cards can be refunded in part. Pesapal then needs the merchant to approve the refund on its side. There is no way to read a refund's status. |
| Cancelling | `/api/Transactions/CancelOrder` with `order_tracking_id`, for an order not yet paid, once. |
| Recurring | Pesapal runs the schedule itself and charges the card on its own dates. There is no charge on demand and no token we can use. |
| Payouts | None in API 3.0. Pesapal's bulk payout product (Openfloat) is run from a dashboard, with no public API. |
| Settlement | The merchant asks for a withdrawal of its Pesapal balance to the bank account set at sign-up, paid within about 48 business hours (Uganda bank partner FAQ). No settlement or statement API is documented. |
| Licences | Bank of Uganda Payment System Operator licence (2022) and Payment Service Provider licence PSP 20/23 (2024). PCI DSS Level 1. 3-D Secure is mentioned for online card payments. |

Fees for Uganda are not published. Ask for a quote. Third parties quote 1.5% to 3.5%, which nobody has confirmed.

### A card payment, step by step

The same shape as a mobile money deposit, so every rule in [rules.md](rules.md) holds.

1. **The client asks to pay by card.** `POST /client/investments/{id}/payments/card` with an `Idempotency-Key`. In the client's own transaction, payments saves the deposit as `created` (method `card`, provider `pesapal`, no payer number) and a job to send it. The request cannot name an amount; it is the investment's. The one live deposit per investment rule covers cards too. The answer is `202` with the deposit.
2. **The worker opens the order.** It marks the deposit `sending` and commits, then calls `SubmitOrderRequest` with no transaction open. Our reference (`id`) is the deposit ID. `billing_address` carries the client's proved email or phone, nothing else. The amount is turned from integer shillings into Pesapal's decimal only inside the adapter. It saves `order_tracking_id` as the provider reference and the `redirect_url`, and the deposit becomes `awaiting_payer` (the existing `awaiting_approval` status, shown to the client as "Waiting for your card payment").
3. **The app opens Pesapal's page.** The app polls `GET /client/payments/{id}` (it already does for mobile money) until the redirect address appears, usually in a second or two, then opens it. The payer types the card and passes 3-D Secure on Pesapal's page.
4. **The payer comes back.** Pesapal sends them to `callback_url`, a page of the client app that shows the payment as it polls. The return carries no result and changes nothing.
5. **The worker reads the result.** An IPN, the app's return, or the sweep queues a status read. Only `GetTransactionStatus` decides: `1` completed, for exactly the amount and currency asked, confirms the deposit and posts the ledger entry in that transaction, then `payments.deposit_confirmed` starts the investment as today. `2` failed fails the deposit with a mapped code (`declined`, `insufficient_funds`, `other`). A different amount holds the deposit `uncertain` with `amount_mismatch` for staff, as today.

#### Why a lost answer is safe here

Pesapal documents no idempotency on `SubmitOrderRequest`, and a status read needs Pesapal's tracking ID. If the answer to the send is lost, we cannot look the order up. That is still safe: no card can be charged until the payer opens the `redirect_url`, and the payer only ever gets it from our saved answer. So a send whose answer was lost becomes `failed` with `provider_unavailable` rather than `uncertain`, and the client simply tries again with a new deposit. Confirm with Pesapal that an order cannot be paid without its redirect address.

#### What stops a card payment getting stuck

The sweep in [payments.md](payments.md#what-stops-a-deposit-getting-stuck) gains these rows for cards:

| Found | Action |
| --- | --- |
| `awaiting_payer` and quiet for 2 minutes | A status read is queued, then every few minutes. |
| `awaiting_payer` for 30 minutes, not completed | The worker sends `CancelOrder` (a job registered as having an external effect, sent once), then reads the status again. Only a read that shows failed, or an accepted cancel followed by a read that is not completed, fails the deposit with `approval_timed_out`. |
| Anything else unclear | `uncertain`, read on the existing back-off for 24 hours, then reconciliation settles it from Pesapal's statement. |

#### Notifications from Pesapal

`POST /provider-callbacks/collections/pesapal` (or `GET`, whichever we register). The IPN is not signed, so the registered address carries a long random token in its path (`FINVEST_PESAPAL_IPN_TOKEN`) to keep out noise, and the notice is treated as a hint only, as every callback is. The route records it once, keyed `<OrderTrackingId>:<OrderNotificationType>`, queues a status read, and answers the JSON Pesapal asks for. It cannot read or change deposits.

### Backend changes for cards

All in `backend/app/modules/payments` unless said otherwise, with a migration range asked of the coordinator at build time.

- **Port.** A `CardCollectionsProvider` in `domain/provider.py`: open an order (returns the tracking ID and redirect address, or rejected, deferred or uncertain), read its status, cancel it, request a refund, and read a notice. Every failure is sorted and never raised, as the mobile money port does.
- **Adapters.** `providers/pesapal.py` (token cached and refreshed before its 5 minutes, shared in `app/infrastructure/pesapal`), and a card sandbox whose amount's last digits choose the result, refused in production like the mobile money sandbox. Tested against a fake that returns success, rejection, timeout and an uncertain outcome.
- **Deposits.** Method `card` and provider `pesapal` added to the check constraints. A card deposit has no payer number. New columns, set once by the worker: the redirect address, the card brand and masked card from the status read (`payment_account`, sealed like other account details), and Pesapal's `confirmation_code`, which a refund needs. The state trigger and its every-pair test gain the card path.
- **Client routes.** `POST /client/investments/{id}/payments/card`, then the existing `GET /client/investments/{id}/payment` and `GET /client/payments/{id}`, which gain `redirect_url` while waiting and `card` (brand and last four) once paid. Goals use the same route once goal payments over HTTP exist.
- **Charge.** Booked as ioTec's is, from what Pesapal reports, rounded up to a whole shilling. If the client pays the charge, it is added to what Pesapal collects and shown before paying (see the choices).
- **Channel switch.** `payment_channels` gains card pay in, paused by two people like the others. A paused card channel answers `503` `channel_paused`.
- **Monitoring and fraud.** Card deposits feed `screening.monitor_deposit` like any other. A masked card seen on another client's paid deposit is flagged to staff, as a reused bank receipt is. Card limits (see below) are checked before the deposit is saved.
- **Settings** (`FINVEST_PESAPAL_*`): `CONSUMER_KEY` and `CONSUMER_SECRET` (secrets), `IPN_ID` (registered once with a command, then a variable), `IPN_TOKEN` (secret), `BASE_URL`, `TIMEOUT_SECONDS`, and `FINVEST_CARD_PROVIDER` = `disabled` (default), `sandbox` or `pesapal`. API and worker need the credentials; the API also needs the IPN token. The infrastructure thread wires them into the deploy.
- **Audit.** `payments.deposit_requested`, `_confirmed` and `_failed` with method `card`, and new events for refunds. Never the card, the amount or the client's details in logs.

#### Limits for cards

Stolen cards are the main risk: a stolen card pays for an investment, and the money is taken out to mobile money before the real cardholder disputes it. Proposed, for the owner to confirm before building:

- At most UGX 5,000,000 per card payment and UGX 10,000,000 per client in any 30 days. Changed by two people on Settings.
- 3-D Secure on every card payment (confirm Pesapal enforces it).
- Money paid by card that is refunded, for any reason, goes back to the same card, never to a payout account.
- An early withdrawal of an investment paid by card within 30 days of the payment is refunded to the card, not paid to mobile money or a bank. Maturity payouts are unchanged.

### Client app changes

In `apps/client/src/payments` and the pay screens.

- **Pick a way to pay** becomes mobile money (MTN or Airtel, prompted in the app as today), card or bank transfer, each shown only when its channel is open and the amount is within its limit. If the client pays the charge, the screen shows it in shillings and the total before they go on. No rate is shown.
- **Paying.** On the web (app.finvestug.com) the app sends the whole page to Pesapal and Pesapal sends it back to `https://app.finvestug.com/pay/card/return?payment=<id>`. On Android and iPhone the app opens Pesapal's page in the system's secure in-app browser (`expo-web-browser`, a new dependency), which closes when the payer reaches the return address. **No iframe.** A full redirect keeps card entry entirely on Pesapal's page, which is what keeps us at SAQ A, and the client app's Content Security Policy needs no frame source for Pesapal.
- **The web app's state is lost on the redirect.** Sending the whole page to Pesapal drops everything held in memory on app.finvestug.com. The return route `/pay/card/return?payment=<id>` rebuilds everything from the API, using only the payment ID in the address: the payment, its investment and what to show next. Nothing may rely on context from before the redirect.
- **Waiting and result.** The return screen polls the payment and shows "Confirming your card payment", then the existing paid or failed screens. Closing the browser early returns to the same waiting screen, with "Open the card page again" while the order is open.
- **Receipts, Activity and notices** say "Visa card ending 0010". Notifications gain the method word "your card".
- Automatic investing stays mobile money only: Pesapal cannot charge a card on our schedule.
- Screens to design and screenshot: method picker with card, charge and total, waiting, return, paid, failed, card paused, and limit reached, phone and wide.

### Console changes

- **Settings.** Payment channels gains card pay in. Card limits as a setting changed by two people. A Pesapal statement layout under Statement import.
- **Reconciliation.** The Pesapal pot in Close the day. Statement import for Pesapal's daily export. Settlement recorded under Transfers between pots, Pesapal to the trust account, with Pesapal's charge as the difference.
- **Card refunds,** a section under Reconciliation: propose, approve, then record that it was approved on Pesapal's side and that it appeared on the statement. In the approvals inbox as `card_refund`.
- **Payments.** A client's card payments show the brand, last four, Pesapal's references and the status history.
- **Overview and Finance.** The Pesapal balance among the floats. New account codes in the accounting export maps.

### Ledger and reconciliation for cards

#### Accounts

| Account | Kind | Use |
| --- | --- | --- |
| `pesapal.balance` | Asset | Card money Pesapal holds for Finvest until settlement. A pot of its own. |
| `fees.provider.pesapal` | Expense | Pesapal's charge, when Finvest bears it |
| `client.refund_in_flight` | Liability, per client | A card refund approved and sent, not yet on Pesapal's statement |

#### Entries

| Event | Debit | Credit |
| --- | --- | --- |
| Card payment confirmed (`payment.deposit_confirmed`) | `pesapal.balance` | `client.awaiting_start`, the client, reference the investment |
| Pesapal's charge (`payment.provider_charge`) | `fees.provider.pesapal` | `pesapal.balance` |
| Settlement to the bank (`pot_transfer`, two people) | `bank.trust` with what arrived | `pesapal.balance` with what left, any difference to `fees.provider.pesapal` |
| Card refund sent (`payment.card_refund_sent`) | `client.awaiting_start` | `client.refund_in_flight` |
| Card refund on the statement (`payment.card_refund_settled`) | `client.refund_in_flight` | `pesapal.balance` |

If the client pays the charge instead, the deposit entry debits `pesapal.balance` with the whole amount collected, credits `client.awaiting_start` with the investment's amount and credits income `fees.card_charges_recovered` with the charge. Pesapal's actual charge is then booked against `fees.provider.pesapal` as above. Whether Pesapal takes its charge per payment or at settlement decides when that entry is posted (confirm).

#### The daily close

- The Pesapal pot joins the required pots once the card provider is on. Pesapal documents no statement API, so finance downloads the day's transaction export from Pesapal's dashboard and imports it like a bank statement. A day must have a statement even when nothing happened (confirm Pesapal's export can give one).
- Card payments carry our deposit ID as Pesapal's merchant reference, so they match by reference. Refunds carry the confirmation code and are paired by it.
- Settlement leaves Pesapal and arrives at the trust account a day or two later, so it is an open exception on the trust pot until the bank line appears, as a mobile money sweep is.
- Pesapal holds client money between payment and settlement, as the mobile money wallet does. Settlement is asked for at least every working day, so the dealing desk can buy bills with it.

#### Reversals and chargebacks

A status read that turns a completed payment into `3` reversed, or a chargeback Pesapal tells us about, is never posted automatically. Payments raises it to staff as a card reversal, then the existing corrections put it right, each by two people:

- The money still waits for the investment: the deposit is reversed ([payments.md](payments.md#correcting-a-mistaken-credit)), crediting `pesapal.balance`.
- The investment is running: it is unwound first, then the deposit is reversed.
- The money has left: a receivable is opened against the client, then recovered or written off.

A chargeback Finvest disputes successfully is posted back as money arriving at the pot.

## Two people on staff actions

Following the standing rule that a staff action that can have a maker and a checker must have one:

| Action | Proposer | Approver |
| --- | --- | --- |
| Refund card money to the card | Operations or finance | A different person with a fresh passkey or app code. The worker then sends `RefundRequest`. |
| Approving that refund on Pesapal's own dashboard | Done by the approver, or a third person, and recorded in the console | |
| Recording a settlement from Pesapal to the trust account | One person | A different person |
| Putting right a reversal or chargeback | As the existing reversal, unwind and receivable steps | |
| Pausing or reopening card payments, and changing card limits | Settings, two people | |
| Importing Pesapal's statement and closing the day | As every pot | |
| Registering or changing the IPN address | A deploy change, reviewed in a pull request | |

A refund's `username` sent to Pesapal is the approver's staff ID, never a name. Pesapal accepts one refund per payment, so the console refuses a second.

## Card data and compliance

- **No card number, expiry or security code ever reaches Finvest.** Card entry is on Pesapal's hosted page, reached by a full redirect. We keep only what the status read returns: brand, masked card and Pesapal's references. The masked card is sealed at field level like other account details and never logged.
- **PCI DSS.** With a hosted redirect, Finvest should only need the short self-assessment (SAQ A) each year. Pesapal should confirm which questionnaire it wants, and give its own attestation of compliance.
- **Staff never ask for a card number.** Help chat warns a client who types something like a card number and hides it from staff, and the support guidance says so.
- **3-D Secure** on every card payment, so the issuing bank confirms the cardholder.
- **Anti money laundering.** Card deposits are screened and monitored like other deposits. Limits, refunds back to the card and the 30 day rule make stolen card money hard to cash out. The cardholder's name is not available from Pesapal, so the "payer is the client" check that mobile money has cannot be made for cards; 3-D Secure and the limits stand in for it.
- **Data protection.** Pesapal processes the client's email or phone and card details. A data processing agreement with Pesapal is needed under the Data Protection and Privacy Act.
- **Regulator and counsel.** Confirm that taking investment payments by card, and Pesapal holding the money until settlement, fits Finvest's licence and the client money rules, as for the ioTec wallet.
- **Terms and site.** The client terms and the website's Help and fees pages say cards are accepted, any charge, and that card refunds go back to the card.

## What Solo needs to get from Yo!

1. A Yo! Payments **Business** account for the Finvest company, with API access, and the API access letter for payouts.
2. Sandbox and live API username and password, and the addresses Finvest's servers must call from, if Yo! keeps an allow-list (Finvest's reserved address is 159.65.210.209).
3. The current API specification, since the public copy is from 2012.
4. Prices for money in and for payouts, on MTN and on Airtel, and any monthly fee.
5. Whether a repeated `ExternalReference` is refused, and that a status read by our reference works for every payment.
6. Whether payouts must be signed, and with which algorithm (the libraries use SHA-1; ask whether SHA-256 is accepted).
7. Whether notifications are retried, and what the failure notice's reference holds.
8. Limits per payment and per day, on each network.
9. How money is swept to the trust account, how fast, and at what cost, and whether MTN and Airtel money are separate balances.
10. Confirmation that their Bank of Uganda licence is current, and a data processing agreement.

## What Solo needs to get from Pesapal

1. A merchant account for the Finvest company. Pesapal's Uganda list: certified certificate of incorporation, company Form 7 or Form 20, a bank letter for the settlement account, the company TIN, a board resolution to deal with Pesapal, directors' IDs and TINs, and the beneficial owners form.
2. **Their written confirmation that they accept an investment platform** selling Treasury bills and fixed income, and what they need for it (a Capital Markets Authority licence, for example).
3. Sandbox and live consumer key and secret, for UGX (and USD if the dollar product takes cards).
4. A fee quote for local and foreign Visa and Mastercard, any monthly or set-up fee, the refund and chargeback fees, and whether a charge may be passed to the client.
5. Settlement terms: settlement **to the trust account**, how often (daily, automatic or on request), in which currency, and whether a rolling reserve is held back.
6. How to get a daily statement or transaction export, and its columns, and whether there is an API for it.
7. The chargeback process: how we are told, the deadlines, the evidence they want, and how long after a payment a chargeback can come.
8. Confirmation that 3-D Secure is on for every card payment, and the largest payment allowed.
9. Which PCI questionnaire they want from us, and their attestation of compliance.
10. How refunds are approved on their side and how we learn they are done.
11. IPN details: which addresses it comes from, how often it retries, and whether a repeated merchant reference is refused.
12. A data processing agreement.
13. Optional, for later: Openfloat API documentation, in case payouts through Pesapal are ever wanted.

## Order of work, once approved

Mobile money first, because it is most of the money and the change is small:

1. Backend: the Yo! collections adapter behind the existing port, its callbacks and sandbox, tested against a fake and Yo!'s sandbox.
2. Backend: the Yo! payouts adapter, if the owner moves payouts.
3. Reconciliation: the Yo! pot and the nightly statement pull.
4. Infrastructure: the `FINVEST_YO_*` settings in the deploy, then switch day.

Then cards:

1. Backend: the card port, the Pesapal adapter and sandbox, the `card` deposit method and its migration, the status read, sweep and IPN route. Tested against the fake and the real Pesapal sandbox.
2. Backend: the client route, limits, channel switch and monitoring hooks, then `make api-contract`.
3. Client app: method picker, Pesapal page, return and result screens, with screenshots.
4. Reconciliation: the Pesapal pot, statement layout, settlement as a pot transfer, the daily close.
5. Card refunds and reversals, backend then console.
6. Console: Settings, Payments, Overview and Finance additions, with screenshots.
7. Infrastructure: the `FINVEST_PESAPAL_*` settings in the deploy, the IPN address registered, Content Security Policy checked for the redirect.
8. Docs: [payments.md](payments.md), [reconciliation.md](reconciliation.md), [platform-settings.md](platform-settings.md) and [decisions.md](decisions.md) updated as each piece lands, and this plan retired.

The first live check is a small real card payment after deploy, refunded to the card, before the channel is opened to clients.

## The designs

Redesigned before building, as the owner asked. One image per screen and layout, in [screenshots/payment-design](screenshots/payment-design/) (made from static mockups in its `source` folder, not from the app). Phone image 02 is taller than one screen; when the real screens are built, its screenshot shows where the pay button falls at the fold.

| Screen | Shows |
| --- | --- |
| `client-*-01-pay-mobile-money` | Pay with mobile money (MTN or Airtel), card or bank transfer, MTN chosen, prompted on the account's own number |
| `client-*-02-pay-card-with-charge` | Card chosen, with the card charge and the total shown before paying |
| `client-*-03-approve-on-your-phone` | Mobile money: approve the prompt on your phone |
| `client-*-04-finish-on-pesapal` | Card: what happens on Pesapal's page, before it opens |
| `client-*-05-confirming-card` | Back from Pesapal, waiting for the confirmation, with a way to open the page again |
| `client-*-06-paid-by-card` | Paid, with the card's last four digits and the charge |
| `client-*-07-card-declined` | Declined, nothing taken, try again or pay another way |
| `client-*-08-above-card-limit` | An investment above the card limit, card unavailable, mobile money and bank offered |
| `client-*-09-card-payments-paused` | Card payments paused by staff |
| `console-*-c1-settings-payment-channels` | Payment channels on Yo! and Pesapal, a pause waiting for a second person, and the card limits |
| `console-*-c2-card-payment-detail` | A client's card payment with Pesapal's references and its history |
| `console-*-c3-card-refunds` | Refunds to the card, the two people and Pesapal's own approval |
| `console-*-c4-close-the-day` | The Yo! and Pesapal pots in the day close, and recording a Pesapal settlement |

## Choices for the owner

| Choice | Recommended for now |
| --- | --- |
| Mobile money provider | Decided by the owner: Yo! Payments. |
| Card provider | Pesapal (the owner's answer on the decision card). |
| Mobile money payouts | Move to Yo! too, so money in pays money out from one account (on a decision card). |
| SMS and the phone-name check | Stay on ioTec for now. |
| Who pays Yo!'s charge | Finvest, as the owner decided for ioTec's pay-in charge: booked as Finvest's expense, each charge rounded up to a whole shilling. |
| Who pays the card charge | The client, shown in shillings before paying, if Pesapal allows it. Otherwise Finvest. A card charge of a few percent is a large share of a short bill's return. |
| Card limits | UGX 5,000,000 per payment and UGX 10,000,000 per client in 30 days, changeable on Settings by two people. |
| Early withdrawal of money paid by card | Within 30 days of the payment, refunded to the card rather than paid out. This changes how withdrawals work, so it needs the owner's own yes before it is built. |
| Dollar product by card | Not at first. Cards pay for shilling products only, until Pesapal confirms USD settlement to a dollar trust account. |

## Sources

Yo! Payments' official code libraries (github.com/YO-Uganda, the Python and PHP libraries), Yo!'s API specification v2.1, yo.co.ug's licence announcement and fee pages. Pesapal's API 3.0 reference (developer.pesapal.com, the pages for authentication, SubmitOrderRequest, RegisterIPN, GetTransactionStatus, RefundRequest, CancelOrder and recurring payments), Pesapal's Uganda business page and FAQs, its blog posts on required documents, test cards, its Bank of Uganda licences and Openfloat, and the dfcu Pesapal FAQ.
