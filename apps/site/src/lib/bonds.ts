/**
 * The bond issues Finvest offers, as the website shows them, and the check on what the API
 * sends (`GET /public/bonds`).
 *
 * Only figures a client may see: the coupon after tax, never the coupon before tax or the tax
 * rate (docs/rules.md). Amounts are whole numbers in the smallest unit.
 */

import type { Currency } from "./products.ts";

export type BondStatus = "live" | "paused";

export interface Bond {
  code: string;
  isin: string;
  name: string;
  currency: Currency;
  /** The yearly coupon after tax, as a label: "12.80". */
  couponPercent: string;
  maturityDate: string;
  years: number;
  nextCouponDate: string | null;
  minimumFaceMinor: number;
  faceStepMinor: number;
  /** Per 100 of face value, four decimal places: 1,000,000 is 100. `null` until set. */
  price: number | null;
  status: BondStatus;
  canBuy: boolean;
}

export interface BondList {
  updatedOn: string;
  bonds: Bond[];
}

const CODE = /^[A-Z0-9-]{1,20}$/;
const ISIN = /^[A-Z]{2}[A-Z0-9]{9}\d$/;
const PERCENT = /^\d{1,2}\.\d{2}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MAX_BONDS = 20;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function wholeNumber(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
}

function day(value: unknown): value is string {
  return typeof value === "string" && DAY.test(value);
}

function parseBond(value: unknown): Bond | null {
  if (!isRecord(value)) return null;
  const { code, isin, name, currency, years, price, status } = value;
  const coupon = value["coupon_percent"];
  const matures = value["maturity_date"];
  const next = value["next_coupon_date"];
  const minimum = value["minimum_face_minor"];
  const step = value["face_step_minor"];
  const canBuy = value["can_buy"];
  if (typeof code !== "string" || !CODE.test(code)) return null;
  if (typeof isin !== "string" || !ISIN.test(isin)) return null;
  if (typeof name !== "string" || name.trim() === "" || name.length > 80) return null;
  if (currency !== "UGX" && currency !== "USD") return null;
  if (typeof coupon !== "string" || !PERCENT.test(coupon)) return null;
  if (!day(matures) || (next !== null && !day(next))) return null;
  if (!wholeNumber(years, 0, 50)) return null;
  if (!wholeNumber(minimum, 1, Number.MAX_SAFE_INTEGER)) return null;
  if (!wholeNumber(step, 1, Number.MAX_SAFE_INTEGER)) return null;
  if (price !== null && !wholeNumber(price, 1, 10_000_000)) return null;
  if (status !== "live" && status !== "paused") return null;
  if (typeof canBuy !== "boolean") return null;
  return {
    code,
    isin,
    name: name.trim(),
    currency,
    couponPercent: coupon,
    maturityDate: matures,
    years,
    nextCouponDate: next,
    minimumFaceMinor: minimum,
    faceStepMinor: step,
    price,
    status,
    canBuy,
  };
}

/**
 * Reads the API's answer. Returns `null` unless every part of it is what the site expects, so
 * a half-right answer never shows. An empty list is an answer: no bonds today.
 */
export function parseBonds(value: unknown): BondList | null {
  if (!isRecord(value)) return null;
  const updated = value["updated_on"];
  const bonds = value["bonds"];
  if (!day(updated)) return null;
  if (!Array.isArray(bonds) || bonds.length > MAX_BONDS) return null;
  const parsed: Bond[] = [];
  const seen = new Set<string>();
  for (const item of bonds) {
    const bond = parseBond(item);
    if (bond === null || seen.has(bond.code)) return null;
    seen.add(bond.code);
    parsed.push(bond);
  }
  return { updatedOn: updated, bonds: parsed };
}

/** "98.9450" per 100 of face value, from 989,450: exact, as the app shows it. */
export function priceLabel(price: number): string {
  return `${Math.floor(price / 10_000)}.${String(price % 10_000).padStart(4, "0")}`;
}
