/**
 * The contract between the bond screens and whatever serves them.
 *
 * The shapes follow the client routes in backend/app/modules/bonds/api.py (`/client/bonds`,
 * `/client/bonds/{code}/quote`, `/client/bond-holdings`). `apiBondsClient.ts` answers it from
 * the API; `stubBondsClient.ts` is the in-memory demo.
 *
 * A client buys a face value of one specific Bank of Uganda issue, at the price in force that
 * day, and is paid a coupon every six months on the issue's own dates and the face value at
 * maturity. A client never sees a tax rate or a figure before tax: every coupon here is after
 * tax, and every amount is one the backend worked out. The app adds nothing up itself.
 */

import type { Amount, Currency } from "@/investing/types";

export type { Amount, Currency };

export type BondStatus = "live" | "paused";

/** An issue, as a client sees it. */
export type Bond = {
  /** "BOND-10Y-2037" */
  code: string;
  isin: string;
  /** "10 Year Treasury Bond 2037" */
  name: string;
  currency: Currency;
  /** The yearly coupon after tax, as a label: "14.40". Nothing is worked out from it. */
  couponPercent: string;
  /** Calendar dates. */
  issueDate: string;
  maturityDate: string;
  years: number;
  /** Null once the last coupon is paid. */
  nextCouponDate: string | null;
  /** The coupons still to be paid, the one at maturity included. */
  couponsLeft: number;
  minimumFace: Amount;
  /** Face values go up in this step. */
  faceStep: Amount;
  /** Per 100 of face value, to four decimal places as an integer: 98.5 is 985000. Null until staff set one. */
  price: number | null;
  /** A paused issue is shown but takes no new buying. */
  status: BondStatus;
  /** Live, priced, and not yet in its last coupon period. */
  canBuy: boolean;
};

/** What buying a face value costs today and pays to maturity, after tax. */
export type BondQuote = {
  code: string;
  face: Amount;
  price: number;
  /** Interest earned since the last coupon: paid on top of the price, and back in the next coupon. */
  accrued: Amount;
  /** What the client pays: the price and the accrued interest. */
  cost: Amount;
  /** Each coupon after tax. */
  coupon: Amount;
  couponsLeft: number;
  nextCouponDate: string;
  /** The coupons still to come and the face value at maturity. */
  totalBack: Amount;
  /** `totalBack` less `cost`. */
  gain: Amount;
};

export type HoldingStatus = "unpaid" | "active" | "matured" | "sold" | "cancelled";

/** A face value of one issue that the client bought, or is paying for. Every figure is after tax. */
export type BondHolding = {
  id: string;
  code: string;
  name: string;
  face: Amount;
  price: number;
  accrued: Amount;
  /** What the client pays, or paid. */
  cost: Amount;
  status: HoldingStatus;
  /** ISO 8601, UTC. */
  createdAt: string;
  /** Pay by this moment, or the purchase lapses. Null once it is not unpaid. ISO 8601, UTC. */
  payBy: string | null;
  /** The business day it started, once paid. */
  startDate: string | null;
  maturityDate: string;
  coupon: Amount;
  couponsLeft: number;
  nextCouponDate: string | null;
  /** The coupons still to come and the face value at maturity. */
  stillToCome: Amount;
  /** Where coupons are paid. Null until the client chooses: coupons then wait for one. */
  couponDestinationId: string | null;
};

export type NewBondHolding = {
  code: string;
  face: Amount;
  /** Made once when the review screen opens and sent with every try, so a double tap buys once. */
  idempotencyKey: string;
};

/** `paid_out` once a payout carries it, `held` while it waits for an account the client names. */
export type MoneyStatus = "paid_out" | "held";

/** A coupon paid to one of the client's holdings, after tax. */
export type BondCoupon = {
  id: string;
  holdingId: string;
  name: string;
  couponDate: string;
  amount: Amount;
  status: MoneyStatus;
  payoutId: string | null;
  /** Why it waits: `no_destination`, or the payout's refusal. */
  heldBecause: string | null;
};

/** A holding repaid at maturity: the face value and the last coupon, after tax. */
export type BondRepayment = {
  id: string;
  holdingId: string;
  name: string;
  repaidOn: string;
  face: Amount;
  coupon: Amount;
  /** What the client is paid: the face value and the last coupon. */
  amount: Amount;
  status: MoneyStatus;
  payoutId: string | null;
  heldBecause: string | null;
};

/**
 * `requested`, `priced` and `approved` while Finvest's dealing desk sells it, then `sold`.
 * `cancelled` by the client, or `closed` when the bond matured first and was repaid instead.
 */
export type SaleStatus = "requested" | "priced" | "approved" | "sold" | "cancelled" | "closed";

/** A client's ask to sell a whole holding early, and what it paid, after tax. */
export type BondSale = {
  id: string;
  holdingId: string;
  name: string;
  face: Amount;
  /** ISO 8601, UTC. */
  requestedAt: string;
  status: SaleStatus;
  destinationId: string;
  /** Once sold: the price per 100, the day it settled, and what the client is paid. */
  price: number | null;
  soldOn: string | null;
  amount: Amount | null;
  payoutStatus: MoneyStatus | null;
  payoutId: string | null;
  heldBecause: string | null;
};

