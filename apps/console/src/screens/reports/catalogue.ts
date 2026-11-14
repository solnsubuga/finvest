/**
 * The reports staff download and have emailed on a schedule, as the Reports and audit screen
 * sees them.
 *
 * The API lists only the reports the reader may download, and checks again on every download.
 * A schedule is proposed by one person and approved by a different one, and sends the people
 * it names an email with a link to the file, never the file itself.
 */

import { ApiError } from "@/api/http.ts";

export const REPORT_KEYS = [
  "daily_position",
  "deposits_and_withdrawals",
  "interest_and_tax",
  "maturity_ladder",
  "identity_and_risk",
  "client_statements",
  "complaints_quarterly",
] as const;
export type ReportKey = (typeof REPORT_KEYS)[number];

/**
 * `day`: one day. `days`: up to 31 days that have begun. `month`: one calendar month.
 * `quarter`: one calendar quarter. `ahead`: up to 92 days, which may be in the future.
 * `now`: today only.
 */
export type PeriodKind = "day" | "days" | "month" | "quarter" | "ahead" | "now";
export type Currency = "UGX" | "USD";
export const FREQUENCIES = ["daily", "weekly", "monthly", "quarterly"] as const;
export type Frequency = (typeof FREQUENCIES)[number];
export type ScheduleStatus = "proposed" | "active" | "declined" | "withdrawn" | "stopped";

export const MAX_DAYS = 31;
export const MAX_AHEAD_DAYS = 92;
export const MAX_RECIPIENTS = 20;

export interface Report {
  key: ReportKey;
  name: string;
  about: string;
  period: PeriodKind;
  /** The figures are in one currency, so the screen asks which. */
  byCurrency: boolean;
  /** The period offered first. Kampala days, `YYYY-MM-DD`, both inclusive. */
  from: string;
  to: string;
}

export interface Catalogue {
  /** Today in Kampala. */
  today: string;
  reports: Report[];
}

export interface Period {
  from: string;
  to: string;
  currency: Currency;
}

export interface ReportFile {
  file: Blob;
  filename: string;
}

export interface Schedule {
  id: string;
  reportKey: ReportKey;
  reportName: string;
  frequency: Frequency;
  /** Null for a report that is not in one currency. */
  currency: Currency | null;
  recipients: string[];
  recipientNames: string[];
  reason: string;
  status: ScheduleStatus;
  proposedBy: string;
  proposedByName: string | null;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  stoppedBy: string | null;
  stoppedAt: string | null;
  lastSentAt: string | null;
  /** The reader proposed it: they may withdraw it, never approve it. */
  yours: boolean;
}

export interface Recipient {
  id: string;
  name: string;
  /** The reports this person's role may download, so may be sent. */
  reports: ReportKey[];
}

export interface Schedules {
  schedules: Schedule[];
  frequencies: Partial<Record<ReportKey, Frequency[]>>;
  people: Recipient[];
}

export interface Proposal {
  reportKey: ReportKey;
  frequency: Frequency;
  currency: Currency;
  recipients: string[];
  reason: string;
}

export type Decision = "approve" | "decline" | "withdraw" | "stop";

export interface ReportsApi {
  catalogue(): Promise<Catalogue>;
  download(key: ReportKey, period: Period): Promise<ReportFile>;
  schedules(): Promise<Schedules>;
  propose(proposal: Proposal, idempotencyKey: string): Promise<Schedule>;
  /** `approve` carries the key; the others are safe to send twice. */
  decide(id: string, decision: Decision, reason: string, idempotencyKey: string): Promise<Schedule>;
}

export type ReportsErrorCode =
  | "forbidden"
  | "not_connected"
  | "unavailable"
  | "not_found"
  | "period"
  | "too_many_rows"
  | "refused"
  | "unknown";

export class ReportsError extends Error {
  constructor(
    readonly code: ReportsErrorCode,
    /** The API's own reason, such as `whole_month_only` or `proposed_by_you`. */
    readonly reason: string | null = null,
  ) {
    super(code);
    this.name = "ReportsError";
  }
}

const PERIOD_REASONS = new Set([
  "days_backwards",
  "too_many_days",
  "today_only",
  "period_in_future",
  "one_day_only",
  "whole_month_only",
  "whole_quarter_only",
]);

/** Turns what the API answered into the screen's own error. */
export function toReportsError(error: unknown): ReportsError {
  if (error instanceof ReportsError) return error;
  if (error instanceof ApiError) {
    const reason = error.fieldCode;
    if (error.code === "unavailable") return new ReportsError("unavailable");
    if (reason === "too_many_rows") return new ReportsError("too_many_rows", reason);
    if (reason && PERIOD_REASONS.has(reason)) return new ReportsError("period", reason);
    if (error.code === "not_found" || error.status === 404) return new ReportsError("not_found");
    if (reason) return new ReportsError("refused", reason);
    if (error.code === "forbidden" || error.status === 403) return new ReportsError("forbidden");
    return new ReportsError("unknown");
  }
  return new ReportsError("unavailable");
}

const PERIOD_MESSAGES: Record<string, string> = {
  days_backwards: "The to date must be on or after the from date.",
  too_many_days: "That is too many days for one file. Choose a shorter period.",
  today_only: "This report shows how things stand now, so it can only be made for today.",
  period_in_future: "That period has not started yet.",
  one_day_only: "This report covers one day. Choose a single day.",
  whole_month_only: "This report covers one whole calendar month.",
  whole_quarter_only: "This report covers one whole calendar quarter.",
};

