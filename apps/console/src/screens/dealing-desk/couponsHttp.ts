/** Coupons paid in on the desk's bonds, over the real API (backend/app/modules/dealing/bond_coupons_api.py). */

import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import type { CouponsOut } from "@finvest/api-client/staff";
import { CouponError, type CouponErrorCode, type CouponsApi, type CouponsScreen, type NewCoupon } from "./coupons.ts";

const PATH = "/staff/dealing/bond-coupons";

/** The screen as the API sends it, from the generated contract. */
export type CouponsJson = CouponsOut;

export function toCouponsScreen(json: CouponsJson): CouponsScreen {
  return {
    currency: json.currency,
    mayChange: json.may_change,
    due: json.due.map((d) => ({ issueCode: d.issue_code, name: d.name, couponDate: d.coupon_date, face: d.face, expected: d.expected })),
    coupons: json.coupons.map((c) => ({
      id: c.id,
      issueCode: c.issue_code,
      couponDate: c.coupon_date,
      face: c.face,
      expected: c.expected,
      amount: c.amount,
      valueDate: c.value_date,
      bankReference: c.bank_reference,
      note: c.note,
      status: c.status,
      recordedBy: c.recorded_by,
      recordedAt: c.recorded_at,
      decidedBy: c.decided_by,
      decidedAt: c.decided_at,
      decisionReason: c.decision_reason,
      allowed: c.allowed,
    })),
  };
}

const FIELD_CODES = new Set<CouponErrorCode>([
  "not_a_coupon_date",
  "coupon_date_in_future",
  "nothing_held",
  "already_recorded",
  "value_date_before_coupon",
  "value_date_in_future",
  "value_date_in_a_closed_day",
  "note_required",
]);

/** The API's code, or the code of its first field problem, as the screen's own. */
export function toCouponError(error: unknown): CouponError {
  if (error instanceof CouponError) return error;
  if (!(error instanceof ApiError)) return new CouponError("unavailable");
  const field = error.fieldCode;
  if (field && FIELD_CODES.has(field as CouponErrorCode)) return new CouponError(field as CouponErrorCode);
  switch (error.code) {
    case "not_found":
    case "conflict":
    case "forbidden":
      return new CouponError(error.code);
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case CONFIRM_IT_IS_YOU:
      return new CouponError("not_confirmed");
    case "validation_failed":
      return new CouponError("invalid");
    default:
      return new CouponError("unavailable");
  }
}

export function createHttpCouponsApi(http: Http): CouponsApi {
  async function call(path: string, key: string, body?: unknown): Promise<CouponsScreen> {
    try {
      return toCouponsScreen(await http.request<CouponsJson>("POST", `${PATH}${path}`, body, { idempotencyKey: key }));
    } catch (error) {
      throw toCouponError(error);
    }
  }
  return {
    async getCoupons(currency) {
      try {
        return toCouponsScreen(await http.request<CouponsJson>("GET", `${PATH}?currency=${currency}`));
      } catch (error) {
        throw toCouponError(error);
      }
    },
    record: (key, given: NewCoupon) =>
      call("", key, {
        issue_code: given.issueCode,
        coupon_date: given.couponDate,
        currency: given.amount.currency,
        amount_minor: given.amount.minor,
        value_date: given.valueDate,
        bank_reference: given.bankReference,
        note: given.note,
      }),
    approve: (key, id) => call(`/${encodeURIComponent(id)}/approve`, key),
    decline: (key, id, reason) => call(`/${encodeURIComponent(id)}/decline`, key, { reason }),
  };
}
