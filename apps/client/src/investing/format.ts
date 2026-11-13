import { Amount, AtMaturity, InvestmentStatus, Product } from "./types";

const DIGITS: Record<Amount["currency"], number> = { UGX: 0, USD: 2 };

/** "UGX 1,000,000" or "USD 500.00". Works on the integer, so no amount passes through a fraction. */
export function formatAmount(amount: Amount): string {
  if (!Number.isSafeInteger(amount.minor)) {
    throw new Error("An amount must be a whole number of the smallest unit");
  }
  const digits = DIGITS[amount.currency];
  const size = Math.abs(amount.minor);
  const unit = 10 ** digits;
  const whole = String(Math.floor(size / unit)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = digits ? `.${String(size % unit).padStart(digits, "0")}` : "";
  return `${amount.currency} ${amount.minor < 0 ? "-" : ""}${whole}${fraction}`;
}

/** "1,000,000" for UGX 1,000,000 or "500" for USD 500.00: the whole units, as the amount field takes them. */
export function wholeUnits(amount: Amount): string {
  return formatAmount(amount)
    .replace(/^[A-Z]+ /, "")
    .replace(/\.\d+$/, "");
}

/**
 * Reads what the client typed as whole units: "1,000,000" or "1000000".
 * Digits only, so no decimal ever reaches an amount. Null when it is not a whole number.
 */
export function parseWholeAmount(text: string, currency: Amount["currency"]): Amount | null {
  const plain = text.replace(/[,\s]/g, "");
  if (!/^\d{1,12}$/.test(plain)) {
    return null;
  }
  const minor = Number(plain) * 10 ** DIGITS[currency];
  return Number.isSafeInteger(minor) && minor > 0 ? { currency, minor } : null;
}

/** "1,000,000" for the amount field. */
export function groupDigits(text: string): string {
  const plain = text.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
  return plain.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "18 Feb 2027" from a calendar date. */
export function formatDay(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  return `${day} ${MONTHS[(month ?? 1) - 1]} ${year}`;
}

export function termLabel(product: Pick<Product, "termDays">): string {
  return product.termDays === null ? "Open term" : `${product.termDays} days`;
}

export const kindLabel: Record<Product["kind"], string> = {
  treasury_bill: "Treasury bill",
  fixed_income: "Fixed income",
};

export const atMaturityLabel: Record<AtMaturity, string> = {
  roll_over: "Roll over",
  pay_out: "Pay out",
};

export const atMaturityDetail: Record<AtMaturity, string> = {
  roll_over: "Start a new term with everything, at the rate on that day.",
  pay_out: "Send everything to your mobile money or bank account.",
};

export const statusLabel: Record<InvestmentStatus, string> = {
  awaiting_payment: "Waiting for payment",
  active: "Running",
  matured: "Matured",
  cancelled: "Not paid in time",
  withdrawn: "Withdrawn early",
};
