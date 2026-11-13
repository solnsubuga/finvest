/** Typing an amount in whole shillings: digits only, grouped by thousands as they are typed. */

/** At most a trillion shillings, far above any goal, and well inside a safe integer. */
const MAX_DIGITS = 13;

/** "2000000" or "2,000,000" to 2000000. Anything but digits and commas is not an amount. */
export function parseShillings(text: string): number | null {
  const digits = text.replace(/,/g, "").trim();
  if (!/^\d+$/.test(digits) || digits.length > MAX_DIGITS) {
    return null;
  }
  const value = Number(digits);
  return value > 0 ? value : null;
}

/** What the field shows as it is typed: "2000000" becomes "2,000,000". */
export function groupDigits(text: string): string {
  const digits = text.replace(/\D/g, "").replace(/^0+(?=\d)/, "").slice(0, MAX_DIGITS);
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "2026-11-13" in Kampala, for a date `months` from today on the same day, the 28th at most. */
export function kampalaToday(now: number = Date.now()): string {
  return new Date(now + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "November 2028" from "2028-11-13". */
export function monthYear(day: string): string {
  const [year, month] = day.split("-").map(Number);
  return `${MONTHS[(month ?? 1) - 1]} ${year}`;
}
