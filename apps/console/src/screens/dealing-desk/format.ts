import type { Amount, BufferLevel, Cover, Currency, OrderKind, OrderStatus } from "./api.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount: "UGX 240,000,000" or "$362,000.00". Used wherever a person checks a figure. */
export function formatExact(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const sign = amount.minor < 0 ? "-" : "";
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${sign}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

/**
 * A figure for reading at a glance, as on the canvas: shillings in millions to
 * one decimal ("7,412.0m"), dollars to the whole dollar. Labels only. Nothing is
 * worked out from these.
 */
export function formatShort(amount: Amount, withCurrency = true): string {
  if (amount.currency === "USD") return `$${GROUPED.format(Math.round(amount.minor / 100))}`;
  const tenths = Math.round(amount.minor / 100_000);
  const text = `${GROUPED.format(Math.trunc(tenths / 10))}.${Math.abs(tenths % 10)}m`;
  return withCurrency ? `UGX ${text}` : text;
}

/** Reads "240,000,000" or "240000000" as whole major units. Null when it is anything else. */
export function parseAmount(text: string, currency: Currency): Amount | null {
  const plain = text.trim();
  if (!/^(\d{1,3}(,\d{3})+|\d+)$/.test(plain)) return null;
  const major = Number(plain.replaceAll(",", ""));
  const minor = major * 10 ** MINOR_DIGITS[currency];
  return Number.isSafeInteger(minor) && minor > 0 ? { minor, currency } : null;
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const DAY_WITH_YEAR = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

/** "Wed 18 Nov" */
export function formatAuctionDay(isoDate: string): string {
  return DAY.format(new Date(`${isoDate}T00:00:00Z`)).replace(",", "");
}

/** "18 Feb 2027" */
export function formatDay(isoDate: string): string {
  return DAY_WITH_YEAR.format(new Date(`${isoDate}T00:00:00Z`));
}

/** The Kampala time of day: "14:20" */
export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export const COVER_LABELS: Record<Cover, string> = {
  covered: "Covered",
  bid_needed: "Bid needed",
  on_order: "On order",
  surplus: "Surplus",
};

export const KIND_LABELS: Record<OrderKind, string> = {
  auction_bid: "Auction bid",
  secondary_purchase: "Secondary market purchase",
  secondary_sale: "Secondary market sale",
};

export const STATUS_LABELS: Record<OrderStatus, string> = {
  prepared: "Waiting for a second person",
  approved: "Approved, waiting for the result",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
  result_recorded: "Result waiting to be confirmed",
  settled: "Settled",
  unsuccessful: "Unsuccessful",
};

export function bufferNote(level: BufferLevel, target: Amount, floor: Amount): string {
  if (level === "on_target") return `Target ${formatShort(target, false)}, on target`;
  if (level === "under_target") return `Target ${formatShort(target, false)}, under target`;
  return `Below the floor of ${formatShort(floor, false)}. Small withdrawals are queued.`;
}
