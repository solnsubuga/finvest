/**
 * The console's view of coupons Bank of Uganda pays in on the dealing desk's own bonds.
 *
 * The shapes follow backend/app/modules/dealing/bond_coupons_api.py and docs/dealing-desk.md
 * ("Coupons paid in"). The backend works out which coupons are due, the face value the desk held
 * and what it earns. One member of staff records what reached the trust account; a different one
 * checks the bank statement and approves it, which posts it, or declines it. The console works
 * out no money figure and no date rule: forms check what was typed only to save a round trip.
 */

import type { Amount, Currency } from "./api.ts";

export type CouponStatus = "recorded" | "approved" | "declined";
/** What the signed-in person may do with a recorded coupon. The backend decides. */
export type CouponStep = "approve" | "decline";

/** A coupon date that has come on an issue the desk held, with nothing recorded for it yet. */
export interface CouponDue {
  issueCode: string;
  name: string;
  couponDate: string;
  /** The face value the desk held the day before the coupon date. */
  face: Amount;
  /** What that face value earns, before any tax. */
  expected: Amount;
}

export interface Coupon {
  id: string;
  issueCode: string;
  couponDate: string;
  face: Amount;
  expected: Amount;
  /** What reached the trust account, on `valueDate`. */
  amount: Amount;
  valueDate: string;
  bankReference: string;
  /** Why what arrived differs from what was expected. */
  note: string | null;
  status: CouponStatus;
  /** Staff ids. The screen names them. */
  recordedBy: string;
  recordedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  allowed: readonly CouponStep[];
}

export interface CouponsScreen {
  currency: Currency;
  due: readonly CouponDue[];
  /** Latest coupon date first. */
  coupons: readonly Coupon[];
  /** Whether the reader may record a coupon. */
  mayChange: boolean;
}

export interface NewCoupon {
  issueCode: string;
  couponDate: string;
  amount: Amount;
  valueDate: string;
  bankReference: string;
  note: string | null;
}

/** Every command takes an idempotency key made once per attempt by the screen. */
export interface CouponsApi {
  getCoupons(currency: Currency): Promise<CouponsScreen>;
  record(key: string, coupon: NewCoupon): Promise<CouponsScreen>;
  approve(key: string, couponId: string): Promise<CouponsScreen>;
  decline(key: string, couponId: string, reason: string): Promise<CouponsScreen>;
}

export type CouponErrorCode =
  | "not_found"
  | "conflict"
  | "forbidden"
  | "not_confirmed"
  | "not_a_coupon_date"
  | "coupon_date_in_future"
  | "nothing_held"
  | "already_recorded"
  | "value_date_before_coupon"
  | "value_date_in_future"
  | "value_date_in_a_closed_day"
  | "note_required"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class CouponError extends Error {
  readonly code: CouponErrorCode;

  constructor(code: CouponErrorCode) {
    super(code);
    this.name = "CouponError";
    this.code = code;
  }
}

const MESSAGES: Record<CouponErrorCode, string> = {
  not_found: "This coupon no longer exists.",
  conflict: "Someone else has already decided this coupon. The list has been reloaded.",
  forbidden: "A second person has to do this, and you may not approve a coupon you recorded.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_a_coupon_date: "That is not one of this issue's coupon dates.",
  coupon_date_in_future: "That coupon date has not come yet.",
  nothing_held: "The desk held none of this issue the day before that coupon date, so it earns no coupon.",
  already_recorded: "This coupon, or a payment with this bank reference, is already recorded.",
  value_date_before_coupon: "The money arrives on or after the coupon date. Check the day it reached the trust account.",
  value_date_in_future: "Record a coupon once it is on the trust account, on or before today.",
  value_date_in_a_closed_day: "That day is closed in the books. Record it on an open day.",
  note_required: "What arrived differs from what the bonds earn, so write a note saying why.",
  invalid: "Check the figures and try again.",
  not_connected: "Coupons are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function couponErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof CouponError ? error.code : "unavailable"];
}

export const COUPON_STATUS_LABELS: Record<CouponStatus, string> = {
  recorded: "Waiting for a second person",
  approved: "Approved and posted",
  declined: "Declined",
};

const notConnected = () => Promise.reject(new CouponError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedCouponsApi: CouponsApi = {
  getCoupons: notConnected,
  record: notConnected,
  approve: notConnected,
  decline: notConnected,
};
