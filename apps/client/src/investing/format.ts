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

export const interestPaidLabel: Record<Product["interestPaid"], string> = {
  at_maturity: "Interest paid at maturity",
  monthly: "Interest paid monthly",
};

export const kindLabel: Record<Product["kind"], string> = {
  treasury_bill: "Treasury bill",
  fixed_income: "Fixed income",
};

export const atMaturityLabel: Record<AtMaturity, string> = {
  roll_over: "Roll over",
  pay_out: "Pay out",
  pay_interest: "Pay me the interest",
  interest_to_goal: "Save the interest to a goal",
};

export const atMaturityDetail: Record<AtMaturity, string> = {
  roll_over: "Start a new term with everything, at the rate on that day.",
  pay_out: "Send everything to your mobile money or bank account.",
  pay_interest: "Keep what you put in invested for another term, and send the interest to your mobile money or bank account. Every term, until you change it.",
  interest_to_goal: "Keep what you put in invested for another term, and save the interest towards one of your goals. Every term, until you change it.",
};

export const statusLabel: Record<InvestmentStatus, string> = {
  awaiting_payment: "Waiting for payment",
  active: "Running",
  matured: "Matured",
  cancelled: "Not paid in time",
  withdrawn: "Withdrawn early",
  // The API never sends one to a client; the label is here so every status has one.
  unwound: "Reversed",
  transferred: "Sent to another client",
};

/**
 * What an investment shows as its state. Once an early withdrawal is asked for it reads
 * "Withdrawn early", though the API keeps it `active` until the money has moved.
 */
export function investmentLabel(investment: { status: InvestmentStatus; withdrawalRequestedAt: string | null }): string {
  return investment.withdrawalRequestedAt ? statusLabel.withdrawn : statusLabel[investment.status];
}

/**
 * How long the interest shown has been earning. A part kept after sending some of an investment
 * carries on its term, so its days count from the term's start, but its interest so far is only
 * its own: say the day it began instead of a count of days.
 */
export function earnedLabel(investment: { daysEarned: number; termDays: number | null; startDate: string | null; maturityDate: string | null }): string {
  if (investment.startDate && investment.maturityDate && investment.termDays) {
    const termStart = new Date(Date.parse(`${investment.maturityDate}T00:00:00Z`) - investment.termDays * 86_400_000)
      .toISOString()
      .slice(0, 10);
    if (termStart < investment.startDate) return `earning since ${formatDay(investment.startDate)}`;
  }
  return `${investment.daysEarned} days earned`;
}
