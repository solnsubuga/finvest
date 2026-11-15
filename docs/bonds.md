# Bonds

Uganda government bonds, sold the way Bank of Uganda sells them. The owner decided on 2026-11-15 that a client buys a part of a specific bond issue, not a bond by tenor counted from the day they pay (see [decisions.md](decisions.md)). The pure rules are in `backend/app/modules/bonds/domain`, the catalogue in `backend/app/modules/bonds/catalogue.py`, the routes in `backend/app/modules/bonds/api.py`, and the database side is migration 0490.

This document covers the catalogue, buying and the coupon run. Selling early and maturity come in later pull requests, and this document grows with them.

## How a bond works

Bank of Uganda sells each bond as an issue: an ISIN, a coupon rate fixed for its whole life, an issue date and a maturity date. It pays a coupon every six months on the issue's own dates, the same dates for every holder, and the face value at maturity. It often reopens an issue later, selling more of it at a price set by the market yield on the day, so a bond is bought above or below its face value. Finvest holds the bonds for its clients through the dealing desk, and a client owns a face value of one issue.

A client who wants to get out early sells through Finvest: the dealing desk sells that face value on the secondary market at the market price, which can be below what the client paid.

Bills are different. Finvest gives each client their own bill term from the day their payment lands, because the desk can match it with a new bill every two weeks. That is the [investing](investing.md) module, and it is not used for bonds.

## Issues

| Field | Rule |
| --- | --- |
| Code | Upper-case letters, digits and hyphens, up to 24, such as `BOND-10Y-2037`. Never changes. |
| ISIN | 12 characters, such as `UG12K1405378`. Unique. Never changes. |
| Name | 1 to 80 characters, such as "10 Year Treasury Bond 2037". |
| Coupon | The yearly rate before tax, in basis points: 16% is `1600`. Never changes. |
| Tax | Withholding tax on coupons. Defaults to Bank of Uganda's: 20% for bonds under ten years, 10% at ten years or more. Staff may enter another rate. Never changes once created. |
| Issue and maturity dates | The issue's own. Never change. |
| Smallest face value and step | UGX 100,000 and UGX 100,000 unless staff enter others. |
| Status | Draft (clients and the website never see it), live (clients can buy), or paused (no new buying; holdings carry on). |

A typing mistake in an issue is put right by leaving it paused and creating it again under a new code and the right ISIN, because clients' holdings refer to everything that never changes.

## Coupons and accrued interest

Coupon dates are every six calendar months back from the maturity date, on the maturity date's day of the month, or the month's last day when it is shorter, after the issue date. The last coupon is paid with the face value on the maturity date.

| Figure | Rule |
| --- | --- |
| A coupon before tax | Face value × coupon rate ÷ 2, rounded down to the shilling. |
| Tax on a coupon | The issue's tax rate applied to the coupon, rounded down. Clients see only the coupon after tax. |
| Accrued interest | The coming coupon × days since the last coupon date ÷ days in the period (actual/actual), rounded down. A buyer pays it on top of the price and gets it back in the full coupon. A seller is paid it on top of the sale price. |

Accrued interest rounds down on a buy as well as on a sale, on purpose: on a buy that favours the client by under a shilling, and the clean amount, which rounds up, is the figure Finvest's spread is in. One rule for every accrued figure is easier to check than two.

Worked example, `UG12K1405378` (16% coupon, 10% tax, matures 14 May 2037), UGX 1,000,000 of face value bought on 16 November 2026 at 98.5:

| | UGX |
| --- | --- |
| Price, 98.5 per 100 | 985,000 |
| Accrued interest, 2 of the 181 days since 14 November | 883 |
| The client pays | 985,883 |
| Each coupon after tax, 21 left including the one at maturity | 72,000 |
| Back by maturity: the face value and 21 coupons | 2,512,000 |

Every issue has a product type of the bond model, Treasury bond unless staff name another, such as a corporate bond type they defined (see [product-types.md](product-types.md)). The type is fixed with the issue.

## Prices

A price is per 100 of face value, to four decimal places, held as an integer: 98.5 is `985000` and par is `1000000`. A price below 50 or above 150 is refused as a typing mistake. What a client pays rounds up to the shilling, and what a client is paid rounds down.

The price in force is the latest one approved. Unlike a rate, a price takes effect when the second person approves it, because bond prices move with the market every day.

## Changing the catalogue

Staff never change an issue or its price directly. Each change is proposed by one member of staff with a reason and approved, or sent back with a note, by a different one, as products and rates are. The proposer can withdraw it while it waits. One change waits per issue at a time. Proposing, sending back and withdrawing need `products.change`; approving needs `products.change` and a second step from the last few minutes. Waiting changes appear in the console's approvals inbox as `bond_change`.

