/**
 * The console's view of bond issues: Uganda government bonds as Bank of Uganda sells them, each
 * with its ISIN, coupon, dates and price, and the changes one person proposes and another
 * approves. See docs/bonds.md.
 *
 * `http.ts` calls the staff routes in backend/app/modules/bonds/api.py (`/staff/bonds`,
 * `/staff/bond-changes`); `stub.ts` stands in for them under `pnpm dev` and previews.
 */

import type { Amount, Currency, ProposalStatus } from "../api.ts";

export type BondStatus = "draft" | "live" | "paused";
export type BondAction = "create" | "price" | "pause" | "open";

/** A price per 100 of face value to four decimal places: 98.5 is 985000, par is 1000000. */
export type Price = number;

export interface Bond {
  code: string;
  isin: string;
  name: string;
  currency: Currency;
  /** The yearly coupon before tax, in hundredths of a percent: 1600 is 16%. */
  couponBps: number;
  /** Withholding tax on coupons, in hundredths of a percent. */
  taxBps: number;
  /** Bank of Uganda's tax for a bond of this length, for comparison. */
  usualTaxBps: number;
  issueDate: string;
  maturityDate: string;
  years: number;
  minimumFace: Amount;
  faceStep: Amount;
  status: BondStatus;
  /** Null until the first price is approved. */
  price: Price | null;
  pricedAt: string | null;
}

export interface BondTerms {
  isin: string;
  name: string;
  currency: Currency;
  couponBps: number;
  taxBps: number;
  issueDate: string;
  maturityDate: string;
  minimumFace: Amount;
  faceStep: Amount;
}

export interface BondChange {
  id: string;
  action: BondAction;
  code: string;
  /** A new issue as it will be. Null for a price, pause or opening. */
  terms: BondTerms | null;
  price: Price | null;
  reason: string;
  status: ProposalStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
}

export interface NewBondDraft {
  code: string;
  isin: string;
  name: string;
  couponBps: number;
  taxBps: number;
  issueDate: string;
  maturityDate: string;
  minimumFaceMinor: number;
  faceStepMinor: number;
  price: Price;
  reason: string;
}

export interface BondsApi {
  listBonds(): Promise<readonly Bond[]>;
  /** Newest first. With a status, only changes in that status. */
  listChanges(status?: ProposalStatus): Promise<readonly BondChange[]>;
  proposeBond(draft: NewBondDraft): Promise<BondChange>;
  proposePrice(code: string, price: Price, reason: string): Promise<BondChange>;
  /** `live` opens an issue to clients, `paused` stops new buying. */
  proposeStatus(code: string, to: "live" | "paused", reason: string): Promise<BondChange>;
  approve(changeId: string): Promise<BondChange>;
  sendBack(changeId: string, note: string): Promise<BondChange>;
  withdraw(changeId: string): Promise<BondChange>;
}

export type BondsErrorCode =
  | "reason_required"
  | "code_taken"
  | "isin_taken"
  | "same_price"
  | "out_of_range"
  | "already_live"
  | "not_live"
  | "no_price"
  | "matured"
  | "currency_not_open"
  | "stale"
  | "invalid"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class BondsError extends Error {
  readonly code: BondsErrorCode;

  constructor(code: BondsErrorCode) {
    super(code);
    this.name = "BondsError";
    this.code = code;
  }
}

const MESSAGES: Record<BondsErrorCode, string> = {
  reason_required: "Say why. The reason goes in the audit trail.",
  code_taken: "A bond with this code already exists or is waiting for approval. Pick another code.",
  isin_taken: "This ISIN is already in the catalogue or waiting for approval.",
  same_price: "That is the price already in force.",
  out_of_range: "A price is between 50 and 150 per 100 of face value.",
  already_live: "This bond is already open to clients.",
  not_live: "Only a bond open to clients can be paused.",
  no_price: "This bond has no price yet. Approve a price before opening it.",
  matured: "This bond is in its last coupon period, so clients can no longer buy it.",
  currency_not_open: "Finvest cannot take payments or pay out in this currency yet.",
  stale: "The bond changed after this was proposed. Propose it again.",
  invalid: "Finvest did not accept these details. Check the ISIN, dates and amounts.",
  conflict: "Someone else has decided this change, or another change for this bond is waiting. The list has been reloaded.",
  not_found: "This bond or change no longer exists.",
  forbidden: "A second person has to approve this, or you are not allowed to make this change.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_connected: "Bonds are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bondsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BondsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new BondsError("not_connected"));

export const notConnectedBondsApi: BondsApi = {
  listBonds: notConnected,
  listChanges: notConnected,
  proposeBond: notConnected,
  proposePrice: notConnected,
  proposeStatus: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};

/** 985000 as "98.5000". */
export function formatPrice(price: Price): string {
  return `${Math.trunc(price / 10_000)}.${String(price % 10_000).padStart(4, "0")}`;
}

/** Reads a typed price, "98.5" or "101.1250", as an integer. Null when it is not one. */
export function parsePrice(text: string): Price | null {
  const match = /^(\d{1,3})(?:\.(\d{1,4}))?$/.exec(text.trim());
  if (!match) return null;
  const price = Number(match[1]) * 10_000 + Number((match[2] ?? "").padEnd(4, "0"));
  return price >= 500_000 && price <= 1_500_000 ? price : null;
}

/** Bank of Uganda's withholding tax: 20% under ten years, 10% at ten years (of 364 days) or more. */
export function usualTaxBps(issueDate: string, maturityDate: string): number {
  const days = (Date.parse(`${maturityDate}T00:00:00Z`) - Date.parse(`${issueDate}T00:00:00Z`)) / 86_400_000;
  return days >= 10 * 364 ? 1000 : 2000;
}

/** The yearly coupon after tax, as clients see it, rounded down: "14.40%". */
export function clientCoupon(couponBps: number, taxBps: number): string {
  const hundredths = Math.trunc((couponBps * (10_000 - taxBps)) / 10_000);
  return `${Math.trunc(hundredths / 100)}.${String(hundredths % 100).padStart(2, "0")}%`;
}