const REFUSED_MESSAGES: Record<string, string> = {
  reason_required: "Give a reason. It is kept on the audit trail.",
  frequency_not_allowed: "This report cannot be sent that often.",
  recipients_count: `Choose between 1 and ${MAX_RECIPIENTS} people.`,
  recipient_not_active: "Someone you chose is no longer active on the team. Choose again.",
  recipient_may_not_download: "Someone you chose has a role that cannot download this report. Choose again.",
  not_waiting: "This schedule is no longer waiting. The list has been refreshed.",
  not_running: "This schedule is no longer running. The list has been refreshed.",
  proposed_by_you: "You proposed this schedule, so a different person must approve or decline it.",
  not_proposed_by_you: "Only the person who proposed a schedule can withdraw it.",
};

export function reportsErrorMessage(error: unknown): string {
  const known = toReportsError(error);
  switch (known.code) {
    case "forbidden":
      return "Your role cannot do this.";
    case "not_connected":
      return "This console is not connected to Finvest yet.";
    case "unavailable":
      return "Finvest could not be reached. Check your connection and try again.";
    case "not_found":
      return "That schedule is no longer there. The list has been refreshed.";
    case "too_many_rows":
      return "The file would hold more than 20,000 rows. Choose a shorter period.";
    case "period":
      return PERIOD_MESSAGES[known.reason ?? ""] ?? "That period does not fit this report.";
    case "refused":
      return REFUSED_MESSAGES[known.reason ?? ""] ?? "Finvest refused that. Refresh and try again.";
    case "unknown":
      return "Something went wrong. Try again.";
  }
}

/** Errors after which the list is out of date and is fetched again. */
export function reloadsSchedules(error: unknown): boolean {
  const known = toReportsError(error);
  return known.code === "not_found" || known.reason === "not_waiting" || known.reason === "not_running";
}

const refuse = () => Promise.reject(new ReportsError("not_connected"));

export const notConnectedReportsApi: ReportsApi = {
  catalogue: refuse,
  download: refuse,
  schedules: refuse,
  propose: refuse,
  decide: refuse,
};

// Dates. Every date here is a Kampala day, `YYYY-MM-DD`, handled as a calendar date in UTC so
// the reader's own time zone never moves it.

const PAD = (value: number) => String(value).padStart(2, "0");

export function toDay(date: Date): string {
  return `${date.getUTCFullYear()}-${PAD(date.getUTCMonth() + 1)}-${PAD(date.getUTCDate())}`;
}

export function parseDay(day: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const [year, month, date] = day.split("-").map(Number) as [number, number, number];
  const parsed = new Date(Date.UTC(year, month - 1, date));
  return toDay(parsed) === day ? parsed : null;
}

export function addDays(day: string, days: number): string {
  const parsed = parseDay(day) ?? new Date(0);
  return toDay(new Date(parsed.getTime() + days * 86_400_000));
}

export function daysBetween(from: string, to: string): number {
  const start = parseDay(from);
  const end = parseDay(to);
  if (!start || !end) return 0;
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
}

/** `YYYY-MM` to its first and last day. */
export function monthPeriod(month: string): { from: string; to: string } | null {
  if (!/^\d{4}-\d{2}$/.test(month)) return null;
  const [year, number] = month.split("-").map(Number) as [number, number];
  if (number < 1 || number > 12) return null;
  const from = toDay(new Date(Date.UTC(year, number - 1, 1)));
  const to = toDay(new Date(Date.UTC(year, number, 0)));
  return { from, to };
}

export function quarterPeriod(year: number, quarter: number): { from: string; to: string } {
  const from = toDay(new Date(Date.UTC(year, (quarter - 1) * 3, 1)));
  const to = toDay(new Date(Date.UTC(year, quarter * 3, 0)));
  return { from, to };
}

/** The quarter a day falls in, as `{year, quarter}`. */
export function quarterOf(day: string): { year: number; quarter: number } {
  const [year, month] = day.split("-").map(Number) as [number, number];
  return { year, quarter: Math.floor((month - 1) / 3) + 1 };
}

/** What is wrong with a period before it is sent, the way the API would say it, or null. */
export function periodProblem(kind: PeriodKind, from: string, to: string, today: string): string | null {
  if (!parseDay(from) || !parseDay(to)) return "Choose the dates.";
  if (to < from) return PERIOD_MESSAGES.days_backwards ?? null;
  if (kind === "ahead") return daysBetween(from, to) > MAX_AHEAD_DAYS ? `Choose up to ${MAX_AHEAD_DAYS} days.` : null;
  if (kind === "now") return from === today && to === today ? null : (PERIOD_MESSAGES.today_only ?? null);
  if (from > today) return PERIOD_MESSAGES.period_in_future ?? null;
  if (kind === "days" && daysBetween(from, to) > MAX_DAYS) return `Choose up to ${MAX_DAYS} days.`;
  return null;
}

/** The file's name, as the API names it in Content-Disposition. */
export function reportFilename(key: ReportKey, currency: Currency | null, from: string, to: string): string {
  const slug = key.replaceAll("_", "-");
  const money = currency ? `-${currency.toLowerCase()}` : "";
  const span = from === to ? from : `${from}-to-${to}`;
  return `finvest-${slug}${money}-${span}.csv`;
}
