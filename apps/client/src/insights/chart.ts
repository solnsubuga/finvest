import { Money } from "@/payments/types";

import { Currency, InsightsMonth } from "./types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FULL_MONTHS = [
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

export function monthShort(month: string): string {
  return MONTHS[Number(month.slice(5, 7)) - 1] ?? "";
}

export function monthLong(month: string): string {
  return FULL_MONTHS[Number(month.slice(5, 7)) - 1] ?? "";
}

/**
 * "31.4": thousands of shillings with one decimal place, for a bar label.
 * Worked on the integer, so no amount passes through a float that could
 * round it the wrong way.
 */
export function thousands(minor: number): string {
  const tenths = Math.floor(minor / 100);
  return `${Math.floor(tenths / 10).toLocaleString("en-US")}.${tenths % 10}`;
}

/**
 * "12.50": whole dollars and cents, for a bar label. Moved digit by digit from the
 * integer number of cents, like `thousands`.
 */
export function dollars(cents: number): string {
  const digits = String(Math.abs(cents)).padStart(3, "0");
  const whole = Number(digits.slice(0, -2)).toLocaleString("en-US");
  return `${cents < 0 ? "-" : ""}${whole}.${digits.slice(-2)}`;
}

/** A bar's label in its own currency's unit. */
export function barValue(money: Money): string {
  return money.currency === "USD" ? dollars(money.minor) : thousands(money.minor);
}

/** What the bar labels count, for the line under the chart. */
export const barUnit: Record<Currency, string> = {
  UGX: "Thousands of shillings",
  USD: "Dollars",
};

export type Bar = {
  month: string;
  label: string;
  value: string;
  height: number;
  current: boolean;
};

/** Bars for interest earned each month, scaled so the tallest is `maxHeight`. */
export function earnedBars(months: InsightsMonth[], maxHeight: number): Bar[] {
  const top = Math.max(0, ...months.map((month) => month.earned.minor));
  return months.map((month, index) => ({
    month: month.month,
    label: monthShort(month.month),
    value: barValue(month.earned),
    // A month with nothing earned still shows a sliver, so the axis reads.
    height: top === 0 ? 4 : Math.max(4, Math.round((month.earned.minor / top) * maxHeight)),
    current: index === months.length - 1,
  }));
}

/** Whole days from `today` to `day`, both YYYY-MM-DD. */
export function daysUntil(day: string, today: string): number {
  return Math.round(
    (Date.parse(`${day}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000,
  );
}