| Change | What it does | Rules |
| --- | --- | --- |
| New issue | Creates the issue as a draft, with its first price. | The code and ISIN are new. |
| Price | Sets today's price, from the auction result or the dealing desk's quote. | A price that changes nothing is refused. |
| Pause | Stops new buying. Holdings carry on to maturity and are paid their coupons. | Only a live issue pauses. |
| Open | Lets clients buy. | The issue needs a price, its currency must be one Finvest pays in and out in (shillings today), and it must not have entered its last coupon period. |

Clients cannot buy in the last coupon period, when only the face value and one coupon are left and the dealing desk may not find the bond before it matures (`too_close_to_maturity`).

### What the database enforces

| Rule | How |
| --- | --- |
| An issue is written only by an approval | The `bond_issues` trigger accepts a row only with `change_id` naming an approved change, decided by the member of staff the transaction acts for. A new issue has exactly the change's values. An existing one moves one version at a time by a pause or an opening made against that version, so an approved change can never be replayed. Nothing but its status ever changes, and nothing removes it. |
| A price is written only by an approval | The `bond_prices` trigger accepts a price only from an approved new issue or price change, with that change's price, in force from the moment it was approved. A price is never changed or removed. |
| An approval writes what it approved | A constraint checked at commit refuses an approval that wrote no issue or no price. |
| A second person decides | A check constraint refuses an approval or a send back by the proposer, and a withdrawal by anyone else. |
| Nobody acts in another's name | A trigger requires the proposer, and later the decider, to be the member of staff the transaction acts for, and a decided change never changes again. |
| One change at a time | A unique index lets one change wait per issue, and one new issue wait per code and per ISIN. |
| Changes are staff business | A row policy shows changes to staff transactions only. |

Issues and prices are readable on every API transaction, including the website's with nobody signed in.

## Buying

A client buys a face value of a live issue at the price in force, as a quote shows it. The purchase is saved as a holding that waits for one payment (`app/modules/bonds/holdings.py`, rules in `domain/holdings.py`):

    unpaid ──► active
       │
       └─► cancelled

* The face value, price, accrued interest (as at the day of the quote) and cost are fixed when the client confirms. They hold for 24 hours, shorter than a bill's 72 because a bond's price moves with the market. A client has at most five purchases waiting.
* The client pays exactly the cost by mobile money (ioTec or Pesapal, as Settings choose) or by card, through the payments module's routes under `/client/bond-holdings/{id}/payments/...`. The payment has purpose `bond` and the holding's ID. Bank transfers are not offered for bonds yet.
* When the payment is confirmed, the money is in the client's `client.awaiting_start` account under the holding's ID, as for an investment. The worker (`bonds.start_from_deposit`, a consumer of `payments.deposit_confirmed`) moves it to `client.bond_holding` in one entry, `bond.bought`, and the holding is active from that business day. Coupons dated after that day are paid to it.
* A quote is only good while its next coupon is still to come: the client paid interest up to the quote, and that coupon pays it back. A payment cannot be started on or after the next coupon date, and a payment confirmed then does not start the holding: the money stays waiting, for staff.
* Anything but the whole cost waiting is not started either. An unpaid holding is cancelled an hour after its window closes (`bonds.cancel_unpaid`, every 15 minutes), unless money or a payment that may still confirm is there.

The database (migration 0491) lets only the client save a holding, for themselves, while the issue is live and at the latest price, and only the worker move one on, once. What was bought never changes, and no holding is removed. Clients see only their own holdings.

The client's view is after tax: the coupon after tax, the coupons still to be paid and everything still to come (those coupons and the face value). It never shows the tax.

## The coupon run

Coupons are paid the way an investment's interest is paid at maturity (`app/modules/bonds/coupons.py`, rules in `domain/coupons.py`, database side migration 0492).

