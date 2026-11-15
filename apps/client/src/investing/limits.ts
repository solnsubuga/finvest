import { formatAmount } from "@/investing/format";
import { Amount, Currency } from "@/investing/types";

/**
 * The largest single investment, and the largest goal target, Finvest accepts. The same figures
 * as `app/shared/limits.py` on the server, which refuses anything larger with `above_maximum`
 * (investments) or `target_too_large` (goals).
 */
export const LARGEST_SINGLE_AMOUNT: Record<Currency, Amount> = {
  UGX: { minor: 5_000_000_000, currency: "UGX" },
  USD: { minor: 150_000_000, currency: "USD" },
};

export function isAboveLargest(amount: Amount): boolean {
  return amount.minor > LARGEST_SINGLE_AMOUNT[amount.currency].minor;
}

export function largestSentence(currency: Currency): string {
  return `The most one investment can hold is ${formatAmount(LARGEST_SINGLE_AMOUNT[currency])}. Split a larger sum over several investments.`;
}
