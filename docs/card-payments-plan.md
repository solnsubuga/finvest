# Plan: card payments through Pesapal

**Status: a plan, not built.** Nothing here is in the code yet. Building waits for the owner's approval of this plan and the choices in [Choices for the owner](#choices-for-the-owner). Facts about Pesapal come from its public API 3.0 documentation, read on 14 November 2026. Items marked "confirm" are not documented publicly and must be checked with Pesapal before anyone relies on them.

## In short

- **Pesapal takes Visa and Mastercard card payments.** Mobile money stays on ioTec Pay, which already works end to end ([payments.md](payments.md)). Bank transfer stays as it is.
- A card payment is a new deposit method, `card`, beside `mtn`, `airtel` and `bank_transfer`. It pays for one investment or one goal saving, exactly as every other deposit does. There is still no wallet.
- The card number is typed only on Pesapal's own page. Finvest's servers and apps never see it, which keeps Finvest in the smallest card security scope (PCI DSS SAQ A, confirm).
- Pesapal holds the money until it is settled to the trust bank account, so the Pesapal balance becomes a new pot in reconciliation, closed every day like the ioTec wallets.
- Refunds of card money go back to the card through Pesapal, approved by two people. A chargeback is put right with the corrections payments already has.
- Pesapal offers no payouts through its API, so payouts are unchanged: mobile money through ioTec, bank through the trust account.

## Pesapal at a glance

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

## How Pesapal fits next to ioTec

| Way to pay | Provider | Why |
| --- | --- | --- |
| MTN Mobile Money, Airtel Money | ioTec Pay (unchanged) | Built, tested and deployed. ioTec prompts the account's own number, which enforces the owner's rule that the payer is the client's own number before anything is sent. |
| Visa, Mastercard | **Pesapal (new)** | ioTec takes no cards. Pesapal is licensed by the Bank of Uganda and runs a hosted card page. |
| Bank transfer | The trust account (unchanged) | |

**Pesapal mobile money as a fallback is not planned.** On Pesapal's page the payer types any number, so the "payer must be the account's own number" rule could only be checked after the money has moved, then refunded in full. A mobile money payment through Pesapal is also capped at UGX 5,000,000. When ioTec is down, staff already pause the channel and the app offers another way ([platform-settings.md](platform-settings.md)). A fallback can be added later behind the same adapter if the owner wants it (see the choices).

## A card payment, step by step

The same shape as a mobile money deposit, so every rule in [rules.md](rules.md) holds.

1. **The client asks to pay by card.** `POST /client/investments/{id}/payments/card` with an `Idempotency-Key`. In the client's own transaction, payments saves the deposit as `created` (method `card`, provider `pesapal`, no payer number) and a job to send it. The request cannot name an amount; it is the investment's. The one live deposit per investment rule covers cards too. The answer is `202` with the deposit.
2. **The worker opens the order.** It marks the deposit `sending` and commits, then calls `SubmitOrderRequest` with no transaction open. Our reference (`id`) is the deposit ID. `billing_address` carries the client's proved email or phone, nothing else. The amount is turned from integer shillings into Pesapal's decimal only inside the adapter. It saves `order_tracking_id` as the provider reference and the `redirect_url`, and the deposit becomes `awaiting_payer` (the existing `awaiting_approval` status, shown to the client as "Waiting for your card payment").
3. **The app opens Pesapal's page.** The app polls `GET /client/payments/{id}` (it already does for mobile money) until the redirect address appears, usually in a second or two, then opens it. The payer types the card and passes 3-D Secure on Pesapal's page.
4. **The payer comes back.** Pesapal sends them to `callback_url`, a page of the client app that shows the payment as it polls. The return carries no result and changes nothing.
5. **The worker reads the result.** An IPN, the app's return, or the sweep queues a status read. Only `GetTransactionStatus` decides: `1` completed, for exactly the amount and currency asked, confirms the deposit and posts the ledger entry in that transaction, then `payments.deposit_confirmed` starts the investment as today. `2` failed fails the deposit with a mapped code (`declined`, `insufficient_funds`, `other`). A different amount holds the deposit `uncertain` with `amount_mismatch` for staff, as today.

### Why a lost answer is safe here

Pesapal documents no idempotency on `SubmitOrderRequest`, and a status read needs Pesapal's tracking ID. If the answer to the send is lost, we cannot look the order up. That is still safe: no card can be charged until the payer opens the `redirect_url`, and the payer only ever gets it from our saved answer. So a send whose answer was lost becomes `failed` with `provider_unavailable` rather than `uncertain`, and the client simply tries again with a new deposit. Confirm with Pesapal that an order cannot be paid without its redirect address.

### What stops a card payment getting stuck

