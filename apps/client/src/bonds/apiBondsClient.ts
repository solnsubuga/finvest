import type { BondHoldingOut, BondOut, BondQuoteOut } from "@finvest/api-client/client";

import { api, ApiError, isAccountClosing } from "@/api/http";

import { Bond, BondHolding, BondQuote, BondsClient, BondsError, BondsErrorCode } from "./types";

/**
 * The bond screens' contract, answered by the real API (backend/app/modules/bonds/api.py).
 * Every figure is the backend's, after tax. Buying carries the review screen's idempotency key,
 * so a retry after a lost answer saves one purchase.
 */

export function toBond(view: BondOut): Bond {
  return {
    code: view.code,
    isin: view.isin,
    name: view.name,
    currency: view.currency,
    couponPercent: view.coupon_percent,
    issueDate: view.issue_date,
    maturityDate: view.maturity_date,
    years: view.years,
    nextCouponDate: view.next_coupon_date,
    couponsLeft: view.coupons_left,
    minimumFace: { currency: view.currency, minor: view.minimum_face_minor },
    faceStep: { currency: view.currency, minor: view.face_step_minor },
    price: view.price,
    // A draft never reaches a client.
    status: view.status === "live" ? "live" : "paused",
    canBuy: view.can_buy,
  };
}

export function toBondQuote(view: BondQuoteOut): BondQuote {
  const money = (minor: number) => ({ currency: view.currency, minor });
  return {
    code: view.code,
    face: money(view.face_minor),
    price: view.price,
    accrued: money(view.accrued_minor),
    cost: money(view.cost_minor),
    coupon: money(view.coupon_minor),
    couponsLeft: view.coupons_left,
    nextCouponDate: view.next_coupon_date,
    totalBack: money(view.total_back_minor),
    gain: money(view.gain_minor),
  };
}

export function toHolding(view: BondHoldingOut): BondHolding {
  const money = (minor: number) => ({ currency: view.currency, minor });
  return {
    id: view.id,
    code: view.code,
    name: view.name,
    face: money(view.face_minor),
    price: view.price,
    accrued: money(view.accrued_minor),
    cost: money(view.cost_minor),
    status: view.status,
    createdAt: view.created_at,
    payBy: view.pay_by,
    startDate: view.start_date,
    maturityDate: view.maturity_date,
    coupon: money(view.coupon_minor),
    couponsLeft: view.coupons_left,
    nextCouponDate: view.next_coupon_date,
    stillToCome: money(view.still_to_come_minor),
    couponDestinationId: view.payout_destination_id,
  };
}

/** The quote's and the purchase's refusals, by the code the API puts on the field. */
const REFUSALS: Record<string, BondsErrorCode> = {
  below_minimum: "below_minimum",
  not_a_step: "not_a_step",
  above_maximum: "above_maximum",
  amount_not_positive: "below_minimum",
  product_not_open: "not_open",
  currency_not_supported: "not_open",
  too_close_to_maturity: "too_close_to_maturity",
  no_price: "no_price",
};

/** Turns what the API or the network said into a reason a bond screen can show. */
export function toBondsError(error: unknown): BondsError {
  if (error instanceof BondsError) return error;
  if (!(error instanceof ApiError) || error.uncertain) return new BondsError("unavailable");
  switch (error.code) {
    case "not_found":
      return new BondsError("not_found");
    case "idempotency_key_reused":
      return new BondsError("key_reused");
    case "forbidden":
      // Nobody buys before their ID check is approved.
      return new BondsError("not_verified");
    case "conflict":
      if (isAccountClosing(error)) return new BondsError("account_closing");
      // Buying: five purchases waiting for payment already.
      return new BondsError("too_many_unpaid");
    case "validation_failed": {
      const found = error.fields.map((field) => REFUSALS[field.code]).find((code) => code !== undefined);
      // Still a definite refusal: nothing was saved, so the next try may take a new key.
      return new BondsError(found ?? "invalid");
    }
    default:
      return new BondsError("unavailable");
  }
}

async function wrap<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (error) {
    throw toBondsError(error);
  }
}

export function createApiBondsClient(): BondsClient {
  return {
    listBonds: () => wrap(async () => (await api("GET /client/bonds", {})).map(toBond)),

    getBond: (code) => wrap(async () => toBond(await api("GET /client/bonds/{code}", { params: { code } }))),

    quote: (code, face) =>
      wrap(async () => {
        const view = await api("GET /client/bonds/{code}/quote", {
          params: { code },
          query: { face_minor: face.minor },
        });
        // The issue's currency decides the quote's. A face value in another one was never asked for.
        if (view.currency !== face.currency) throw new BondsError("unavailable");
        return toBondQuote(view);
      }),

    buy: (request) =>
      wrap(async () =>
        toHolding(
          await api("POST /client/bond-holdings", {
            body: { code: request.code, face_minor: request.face.minor, currency: request.face.currency },
            idempotencyKey: request.idempotencyKey,
          }),
        ),
      ),

    listHoldings: () => wrap(async () => (await api("GET /client/bond-holdings", {})).map(toHolding)),

    getHolding: (holdingId) =>
      wrap(async () =>
        toHolding(await api("GET /client/bond-holdings/{holding_id}", { params: { holding_id: holdingId } })),
      ),
  };
}
