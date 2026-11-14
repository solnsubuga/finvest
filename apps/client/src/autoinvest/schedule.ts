/**
 * Plan schedules, worked out the same way as the backend's
 * `autoinvest/domain/plans.py`, so the date the app shows is the date the
 * prompt arrives. Dates are YYYY-MM-DD Kampala days, handled as UTC dates so
 * the phone's own time zone never shifts them.
 */

import { Frequency, RunStatus } from "./types";

export const MAX_RUN_MINOR = 5_000_000;

export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;

function parse(day: string): Date {
  const [year = NaN, month = NaN, date = NaN] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date));
}

function format(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
}

/** Today in Kampala, as YYYY-MM-DD. */
export function kampalaToday(now: Date = new Date()): string {
  return format(new Date(now.getTime() + KAMPALA_OFFSET_MS));
}

/** "1st", "2nd", "3rd", "28th". */
export function ordinal(day: number): string {
  const tens = day % 100;
  if (tens >= 11 && tens <= 13) {
    return `${day}th`;
  }
  return `${day}${{ 1: "st", 2: "nd", 3: "rd" }[day % 10] ?? "th"}`;
}

/** "Every Friday" or "Every month on the 28th". */
export function scheduleLabel(frequency: Frequency, day: number): string {
  if (frequency === "weekly") {
    return `Every ${WEEKDAYS[day - 1] ?? ""}`;
  }
  const label = `Every month on the ${ordinal(day)}`;
  return day > 28 ? `${label}, or the month's last day` : label;
}

/** The first due day strictly after `after`. */
export function nextAfter(frequency: Frequency, day: number, after: string): string {
  const start = parse(after);
  start.setUTCDate(start.getUTCDate() + 1);
  if (frequency === "weekly") {
    // getUTCDay: 0 is Sunday. Ours: 1 is Monday, 7 is Sunday.
    const weekday = start.getUTCDay() === 0 ? 7 : start.getUTCDay();
    start.setUTCDate(start.getUTCDate() + ((day - weekday + 7) % 7));
    return format(start);
  }
  let year = start.getUTCFullYear();
  let month = start.getUTCMonth();
  for (;;) {
    const candidate = new Date(Date.UTC(year, month, Math.min(day, daysInMonth(year, month))));
    if (candidate >= start) {
      return format(candidate);
    }
    month += 1;
    if (month === 12) {
      month = 0;
      year += 1;
    }
  }
}

/** A plan set up today first runs on its first due day after today. Never today. */
export function firstRun(frequency: Frequency, day: number, today: string): string {
  return nextAfter(frequency, day, today);
}

/** "Fri 28 Nov". */
export function formatDay(day: string): string {
  const date = parse(day);
  const weekday = (WEEKDAYS[(date.getUTCDay() + 6) % 7] ?? "").slice(0, 3);
  return `${weekday} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()] ?? ""}`;
}

/** "28 Nov 2026". */
export function formatLongDay(day: string): string {
  const date = parse(day);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()] ?? ""} ${date.getUTCFullYear()}`;
}

/**
 * Shillings typed by a client: digits, with commas or spaces allowed. Null
 * for anything else, including a decimal point, so no fraction of a shilling
 * ever reaches a request.
 */
export function parseShillings(text: string): number | null {
  const cleaned = text.replace(/[,\s]/g, "");
  if (!/^\d{1,10}$/.test(cleaned)) {
    return null;
  }
  const value = Number(cleaned);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export const runCopy: Record<RunStatus, string> = {
  due: "Due",
  requested: "Prompt sent",
  paid: "Invested",
  failed: "Payment failed",
  missed: "Missed",
};

/** Why a run did not go through, for the client. */
export function runReason(code: string | null, attempt: number, status: RunStatus): string | null {
  if (status === "failed") {
    return attempt === 1
      ? "We will try once more the next day."
      : "Tried twice. We will not try again for this day.";
  }
  if (status !== "missed") {
    return null;
  }
  switch (code) {
    case "plan_not_active":
      return "The plan was paused before we could try again.";
    case "previous_run_open":
      return "The last payment was still being checked, so we did not send a second prompt.";
    case "late":
      return "We could not send the prompt on the day, so we skipped it rather than collect late.";
    case "closing_account":
      return "Your request to close your account was being checked, so nothing was collected.";
    default:
      return "Skipped. Nothing was collected.";
  }
}
