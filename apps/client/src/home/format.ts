import { Amount } from "@/investing/types";
import { formatAmount } from "@/investing/format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "9 Jan" from a calendar date. */
export function shortDay(isoDate: string): string {
  const [, month, day] = isoDate.split("-").map(Number);
  return `${day} ${MONTHS[(month ?? 1) - 1]}`;
}

/** "February" from a calendar date. */
export function monthName(isoDate: string): string {
  const month = Number(isoDate.split("-")[1]);
  return LONG_MONTHS[(month || 1) - 1] ?? "";
}

/** "10,724,760" for UGX 10,724,760: the figure without its currency, for a big number. */
export function figure(amount: Amount): string {
  return formatAmount(amount).replace(/^[A-Z]+ /, "");
}

/** Uganda keeps UTC+3 all year, so the greeting follows Kampala time wherever the phone is set. */
export function greeting(nowMs: number = Date.now()): string {
  const hour = new Date(nowMs + 3 * 60 * 60 * 1000).getUTCHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

export function plural(count: number, one: string, many: string = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** The bar width for days earned out of a term, as a percentage label for a style. */
export function termProgress(daysEarned: number, termDays: number): `${number}%` {
  if (termDays <= 0) return "0%";
  return `${Math.min(100, Math.max(0, Math.round((daysEarned / termDays) * 100)))}%`;
}
