/**
 * Shares are whole basis points, and clients type them as a percentage with
 * at most two decimals. These convert without floating point, so "33.33"
 * is exactly 3,333 and never 3,332.9999.
 */

export function percentToBps(text: string): number | null {
  const match = /^(\d{1,3})(?:[.,](\d{1,2}))?$/.exec(text.trim().replace(/%$/, "").trim());
  if (!match) return null;
  const whole = Number(match[1]);
  const fraction = Number((match[2] ?? "").padEnd(2, "0"));
  const bps = whole * 100 + fraction;
  return bps >= 1 && bps <= 10_000 ? bps : null;
}

export function bpsToPercent(bps: number): string {
  const whole = Math.floor(bps / 100);
  const rest = bps % 100;
  return rest === 0 ? `${whole}%` : `${whole}.${String(rest).padStart(2, "0").replace(/0$/, "")}%`;
}

/** Splits 100% evenly between `count` people, giving any spare basis points to the first. */
export function evenShares(count: number): number[] {
  if (count <= 0) return [];
  const each = Math.floor(10_000 / count);
  const shares = Array.from({ length: count }, () => each);
  return shares.map((bps, i) => (i === 0 ? bps + 10_000 - each * count : bps));
}