* `bonds.pay_coupons` runs at 01:00 Kampala time every day. It pays every coupon before maturity dated on or before that business day that a holding is owed and has not been paid. So a run that never happened, however long the scheduler was down, is made up by the next one, and a coupon is never lost. The day comes from the time the run was scheduled for, so a late run or a retry pays up to its own day and no further. A coupon paid late keeps its coupon date, and its ledger entry is dated the day it was paid.
* A holding is owed a coupon when it is active and started on a business day before the coupon date. The last coupon, on the maturity date, is paid with the face value at maturity, not by the run.
* Each coupon is one ledger entry, `bond.coupon`, dated the day it is paid, with the holding as its source: debit `interest.expense` with the coupon before tax, credit `tax.withholding_payable` with the tax, and credit the rest to the client's payout pending account for that coupon. The tax module reads these entries, so bond tax is in the monthly figures for URA and on the client's certificate, as a line named after the bond.
* The coupon after tax is then paid out to the destination the client chose for the holding (`PUT /client/bond-holdings/{id}/coupon-destination`). With none chosen, or one the payout module refuses (for example while a new number is in its 24-hour safety wait), the coupon is **held**: the money waits in payouts, and the client sends it on with their app PIN (`POST /client/bond-coupons/{id}/pay-out`). The reason it waits is kept on the coupon.
* Holdings are paid in batches of 200, each in a transaction only the current holder of the job can commit, and each holding in its own savepoint. One that fails is logged and stays owed, and the run is retried. A holding is never paid a date twice: the coupon is unique per holding and date, the ledger entry's idempotency key is the holding and date, and the holding is locked while it is paid.

The database (migration 0492) lets only the worker save a coupon. A held coupon can be marked paid out once, by the worker or the client it belongs to, and only with a payout of that coupon to that client. No figure on a coupon ever changes, and none is removed. A client may change only the coupon destination on their own holding, and not on a cancelled one. Clients see only their own coupons, after tax.

## HTTP routes

| Route | Who | What |
| --- | --- | --- |
| `GET /public/bonds` | Anyone | Live and paused issues that have not matured, with the coupon after tax, the next coupon date, coupons left and the price. Cached for five minutes, any origin. |
| `GET /public/bonds/{code}/quote?face_minor=` | Anyone | What buying that face value costs today (price and accrued interest) and pays to maturity, after tax. 422 with the field code when it cannot be bought. |
| `GET /client/bonds`, `GET /client/bonds/{code}`, `GET /client/bonds/{code}/quote` | Signed-in client | The same. |
| `GET /staff/bonds`, `GET /staff/bonds/{code}/quote` | `products.view` | Every issue with its tax, price and when it was set, and Bank of Uganda's usual tax for comparison; a quote with the clean amount. |
| `POST /client/bond-holdings` | Signed-in client, with an idempotency key | Buy a face value at today's price. 422 as the quote refuses, 409 with five waiting. |
| `GET /client/bond-holdings`, `GET /client/bond-holdings/{id}` | Signed-in client | Their holdings, unpaid and active, after tax. |
| `GET` and `POST /client/bond-holdings/{id}/payments/card`, `POST .../payments/mobile-money`, `GET .../payment` | Signed-in client | Paying the cost, as for an investment. The payment shows `purpose: "bond"`, with the holding's ID in `investment_id`. |
| `PUT /client/bond-holdings/{id}/coupon-destination` | Signed-in client | Choose where the holding's coupons are paid, or clear it with `null`. 422 on a destination that cannot be paid. |
| `GET /client/bond-coupons` | Signed-in client | Their coupons, newest first, after tax, and whether each is paid out or waiting. |
| `POST /client/bond-coupons/{id}/pay-out` | Signed-in client, with the app PIN | Send on a held coupon. 428 without the PIN, 422 when payouts refuse the destination, 409 when it is not waiting. |
| `GET /staff/bond-holdings?status=` | `investments.view` | Every client's holdings with what each paid. |
| `GET /staff/bond-coupons?status=` | `investments.view` | Coupons paid, with the tax, and the held ones with why they wait. |
| `GET /staff/bond-changes?status=` | `products.view` | New issues, prices, pauses and openings, newest first. |
| `POST /staff/bond-changes/new-issue`, `/price`, `/status`, `POST /staff/bond-changes/{id}/approve`, `/send-back`, `/withdraw` | `products.change` | Proposing and deciding, with the second person enforced in the database. Approving asks the person to confirm it is them. |

Audit events on resource `bond_change`: `bonds.change.proposed`, `.approved`, `.sent_back` and `.withdrawn`, with the issue's code, ISIN, coupon, tax, maturity and price. On resource `bond_holding`: `bonds.purchase_requested`, `bonds.holding_started` and `bonds.coupon_destination_chosen`.

## Not built yet

Selling early through the dealing desk, maturity (the face value and the last coupon), and the client app screens. No notice is sent when a coupon is paid or held. For bond payments: bank transfers, and the staff tools that undo a payment (reversing a mistaken deposit, moving it to another client, refunding a card), which take investment payments only for now. A bond payment that arrives too late to start its holding waits in the client's awaiting-start account for staff. **Follow-up: let the reversal and card refund tools take bond payments**, so staff can return that money. Until then, staff return it with a ledger correction (two people). No notice is sent when a holding starts. Unit trusts are their own product type, waiting on which fund managers Finvest has agreements with.
