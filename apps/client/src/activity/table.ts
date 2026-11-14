import { formatMoney } from "@/payments/format";

import { ActivityItem } from "./types";

const DAY_MS = 24 * 60 * 60 * 1000;
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;

/** True when the row happened in the last `days` days before `nowMs`. */
export function withinDays(item: ActivityItem, nowMs: number, days: number): boolean {
  return Date.parse(item.at) >= nowMs - days * DAY_MS;
}

/** The figure as the list shows it and as a client would type it: "1,000,000", "1000000" or "UGX 1,000,000". */
function amountForms(item: ActivityItem): string[] {
  const shown = formatMoney(item.amount);
  const figure = shown.replace(/^[A-Z]+ /, "");
  return [shown, figure, figure.replace(/,/g, "")];
}

/**
 * Whether a row matches what was typed in the search: any word of its title,
 * detail or status, or its amount with or without commas. Case does not matter.
 */
export function matches(item: ActivityItem, query: string): boolean {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return true;
  const haystack = [item.title, item.detail, item.statusLabel, ...amountForms(item)].join(" ").toLowerCase();
  return wanted.split(/\s+/).every((word) => haystack.includes(word));
}

/** "2026-11-13 15:32", the Kampala time, for a spreadsheet to sort by. */
export function kampalaStamp(iso: string): string {
  const at = new Date(Date.parse(iso) + KAMPALA_OFFSET_MS).toISOString();
  return `${at.slice(0, 10)} ${at.slice(11, 16)}`;
}

/** A CSV field, quoted when it holds a comma, a quote or a line break. */
function field(value: string): string {
  // A leading =, +, -, @, tab or carriage return would be read as a formula by a spreadsheet, so it is made plain text.
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/**
 * The rows as CSV, as the list shows them: one line each, newest first.
 * Amounts are the integer figures the API gave, written in whole units with
 * no thousands separator; nothing is added up.
 */
export function toCsv(items: ActivityItem[]): string {
  const lines = [["Date (Kampala)", "What", "For", "Direction", "Amount", "Currency", "Status"].join(",")];
  for (const item of items) {
    const amount = formatMoney(item.amount).replace(/^[A-Z]+ /, "").replace(/,/g, "");
    lines.push(
      [
        kampalaStamp(item.at),
        item.title,
        item.detail,
        item.direction === "in" ? "To you" : "From you",
        amount,
        item.amount.currency,
        item.statusLabel,
      ]
        .map(field)
        .join(","),
    );
  }
  return `${lines.join("\r\n")}\r\n`;
}
