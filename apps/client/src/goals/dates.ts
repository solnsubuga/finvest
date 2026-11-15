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

/**
 * The next payday on or after `today`: the `payday` of this month, or of next month once it
 * has passed. A payday past the end of a short month falls on its last day.
 */
export function nextPayday(payday: number, today: string): string {
  const [y, m, d] = today.split("-").map(Number);
  const year = y ?? 1970;
  const month = (m ?? 1) - 1;
  const on = (yr: number, mo: number) => {
    const last = new Date(Date.UTC(yr, mo + 1, 0)).getUTCDate();
    return iso(Date.UTC(yr, mo, Math.min(payday, last)));
  };
  const thisMonth = on(year, month);
  if (Number(thisMonth.slice(8)) >= (d ?? 1)) return thisMonth;
  return on(month === 11 ? year + 1 : year, (month + 1) % 12);
}

/** Today's date in Kampala, which keeps UTC+3 all year. */
export function kampalaToday(nowMs: number = Date.now()): string {
  return iso(nowMs + 3 * 60 * 60 * 1000);
}
