/**
 * The console's view of clients' bonds sold early through the dealing desk.
 *
 * The shapes follow `/staff/bond-sales` in backend/app/modules/bonds/api.py and docs/bonds.md.
 * A client asks to sell a whole holding; one member of staff records the price the desk sold at
 * and the day it settled; a different one approves it or sends it back; the worker pays the
 * client. The console never works out a money figure or who may act: every amount, status and
 * allowed step comes from the backend. Forms check what was typed only to save a round trip.
 */

import type { Amount, Currency } from "./api.ts";

export type SaleStatus = "requested" | "priced" | "approved" | "sold" | "cancelled" | "closed";
export type SaleStep = "price" | "approve" | "send_back" | "withdraw";

export interface BondSale {
  id: string;
  holdingId: string;
  clientId: string;
  /** The client's name, or their ID when the directory does not know it. */
  clientName: string;
  code: string;
  name: string;
  currency: Currency;
  face: Amount;
  /** What the client paid for the holding. */
  cost: Amount;
  requestedAt: string;
  status: SaleStatus;
  /** Per 100 of face value, as an integer: 98.5 is 985000. */
  price: number | null;
  soldOn: string | null;
  /** The desk's reference for the trade. */
  reason: string | null;
  pricedBy: string | null;
  pricedAt: string | null;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was last sent back. */
  note: string | null;
  clean: Amount | null;
  accrued: Amount | null;
  tax: Amount | null;
  /** What the client was paid: the price and accrued interest, less the tax. */
  amount: Amount | null;
  soldAt: string | null;
  payoutStatus: "paid_out" | "held" | null;
  heldBecause: string | null;
  allowed: readonly SaleStep[];
}

export interface SalePrice {
  price: number;
  soldOn: string;
  reason: string;
}

export interface BondSalesApi {
  /** Newest first. */
  list(): Promise<BondSale[]>;
  price(saleId: string, price: SalePrice): Promise<BondSale>;
  approve(saleId: string): Promise<BondSale>;
  sendBack(saleId: string, note: string): Promise<BondSale>;
  withdraw(saleId: string): Promise<BondSale>;
}

export type BondSaleErrorCode =
  | "not_found"
  | "conflict"
  | "forbidden"
  | "sold_on_in_future"
  | "sold_on_before_start"
  | "sold_on_at_maturity"
  | "coupon_since_sale"
  | "price_out_of_range"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class BondSaleError extends Error {
  readonly code: BondSaleErrorCode;

  constructor(code: BondSaleErrorCode) {
    super(code);
    this.name = "BondSaleError";
    this.code = code;
  }
}

const MESSAGES: Record<BondSaleErrorCode, string> = {
  not_found: "This sale no longer exists.",
  conflict: "Someone else has already moved this sale on, or the bond matured. The list has been reloaded.",
  forbidden: "A second person has to do this, and you may not approve a sale you priced.",
  sold_on_in_future: "Record a sale once it has settled, on or before today.",
  sold_on_before_start: "The sale settled before the client's holding started. Check the day.",
  sold_on_at_maturity: "The bond matures on that day, so it is repaid, not sold.",
  coupon_since_sale:
    "A coupon date has passed since that day, and the holding was paid that coupon. Send it back, and record a sale from the current coupon period.",
  price_out_of_range: "A price is between 50 and 150 per 100 of face value.",
  invalid: "Check the figures and try again.",
  not_connected: "Bond sales are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bondSaleErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BondSaleError ? error.code : "unavailable"];
}

export const STATUS_LABELS: Record<SaleStatus, string> = {
  requested: "Waiting for the desk to sell",
  priced: "Waiting for a second person",
  approved: "Approved, being paid",
  sold: "Sold",
  cancelled: "Cancelled by the client",
  closed: "Closed, the bond matured first",
};

const notConnected = () => Promise.reject(new BondSaleError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedBondSalesApi: BondSalesApi = {
  list: notConnected,
  price: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
