/**
 * The website's "what you could earn" figure.
 *
 * It is an estimate, and the page says so. The exact figure comes from the backend's interest
 * formula, which works from terms the public site must not carry (docs/rules.md: clients never
 * see tax rates). The site only has the yearly rate after tax as a two decimal label, so the
 * estimate can differ from the real figure by a few shillings. The app shows the exact amount
 * before anyone pays.
 *
 * Whole numbers throughout: BigInt, no floating point.
 */

const DAYS_IN_YEAR = 365n;
/** Hundredths of one percent in a whole: "10.63" is 1063 of 10,000. */
const RATE_SCALE = 10_000n;

export interface Estimate {
  interest: bigint;
  total: bigint;
}

/** "10.63" as 1063. `null` if the label is not a rate. */
export function rateHundredths(label: string): bigint | null {
  const match = /^(\d{1,2})\.(\d{2})$/.exec(label);
  if (match === null) return null;
  return BigInt(`${match[1]}${match[2]}`);
}

/** Interest after tax on `amount` over `days`, rounded down to the smallest unit. */
export function estimate(amount: bigint, rateLabel: string, days: number): Estimate | null {
  const rate = rateHundredths(rateLabel);
  if (rate === null || amount <= 0n || !Number.isInteger(days) || days <= 0) return null;
  const interest = (amount * rate * BigInt(days)) / (RATE_SCALE * DAYS_IN_YEAR);
  return { interest, total: amount + interest };
}