The sweep in [payments.md](payments.md#what-stops-a-deposit-getting-stuck) gains these rows for cards:

| Found | Action |
| --- | --- |
| `awaiting_payer` and quiet for 2 minutes | A status read is queued, then every few minutes. |
| `awaiting_payer` for 30 minutes, not completed | The worker sends `CancelOrder` (a job registered as having an external effect, sent once), then reads the status again. Only a read that shows failed, or an accepted cancel followed by a read that is not completed, fails the deposit with `approval_timed_out`. |
| Anything else unclear | `uncertain`, read on the existing back-off for 24 hours, then reconciliation settles it from Pesapal's statement. |

### Notifications from Pesapal

`POST /provider-callbacks/collections/pesapal` (or `GET`, whichever we register). The IPN is not signed, so the registered address carries a long random token in its path (`FINVEST_PESAPAL_IPN_TOKEN`) to keep out noise, and the notice is treated as a hint only, as every callback is. The route records it once, keyed `<OrderTrackingId>:<OrderNotificationType>`, queues a status read, and answers the JSON Pesapal asks for. It cannot read or change deposits.

## Backend changes

All in `backend/app/modules/payments` unless said otherwise, with a migration range asked of the coordinator at build time.

- **Port.** A `CardCollectionsProvider` in `domain/provider.py`: open an order (returns the tracking ID and redirect address, or rejected, deferred or uncertain), read its status, cancel it, request a refund, and read a notice. Every failure is sorted and never raised, as the mobile money port does.
- **Adapters.** `providers/pesapal.py` (token cached and refreshed before its 5 minutes, shared in `app/infrastructure/pesapal`), and a card sandbox whose amount's last digits choose the result, refused in production like the mobile money sandbox. Tested against a fake that returns success, rejection, timeout and an uncertain outcome.
- **Deposits.** Method `card` and provider `pesapal` added to the check constraints. A card deposit has no payer number. New columns, set once by the worker: the redirect address, the card brand and masked card from the status read (`payment_account`, sealed like other account details), and Pesapal's `confirmation_code`, which a refund needs. The state trigger and its every-pair test gain the card path.
- **Client routes.** `POST /client/investments/{id}/payments/card`, then the existing `GET /client/investments/{id}/payment` and `GET /client/payments/{id}`, which gain `redirect_url` while waiting and `card` (brand and last four) once paid. Goals use the same route once goal payments over HTTP exist.
- **Charge.** Booked as ioTec's is, from what Pesapal reports, rounded up to a whole shilling. If the client pays the charge, it is added to what Pesapal collects and shown before paying (see the choices).
- **Channel switch.** `payment_channels` gains `card` pay in, paused by two people like the others. A paused card channel answers `503` `channel_paused`.
- **Monitoring and fraud.** Card deposits feed `screening.monitor_deposit` like any other. A masked card seen on another client's paid deposit is flagged to staff, as a reused bank receipt is. Card limits (see below) are checked before the deposit is saved.
- **Settings** (`FINVEST_PESAPAL_*`): `CONSUMER_KEY` and `CONSUMER_SECRET` (secrets), `IPN_ID` (registered once with a command, then a variable), `IPN_TOKEN` (secret), `BASE_URL`, `TIMEOUT_SECONDS`, and `FINVEST_CARD_PROVIDER` = `disabled` (default), `sandbox` or `pesapal`. API and worker need the credentials; the API also needs the IPN token. The infrastructure thread wires them into the deploy.
- **Audit.** `payments.deposit_requested`, `_confirmed` and `_failed` with method `card`, and new events for refunds. Never the card, the amount or the client's details in logs.

### Limits for cards, decided for now

Stolen cards are the main risk: a stolen card pays for an investment, and the money is taken out to mobile money before the real cardholder disputes it. So, until the owner changes them:

- At most UGX 5,000,000 per card payment and UGX 10,000,000 per client in any 30 days. Changed by two people on Settings.
- 3-D Secure on every card payment (confirm Pesapal enforces it).
- Money paid by card that is refunded, for any reason, goes back to the same card, never to a payout account.
- An early withdrawal of an investment paid by card within 30 days of the payment is refunded to the card, not paid to mobile money or a bank. Maturity payouts are unchanged.

## Client app changes

In `apps/client/src/payments` and the pay screens.

- **Pick a way to pay** gains "Card (Visa or Mastercard)", shown only when the card channel is open. If the client pays the charge, the screen shows it in shillings and the total before they go on. No rate is shown.
- **Paying.** On the web (app.finvestug.com) the app sends the whole page to Pesapal and Pesapal sends it back to `https://app.finvestug.com/pay/card/return?payment=<id>`. On Android and iPhone the app opens Pesapal's page in the system's secure in-app browser (`expo-web-browser`, a new dependency), which closes when the payer reaches the return address. **No iframe.** A full redirect keeps card entry entirely on Pesapal's page, which is what keeps us at SAQ A, and the client app's Content Security Policy needs no frame source for Pesapal.
- **Waiting and result.** The return screen polls the payment and shows "Confirming your card payment", then the existing paid or failed screens. Closing the browser early returns to the same waiting screen, with "Open the card page again" while the order is open.
- **Receipts, Activity and notices** say "Visa card ending 0010". Notifications gain the method word "your card".
- Automatic investing stays mobile money only: Pesapal cannot charge a card on our schedule.
- Screens to design and screenshot: method picker with card, charge and total, waiting, return, paid, failed, card paused, and limit reached, phone and wide.

## Console changes

- **Settings.** Payment channels gains card pay in. Card limits as a setting changed by two people. A Pesapal statement layout under Statement import.
- **Reconciliation.** The Pesapal pot in Close the day. Statement import for Pesapal's daily export. Settlement recorded under Transfers between pots, Pesapal to the trust account, with Pesapal's charge as the difference.
- **Card refunds,** a section under Reconciliation: propose, approve, then record that it was approved on Pesapal's side and that it appeared on the statement. In the approvals inbox as `card_refund`.
- **Payments.** A client's card payments show the brand, last four, Pesapal's references and the status history.
- **Overview and Finance.** The Pesapal balance among the floats. New account codes in the accounting export maps.

## Ledger and reconciliation

### Accounts

| Account | Kind | Use |
| --- | --- | --- |
| `pesapal.balance`, reference `card` | Asset | Card money Pesapal holds for Finvest until settlement. A pot of its own. |
| `fees.provider.pesapal` | Expense | Pesapal's charge, when Finvest bears it |
| `client.refund_in_flight` | Liability, per client | A card refund approved and sent, not yet on Pesapal's statement |

### Entries

| Event | Debit | Credit |
| --- | --- | --- |
| Card payment confirmed (`payment.deposit_confirmed`) | `pesapal.balance` | `client.awaiting_start`, the client, reference the investment |
| Pesapal's charge (`payment.provider_charge`) | `fees.provider.pesapal` | `pesapal.balance` |
| Settlement to the bank (`pot_transfer`, two people) | `bank.trust` with what arrived | `pesapal.balance` with what left, any difference to `fees.provider.pesapal` |
| Card refund sent (`payment.card_refund_sent`) | `client.awaiting_start` | `client.refund_in_flight` |
| Card refund on the statement (`payment.card_refund_settled`) | `client.refund_in_flight` | `pesapal.balance` |

If the client pays the charge instead, the deposit entry debits `pesapal.balance` with the whole amount collected, credits `client.awaiting_start` with the investment's amount and credits income `fees.card_charges_recovered` with the charge. Pesapal's actual charge is then booked against `fees.provider.pesapal` as above. Whether Pesapal takes its charge per payment or at settlement decides when that entry is posted (confirm).

### The daily close

- The Pesapal pot joins the required pots once the card provider is on. Pesapal documents no statement API, so finance downloads the day's transaction export from Pesapal's dashboard and imports it like a bank statement. A day must have a statement even when nothing happened (confirm Pesapal's export can give one).
- Card payments carry our deposit ID as Pesapal's merchant reference, so they match by reference. Refunds carry the confirmation code and are paired by it.
- Settlement leaves Pesapal and arrives at the trust account a day or two later, so it is an open exception on the trust pot until the bank line appears, as an ioTec sweep is.
- Pesapal holds client money between payment and settlement, as the ioTec wallet does. Settlement is asked for at least every working day, so the dealing desk can buy bills with it.

### Reversals and chargebacks

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

1. Backend: the card port, the Pesapal adapter and sandbox, the `card` deposit method and its migration, the status read, sweep and IPN route. Tested against the fake and the real Pesapal sandbox.
2. Backend: the client route, limits, channel switch and monitoring hooks, then `make api-contract`.
3. Client app: method picker, Pesapal page, return and result screens, with screenshots.
4. Reconciliation: the Pesapal pot, statement layout, settlement as a pot transfer, the daily close.
5. Card refunds and reversals, backend then console.
6. Console: Settings, Payments, Overview and Finance additions, with screenshots.
7. Infrastructure: the `FINVEST_PESAPAL_*` settings in the deploy, the IPN address registered, Content Security Policy checked for the redirect.
8. Docs: [payments.md](payments.md), [reconciliation.md](reconciliation.md), [platform-settings.md](platform-settings.md) and [decisions.md](decisions.md) updated as each piece lands, and this plan retired.

The first live check is a small real card payment after deploy, refunded to the card, before the channel is opened to clients.

## Choices for the owner

| Choice | Recommended for now |
| --- | --- |
| What Pesapal is used for | Cards only. Mobile money stays on ioTec, with no Pesapal fallback. |
| Who pays the card charge | The client, shown in shillings before paying, if Pesapal allows it. Otherwise Finvest, as with ioTec. A card charge of a few percent is a large share of a short bill's return, so absorbing it would cost far more than ioTec's charge. |
| Card limits | UGX 5,000,000 per payment and UGX 10,000,000 per client in 30 days, changeable on Settings by two people. |
| Early withdrawal of money paid by card | Within 30 days of the payment, refunded to the card rather than paid out. |
| Dollar product by card | Not at first. Cards pay for shilling products only, until Pesapal confirms USD settlement to a dollar trust account. |

## Sources

Pesapal's API 3.0 reference (developer.pesapal.com, the pages for authentication, SubmitOrderRequest, RegisterIPN, GetTransactionStatus, RefundRequest, CancelOrder and recurring payments), Pesapal's Uganda business page and FAQs, its blog posts on required documents, test cards, its Bank of Uganda licences and Openfloat, and the dfcu Pesapal FAQ.
