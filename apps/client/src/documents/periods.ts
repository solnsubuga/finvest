/**
 * Statement periods as "YYYY-MM-DD" text: the quick picks, what a typed date
 * means, and the same rules the backend applies (docs/documents.md), so the
 * client is told before asking. The backend still decides.
 */

import { formatLongDay } from "@/autoinvest/schedule";

import { DocumentItem, PeriodProblem } from "./types";

/** A statement covers at most this many days, both ends counted. */
export const MAX_PERIOD_DAYS = 366;

export type QuickPick = "last_month" | "last_3_months" | "this_year";

export const QUICK_PICKS: { pick: QuickPick; label: string }[] = [
  { pick: "last_month", label: "Last month" },
  { pick: "last_3_months", label: "Last 3 months" },
  { pick: "this_year", label: "This year" },
];

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const MONTH_NAMES = [
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
const DAY_MS = 86_400_000;

function parts(day: string): [number, number, number] {
  const [y, m, d] = day.split("-").map(Number);
  return [y ?? 1970, m ?? 1, d ?? 1];
}

function iso(year: number, month: number, day: number): string {
  return new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10);
}

/** The period a quick pick means on `today`. */
export function quickPeriod(pick: QuickPick, today: string): { from: string; to: string } {
  const [year, month] = parts(today);
  if (pick === "last_month") {
    return { from: iso(year, month - 1, 1), to: iso(year, month, 0) };
  }
  if (pick === "last_3_months") {
    // 13 Nov is 1 Aug to 13 Nov: the three whole months before, and this one so far.
    return { from: iso(year, month - 3, 1), to: today };
  }
  return { from: iso(year, 1, 1), to: today };
}

/** Which quick pick, if any, a period is on `today`. */
export function pickFor(from: string, to: string, today: string): QuickPick | null {
  const found = QUICK_PICKS.find(({ pick }) => {
    const period = quickPeriod(pick, today);
    return period.from === from && period.to === to;
  });
  return found?.pick ?? null;
}

/**
 * A date as a client types it: "1 Aug 2026", "1/8/2026" or "2026-08-01".
 * Null for anything else, including a day that does not exist.
 */
export function parseDay(text: string): string | null {
  const clean = text.trim().toLowerCase().replace(/,/g, " ").replace(/\s+/g, " ");
  let year: number;
  let month: number;
  let day: number;
  let match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(clean);
  if (match) {
    [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  } else if ((match = /^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$/.exec(clean))) {
    // Day first, as written in Uganda.
    [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])];
  } else if ((match = /^(\d{1,2}) ([a-z]+) (\d{4})$/.exec(clean))) {
    const index = MONTHS.indexOf((match[2] ?? "").slice(0, 3));
    if (index < 0) {
      return null;
    }
    [day, month, year] = [Number(match[1]), index + 1, Number(match[3])];
  } else {
    return null;
  }
  const result = iso(year, month, day);
  // Date.UTC rolls 31 Feb over to March: such a day was never real.
  return result === `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`
    ? result
    : null;
}

/** The first rule a period breaks, in the backend's words for it, or null. */
export function periodProblem(from: string, to: string, today: string): PeriodProblem | null {
  if (from > to) {
    return "period_backwards";
  }
  if (to > today) {
    return "period_in_future";
  }
  const [fy, fm, fd] = parts(from);
  const [ty, tm, td] = parts(to);
  const days = Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / DAY_MS) + 1;
  return days > MAX_PERIOD_DAYS ? "period_too_long" : null;
}

/** What to tell the client, and which date to change. */
export function problemCopy(
  problem: PeriodProblem,
  from: string,
  to: string,
  today: string,
): { field: "from" | "to"; message: string } {
  if (problem === "period_backwards") {
    return {
      field: "from",
      message: `The start date is after the end date. Pick a start date on or before ${formatLongDay(to)}.`,
    };
  }
  if (problem === "period_in_future") {
    return {
      field: "to",
      message: `A statement can end today at the latest. Pick an end date on or before ${formatLongDay(today)}.`,
    };
  }
  return {
    field: "from",
    message: `A statement covers up to one year. Pick a start date on or after ${formatLongDay(
      earliestFrom(to),
    )}.`,
  };
}

/** The earliest start a statement ending on `to` may have. */
export function earliestFrom(to: string): string {
  const [y, m, d] = parts(to);
  return iso(y, m, d - (MAX_PERIOD_DAYS - 1));
}

/** "1 Aug 2026 to 13 Nov 2026". */
export function periodLabel(from: string, to: string): string {
  return `${formatLongDay(from)} to ${formatLongDay(to)}`;
}

/** "October 2026" for a month's statement. */
export function monthLabel(from: string): string {
  const [year, month] = parts(from);
  return `${MONTH_NAMES[month - 1] ?? ""} ${year}`;
}

/** The receipt's reference, "RC-…", from its file name. */
export function receiptReference(document: DocumentItem): string | null {
  const match = /receipt (\S+)\.pdf$/i.exec(document.fileName ?? "");
  return match?.[1] ?? null;
}
