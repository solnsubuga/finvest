/** Calendar days as "YYYY-MM-DD" text. No rates or money here: screens import this in production. */

const DAY_MS = 86_400_000;

function toUtc(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function iso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((toUtc(to) - toUtc(from)) / DAY_MS);
}

/** The same day of the month `months` later, on the 28th at the latest. */
export function addMonths(day: string, months: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const index = (m ?? 1) - 1 + months;
  return iso(Date.UTC((y ?? 1970) + Math.floor(index / 12), index % 12, Math.min(d ?? 1, 28)));
}
