import type { Amount, Currency, InterestPaid, ProductAction, ProductKind, ProductStatus, ProposalStatus } from "./api.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount: "UGX 1,000,000" or "$500.00". */
export function formatExact(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const sign = amount.minor < 0 ? "-" : "";
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${sign}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

/** Hundredths of a percent as a label: 1250 is "12.5%", 1375 is "13.75%", 1500 is "15%". */
export function formatBps(bps: number): string {
  const whole = Math.trunc(bps / 100);
  const hundredths = String(bps % 100).padStart(2, "0").replace(/0+$/, "");
  return hundredths ? `${whole}.${hundredths}%` : `${whole}%`;
}

/**
 * Reads a typed percentage, "12.5" or "12.50", as hundredths of a percent.
 * Text only, never floating point. Null when it is not a number with at most two decimals.
 */
export function parsePercent(text: string): number | null {
  const match = /^(\d{1,2})(?:\.(\d{1,2}))?$/.exec(text.trim().replace(/%$/, "").trim());
  if (!match) return null;
  const whole = Number(match[1]);
  const fraction = Number((match[2] ?? "").padEnd(2, "0"));
  return whole * 100 + fraction;
}

/**
 * A preview of the rate a client would see, rounded as the backend rounds it.
 * Only a label while the form is being filled in: once saved, the backend's own figure is shown.
 */
export function previewClientRate(annualRateBps: number, taxBps: number): string {
  const millionths = annualRateBps * (10_000 - taxBps);
  let hundredths = Math.trunc(millionths / 10_000);
  if ((millionths % 10_000) * 2 >= 10_000) hundredths += 1;
  return `${Math.trunc(hundredths / 100)}.${String(hundredths % 100).padStart(2, "0")}%`;
}

const DAY_WITH_YEAR = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const STAMP = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Africa/Kampala",
});

/** "18 Feb 2027" */
export function formatDay(isoDate: string): string {
  return DAY_WITH_YEAR.format(new Date(`${isoDate}T00:00:00Z`));
}

/** A moment in Kampala time: "13 Nov, 14:20" */
export function formatStamp(iso: string): string {
  return STAMP.format(new Date(iso));
}

export function formatTerm(termDays: number | null): string {
  return termDays === null ? "Open term" : `${termDays} days`;
}

export const KIND_LABELS: Record<ProductKind, string> = {
  treasury_bill: "Treasury bill",
  fixed_income: "Fixed income",
};

export const PRODUCT_STATUS_LABELS: Record<ProductStatus, string> = {
  draft: "Draft",
  live: "Live",
  paused: "Paused",
  closed: "Closed",
};

export const INTEREST_PAID_LABELS: Record<InterestPaid, string> = {
  at_maturity: "At maturity",
  monthly: "Monthly",
};

export const PROPOSAL_STATUS_LABELS: Record<ProposalStatus, string> = {
  proposed: "Waiting for approval",
  approved: "Approved",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
};

/**
 * Reads a typed amount in whole units, "100,000" or "500.00", as the smallest unit.
 * Text only, never floating point. Null when it is not a positive amount the currency can hold.
 */
export function parseAmount(text: string, currency: Currency): number | null {
  const digits = MINOR_DIGITS[currency];
  const cleaned = text.trim().replace(/,/g, "");
  const match = digits ? /^(\d{1,13})(?:\.(\d{1,2}))?$/.exec(cleaned) : /^(\d{1,15})$/.exec(cleaned);
  if (!match) return null;
  const whole = Number(match[1]);
  const fraction = digits ? Number((match[2] ?? "").padEnd(digits, "0")) : 0;
  const minor = whole * 10 ** digits + fraction;
  return minor > 0 && Number.isSafeInteger(minor) ? minor : null;
}

/** The smallest unit as text a person types: "100000" or "500.00". */
export function amountInput(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  if (!digits) return String(amount.minor);
  const unit = 10 ** digits;
  return `${Math.trunc(amount.minor / unit)}.${String(amount.minor % unit).padStart(digits, "0")}`;
}

export const ACTION_LABELS: Record<ProductAction, string> = {
  create: "New product",
  edit: "New terms",
  pause: "Pause",
  open: "Open to clients",
};

export const CURRENCY_LABELS: Record<Currency, string> = { UGX: "Uganda shillings (UGX)", USD: "US dollars (USD)" };
