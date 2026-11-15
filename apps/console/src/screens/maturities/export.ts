/**
 * The day by day maturities as a CSV file for a spreadsheet. Only totals per day and currency:
 * no client, account or person is in it, so it needs no audit record of its own.
 */

import type { MaturityDay } from "./api.ts";

const HEADINGS = [
  "Day",
  "Currency",
  "Terms",
  "Principal (smallest unit)",
  "Rolled over",
  "Paid out",
  "Held",
  "Still to roll over",
  "Still to pay out",
] as const;

export function maturitiesCsv(days: readonly MaturityDay[]): string {
  const lines = [HEADINGS.join(",")];
  for (const d of days) {
    // Every cell is a date, a currency code or a whole number, so none needs quoting.
    lines.push(
      [d.day, d.currency, d.investments, d.principal.minor, d.rolledOver, d.paidOut, d.held, d.openRollOver, d.openPayOut].join(","),
    );
  }
  return `${lines.join("\r\n")}\r\n`;
}

export function maturitiesFilename(today: string): string {
  return `finvest-maturities-${today}.csv`;
}

/** What ends after today, per currency: terms, principal, and what clients chose for them. */
export interface ComingUp {
  currency: MaturityDay["currency"];
  terms: number;
  principal: number;
  rollOver: number;
  payOut: number;
}

export function comingUp(days: readonly MaturityDay[], today: string): ComingUp[] {
  const sums = new Map<string, ComingUp>();
  for (const d of days) {
    if (d.day <= today) continue;
    const sum = sums.get(d.currency) ?? { currency: d.currency, terms: 0, principal: 0, rollOver: 0, payOut: 0 };
    sum.terms += d.investments;
    sum.principal += d.principal.minor;
    sum.rollOver += d.openRollOver;
    sum.payOut += d.openPayOut;
    sums.set(d.currency, sum);
  }
  return [...sums.values()];
}
