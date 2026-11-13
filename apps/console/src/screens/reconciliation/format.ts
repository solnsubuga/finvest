import type { Amount, Currency, RefuseReason, Stage } from "./api.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount: "UGX 2,500,000". A person checks a transfer against it, so nothing is rounded. */
export function formatExact(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${amount.minor < 0 ? "-" : ""}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

/** A calendar date: "Thu 12 Nov 2026". */
export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

/** A moment, in Kampala time: "13 Nov, 14:20". */
export function formatMoment(moment: string): string {
  return TIME.format(new Date(moment));
}

/** "Sent 1 day before" for a statement line against the day the client gave. */
export function daysApart(sentOn: string, valueDate: string): string {
  const days = Math.round((Date.parse(`${valueDate}T00:00:00Z`) - Date.parse(`${sentOn}T00:00:00Z`)) / 86_400_000);
  if (days === 0) return "Same day as sent";
  if (days < 0) return days === -1 ? "1 day before the day sent" : `${-days} days before the day sent`;
  return days === 1 ? "1 day after sent" : `${days} days after sent`;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(Math.round((bytes / (1024 * 1024)) * 10) / 10).toFixed(1)} MB`;
}

export const STAGE_LABELS: Record<Stage, string> = {
  to_match: "Needs matching",
  to_approve: "Needs a second person",
  confirming: "Approved, confirming",
  confirmed: "Confirmed",
  refused: "Refused",
};

/** The reasons a person can give, worded as staff would say them. The client sees their own wording. */
export const REFUSE_REASONS: Record<RefuseReason, string> = {
  not_on_statement: "Not on any bank statement",
  amount_differs: "The amount received is different",
  name_differs: "Sent from an account in another name",
  unreadable: "The receipt cannot be read",
  other: "Another reason",
};
