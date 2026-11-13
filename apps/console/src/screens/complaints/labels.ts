import type { Category, EventAction, LoggedVia, MoveTo, Outcome, ReceivedVia, Status } from "./api.ts";

export const CATEGORY_LABELS: Record<Category, string> = {
  payments: "Payments",
  withdrawals: "Withdrawals",
  investments: "Investments",
  identity: "Identity",
  account: "Account",
  fees: "Fees",
  service: "Service",
  other: "Other",
};
export const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

export const STATUS_LABELS: Record<Status, string> = {
  new: "New",
  investigating: "Investigating",
  waiting_for_client: "Waiting for client",
  escalated: "Escalated",
  closed: "Closed",
};
export const MOVES: readonly MoveTo[] = ["investigating", "waiting_for_client", "escalated"];

/** Reads after "by": "by phone". */
export const VIA_LABELS: Record<ReceivedVia, string> = {
  app: "in the app",
  chat: "in the help chat",
  phone: "phone",
  email: "email",
  in_person: "in person",
  letter: "letter",
};
export const LOGGED_VIA: readonly LoggedVia[] = ["chat", "phone", "email", "in_person", "letter"];
export const LOGGED_VIA_LABELS: Record<LoggedVia, string> = {
  chat: "Help chat",
  phone: "Phone",
  email: "Email",
  in_person: "In person",
  letter: "Letter",
};

export function received(via: ReceivedVia): string {
  return via === "app" || via === "chat" || via === "in_person" ? VIA_LABELS[via] : `by ${VIA_LABELS[via]}`;
}

export const OUTCOME_LABELS: Record<Outcome, string> = {
  upheld: "Upheld",
  partly_upheld: "Partly upheld",
  not_upheld: "Not upheld",
};
export const OUTCOME_HINTS: Record<Outcome, string> = {
  upheld: "The client was right",
  partly_upheld: "The client was right in part",
  not_upheld: "Finvest did nothing wrong",
};
export const OUTCOMES = Object.keys(OUTCOME_LABELS) as Outcome[];

export const ACTION_LABELS: Record<EventAction, string> = {
  logged: "Logged",
  assigned: "Assigned",
  status: "Moved",
  note: "Note",
  escalated: "Escalated",
  proposed: "Outcome proposed",
  rejected: "Outcome rejected",
  withdrawn: "Outcome withdrawn",
  closed: "Closed",
};

const SHORT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const KAMPALA_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" });
const STAMP = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Africa/Kampala",
});

/** "16 Nov" for a calendar date. */
export function shortDate(date: string): string {
  return SHORT.format(new Date(`${date}T00:00:00Z`));
}

/** The Kampala calendar date of a moment, `YYYY-MM-DD`. */
export function kampalaDate(iso: string): string {
  return KAMPALA_DAY.format(new Date(iso));
}

/** "10 Nov, 14:20", Kampala time. */
export function stamp(iso: string): string {
  return STAMP.format(new Date(iso));
}

/** "Today" or "16 Nov". */
export function dueLabel(dueOn: string, today: string): string {
  return dueOn === today ? "Today" : shortDate(dueOn);
}

/** "2.4 days" from tenths of a working day. */
export function tenthsOfDays(tenths: number): string {
  const text = `${Math.floor(tenths / 10)}.${tenths % 10}`;
  return tenths === 10 ? "1.0 day" : `${text} days`;
}
