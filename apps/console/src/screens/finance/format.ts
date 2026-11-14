import type { Amount, Currency } from "./api.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount: "UGX 4,674" or "$7.25". Integers throughout. */
export function formatAmount(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const sign = amount.minor < 0 ? "-" : "";
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${sign}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

/**
 * Reads "4,674" or "4674", and for dollars "7.25", as the smallest unit. Null
 * when it is anything else or not above zero. No floating point: the digits
 * are joined as text.
 */
export function parseAmount(text: string, currency: Currency): number | null {
  const plain = text.trim().replaceAll(",", "");
  const digits = MINOR_DIGITS[currency];
  const pattern = digits ? /^(\d+)(?:\.(\d{1,2}))?$/ : /^(\d+)$/;
  const found = pattern.exec(plain);
  if (!found) return null;
  const minor = Number(`${found[1]}${(found[2] ?? "").padEnd(digits, "0")}`);
  return Number.isSafeInteger(minor) && minor > 0 ? minor : null;
}

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** "October 2026" */
export function formatMonth(month: string): string {
  return MONTH.format(new Date(`${month}-01T00:00:00Z`));
}

/** "15 Nov 2026" */
export function formatDay(isoDate: string): string {
  return DAY.format(new Date(`${isoDate}T00:00:00Z`));
}