/** Money that waits for an account: a coupon, a repayment at maturity, or a sale's proceeds. */
export type HeldKind = "coupon" | "repayment" | "sale";

export type HeldPayOut = {
  kind: HeldKind;
  id: string;
  /** One of the client's approved accounts past its first day. */
  destinationId: string;
  /** From confirming with the PIN, on a phone with one. A browser sends none. */
  pinConfirmation?: string | null;
};

export type SaleRequest = {
  holdingId: string;
  /** The approved account the proceeds go to. */
  destinationId: string;
  /** Made for this request and sent with every try, so a double tap asks once. */
  idempotencyKey: string;
  pinConfirmation?: string | null;
};

export interface BondsClient {
  /** Live and paused issues that have not matured. */
  listBonds(): Promise<Bond[]>;
  getBond(code: string): Promise<Bond>;
  quote(code: string, face: Amount): Promise<BondQuote>;
  /** Saves the purchase at today's price, waiting for its payment. Nothing is paid yet. */
  buy(request: NewBondHolding): Promise<BondHolding>;
  /** Unpaid and active ones, newest first. */
  listHoldings(): Promise<BondHolding[]>;
  getHolding(holdingId: string): Promise<BondHolding>;
  /** Where the holding's coupons are paid, or null to clear it so they wait. */
  chooseCouponDestination(holdingId: string, destinationId: string | null): Promise<BondHolding>;
  /** Every coupon paid to the client's holdings, newest first. */
  listCoupons(): Promise<BondCoupon[]>;
  listRepayments(): Promise<BondRepayment[]>;
  /** Ask Finvest to sell the whole holding early. Nothing moves until the desk sells it. */
  sell(request: SaleRequest): Promise<BondSale>;
  /** The client's asks to sell, newest first. */
  listSales(): Promise<BondSale[]>;
  /** Allowed until the desk has priced it. */
  cancelSale(saleId: string): Promise<BondSale>;
  /** Send on money that waits for an account. */
  payOutHeld(request: HeldPayOut): Promise<void>;
}

/**
 * The pay screens' ID for paying for a holding, so they tell it from an investment. "~" is in no
 * ID the backend makes, and is safe in a URL as it is.
 */
export function bondPayableId(holdingId: string): string {
  return `bond~${holdingId}`;
}

/** The holding a pay screen ID names, or null when it names something else. */
export function holdingIdOf(payableId: string): string | null {
  return payableId.startsWith("bond~") ? payableId.slice("bond~".length) : null;
}

export type BondsErrorCode =
  | "below_minimum"
  /** Not a whole number of steps of face value. */
  | "not_a_step"
  | "above_maximum"
  | "not_open"
  /** The last coupon period has begun: too close to maturity to buy. */
  | "too_close_to_maturity"
  | "no_price"
  | "not_verified"
  | "too_many_unpaid"
  | "account_closing"
  /** Selling: the holding is not running, has matured, or already has an ask waiting. */
  | "cannot_sell"
  /** The ask was priced since, so it can no longer be cancelled. */
  | "cannot_cancel"
  /** The money is no longer waiting: it was sent on already. */
  | "already_sent"
  | "account_not_ready"
  | "over_limit"
  | "pin_required"
  | "not_found"
  | "key_reused"
  | "invalid"
  | "unavailable";

export class BondsError extends Error {
  readonly code: BondsErrorCode;

  constructor(code: BondsErrorCode) {
    super(code);
    this.name = "BondsError";
    this.code = code;
  }
}

const messages: Record<BondsErrorCode, string> = {
  below_minimum: "That is below the smallest face value for this bond.",
  not_a_step: "Face values of this bond go up in fixed steps. Choose a whole number of steps.",
  above_maximum: "That is more than one purchase can hold. Split a larger sum over several purchases.",
  not_open: "This bond is not taking new buyers right now.",
  too_close_to_maturity: "This bond is too close to maturity to buy. Its last coupon period has begun.",
  no_price: "This bond has no price today, so it cannot be bought yet.",
  not_verified: "You can buy bonds once your ID check is approved.",
  too_many_unpaid: "You have five bond purchases waiting to be paid for. Pay for one of those, or let it lapse, before buying another.",
  account_closing: "Your request to close your account is being checked, so nothing new can start. To carry on, go to Profile, then Close account, and choose Keep my account open.",
  cannot_sell: "This bond cannot be sold now. It may have matured, or you have already asked to sell it.",
  cannot_cancel: "This can no longer be cancelled. Finvest has already priced the sale.",
  already_sent: "This money is already on its way to you. Open your payouts to follow it.",
  account_not_ready: "That account cannot be paid yet. A new account can be paid one day after it is approved.",
  over_limit: "That is more than this account can take today. Choose a bank account, or try again tomorrow.",
  pin_required: "Your PIN confirmation ran out or was already used. Try again and enter your PIN.",
  not_found: "We could not find that bond.",
  key_reused: "Something changed since you last tried. Go back and review your purchase again.",
  invalid: "We could not accept that. Go back, check the details and try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function describeBondsError(error: unknown): string {
  return messages[error instanceof BondsError ? error.code : "unavailable"];
}
