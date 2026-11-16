import type {
  BondCouponOut,
  BondHoldingOut,
  BondOut,
  BondQuoteOut,
  BondRepaymentOut,
  BondSaleOut,
} from "@finvest/api-client/client";

import { api, ApiError, isAccountClosing } from "@/api/http";

import {
  Bond,
  BondCoupon,
  BondHolding,
  BondQuote,
  BondRepayment,
  BondSale,
  BondsClient,
  BondsError,
  BondsErrorCode,
  MoneyStatus,
} from "./types";

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

const moneyStatus = (status: string | null): MoneyStatus | null =>
  status === "paid_out" || status === "held" ? status : null;

export function toCoupon(view: BondCouponOut): BondCoupon {
  return {
    id: view.id,
    holdingId: view.holding_id,
    name: view.name,
    couponDate: view.coupon_date,
    amount: { currency: view.currency, minor: view.amount_minor },
    status: moneyStatus(view.status) ?? "held",
    payoutId: view.payout_id,
    heldBecause: view.held_because,
  };
}

export function toRepayment(view: BondRepaymentOut): BondRepayment {
  const money = (minor: number) => ({ currency: view.currency, minor });
  return {
    id: view.id,
    holdingId: view.holding_id,
    name: view.name,
    repaidOn: view.repaid_on,
    face: money(view.face_minor),
    coupon: money(view.coupon_minor),
    amount: money(view.amount_minor),
    status: moneyStatus(view.status) ?? "held",
    payoutId: view.payout_id,
    heldBecause: view.held_because,
  };
}

export function toSale(view: BondSaleOut): BondSale {
  return {
    id: view.id,
    holdingId: view.holding_id,
    name: view.name,
    face: { currency: view.currency, minor: view.face_minor },
    requestedAt: view.requested_at,
    status: view.status,
    destinationId: view.destination_id,
    price: view.price,
    soldOn: view.sold_on,
    amount: view.amount_minor === null ? null : { currency: view.currency, minor: view.amount_minor },
    payoutStatus: moneyStatus(view.payout_status),
    payoutId: view.payout_id,
    heldBecause: view.held_because,
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
  // Selling, by the code on `holding_id`.
  holding_not_active: "cannot_sell",
  matured: "cannot_sell",
  sale_already_asked: "cannot_sell",
  // Paying out, by the code on `destination_id`.
  not_usable: "account_not_ready",
  destination_in_safety_wait: "account_not_ready",
  destination_not_approved: "account_not_ready",
  destination_not_known_yet: "account_not_ready",
  over_mobile_money_limit: "over_limit",
  over_daily_limit: "over_limit",
  over_bank_limit: "over_limit",
};

/** What a 409 means, by the call that got it. */
type Call = "buy" | "sell" | "cancel" | "pay_out" | "other";
const CONFLICT: Record<Call, BondsErrorCode> = {
  // Five purchases waiting for payment already.
  buy: "too_many_unpaid",
  // An ask already waits for the holding.
  sell: "cannot_sell",
  // The desk priced it.
  cancel: "cannot_cancel",
  // Not waiting any more.
  pay_out: "already_sent",
  other: "invalid",
};

/** Turns what the API or the network said into a reason a bond screen can show. */
export function toBondsError(error: unknown, call: Call = "buy"): BondsError {
  if (error instanceof BondsError) return error;
  if (!(error instanceof ApiError) || error.uncertain) return new BondsError("unavailable");
  switch (error.code) {
    case "not_found":
      return new BondsError("not_found");
    case "pin_required":
      return new BondsError("pin_required");
    case "idempotency_key_reused":
      return new BondsError("key_reused");
    case "forbidden":
      // Nobody buys before their ID check is approved.
      return new BondsError("not_verified");
    case "conflict":
      if (isAccountClosing(error)) return new BondsError("account_closing");
      return new BondsError(CONFLICT[call]);
    case "validation_failed": {
      const found = error.fields.map((field) => REFUSALS[field.code]).find((code) => code !== undefined);
      // Still a definite refusal: nothing was saved, so the next try may take a new key.
      return new BondsError(found ?? "invalid");
    }
    default:
      return new BondsError("unavailable");
  }
}

async function wrap<T>(run: () => Promise<T>, call: Call = "buy"): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw toBondsError(error, call);
  }
}

const pinOf = (request: { pinConfirmation?: string | null }) =>
  request.pinConfirmation ? { pin_confirmation: request.pinConfirmation } : {};

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

    chooseCouponDestination: (holdingId, destinationId) =>
      wrap(
        async () =>
          toHolding(
            await api("PUT /client/bond-holdings/{holding_id}/coupon-destination", {
              params: { holding_id: holdingId },
              body: { destination_id: destinationId },
            }),
          ),
        "other",
      ),

    listCoupons: () => wrap(async () => (await api("GET /client/bond-coupons", {})).map(toCoupon), "other"),

    listRepayments: () => wrap(async () => (await api("GET /client/bond-repayments", {})).map(toRepayment), "other"),

    sell: (request) =>
      wrap(
        async () =>
          toSale(
            await api("POST /client/bond-holdings/{holding_id}/sale", {
              params: { holding_id: request.holdingId },
              body: { destination_id: request.destinationId, ...pinOf(request) },
              idempotencyKey: request.idempotencyKey,
            }),
          ),
        "sell",
      ),

    listSales: () => wrap(async () => (await api("GET /client/bond-sales", {})).map(toSale), "other"),

    cancelSale: (saleId) =>
      wrap(async () => toSale(await api("POST /client/bond-sales/{sale_id}/cancel", { params: { sale_id: saleId } })), "cancel"),

    payOutHeld: (request) =>
      wrap(async () => {
        const body = { destination_id: request.destinationId, ...pinOf(request) };
        if (request.kind === "coupon") {
          await api("POST /client/bond-coupons/{coupon_id}/pay-out", { params: { coupon_id: request.id }, body });
        } else if (request.kind === "repayment") {
          await api("POST /client/bond-repayments/{repayment_id}/pay-out", { params: { repayment_id: request.id }, body });
        } else {
          await api("POST /client/bond-sales/{sale_id}/pay-out", { params: { sale_id: request.id }, body });
        }
      }, "pay_out"),
  };
}
