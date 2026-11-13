/**
 * How figures are written on the site. Whole-number arithmetic only: amounts are counts of
 * the smallest unit and are never turned into floating point.
 */

import type { Currency, InterestPaid, Product, ProductKind } from "./products.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function grouped(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "UGX 100,000", "$500", "$1,250.50". */
export function formatMoney(minor: bigint | number, currency: Currency): string {
  const value = BigInt(minor);
  const digits = MINOR_DIGITS[currency];
  const scale = 10n ** BigInt(digits);
  const whole = grouped((value / scale).toString());
  const part = value % scale;
  const cents = part === 0n ? "" : `.${part.toString().padStart(digits, "0")}`;
  return currency === "USD" ? `$${whole}${cents}` : `UGX ${whole}`;
}

/** "1,000,000" for the amount box. */
export function formatWhole(value: bigint): string {
  return grouped(value.toString());
}

/** The whole number someone typed, ignoring commas and spaces. `null` if it is not one. */
export function parseWhole(text: string): bigint | null {
  const digits = text.replace(/[\s,]/g, "");
  if (!/^\d{1,13}$/.test(digits)) return null;
  return BigInt(digits);
}

/** "13 November 2026" from "2026-11-13". */
export function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  const name = month === undefined ? undefined : MONTHS[month - 1];
  if (year === undefined || date === undefined || name === undefined) return day;
  return `${date} ${name} ${year}`;
}

/** The calendar day `days` after `start` (YYYY-MM-DD), as YYYY-MM-DD. */
export function addDays(start: string, days: number): string {
  const [year, month, date] = start.split("-").map(Number) as [number, number, number];
  const moved = new Date(Date.UTC(year, month - 1, date + days));
  return moved.toISOString().slice(0, 10);
}

/** Today's date in Kampala, where Finvest's day begins and ends. */
export function todayInKampala(now: Date = new Date()): string {
  const kampala = new Date(now.getTime() + 3 * 60 * 60 * 1000);
  return kampala.toISOString().slice(0, 10);
}

export function kindLabel(kind: ProductKind, currency: Currency): string {
  if (kind === "treasury_bill") return "Treasury bill";
  return currency === "USD" ? "Fixed income, USD" : "Fixed income";
}

export function termLabel(termDays: number | null): string {
  return termDays === null ? "Open, no fixed term" : `${termDays} days`;
}

export function interestPaidLabel(paid: InterestPaid): string {
  return paid === "at_maturity" ? "At maturity" : "Monthly";
}

export function minimumLabel(product: Product): string {
  return formatMoney(product.minimumMinor, product.currency);
}
