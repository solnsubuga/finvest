import type { Frequency, Network, Run, RunStatus } from "./api.ts";

export const STATUS_LABELS: Record<RunStatus, string> = {
  due: "Due",
  requested: "Prompt sent",
  paid: "Invested",
  failed: "Payment failed",
  missed: "Missed",
};

export const NETWORK_LABELS: Record<Network, string> = { mtn: "MTN", airtel: "Airtel" };

/** Why a run failed or was missed, in plain words. Unknown codes are shown as they are. */
const CODE_LABELS: Record<string, string> = {
  late: "Not opened on its day, so it was skipped rather than collected late",
  previous_run_open: "The plan's previous run was still open",
  plan_not_active: "The plan was paused or stopped before the retry",
  declined: "The client declined or entered a wrong PIN",
  insufficient_funds: "Not enough money on the number",
  approval_timed_out: "The client did not answer the prompt",
  account_not_found: "The number is not registered for mobile money",
  limit_exceeded: "Over the client's or the network's limit",
  other: "The network refused without a reason",
};

export function codeLabel(code: string): string {
  return CODE_LABELS[code] ?? code;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function ordinal(day: number): string {
  if (day % 100 >= 11 && day % 100 <= 13) return `${day}th`;
  return `${day}${{ 1: "st", 2: "nd", 3: "rd" }[day % 10] ?? "th"}`;
}

export function scheduleLabel(frequency: Frequency, day: number): string {
  if (frequency === "weekly") return `Every ${WEEKDAYS[day - 1] ?? ""}`;
  return `Monthly on the ${ordinal(day)}`;
}

/** "13 Nov 2026" from YYYY-MM-DD, without going through a time zone. */
export function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return `${date} ${MONTHS[(month ?? 1) - 1] ?? ""} ${year}`;
}

/** Whole shillings with thousands separators, from a whole-number string. */
export function formatShillings(amount: string): string {
  return `UGX ${amount.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
}

/** The note under a run's status: the reason, and the retry day when there is one. */
export function runNote(run: Run): string | null {
  const reason = run.code ? codeLabel(run.code) : null;
  if (run.status === "failed" && run.retryOn) {
    return `${reason ?? "Payment failed"}. One more try on ${formatDay(run.retryOn)}.`;
  }
  if (run.attempt === 2) {
    return reason ? `${reason}, on the second try too.` : "Paid on the second try, the day after.";
  }
  return reason ? `${reason}.` : null;
}

/** Adds days to YYYY-MM-DD, counting in UTC so no clock change moves it. */
export function addDays(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
