/**
 * Asking for the interest run or the maturity run again: `/staff/investment-run-requests` in
 * backend/app/modules/investments/api.py (see "Running a day again" in docs/investing.md).
 *
 * One person asks with a reason, a different person approves, and approving queues the run.
 * Both runs only post what is still owed or close what is still open, so running one again is
 * safe. `http.ts` calls the routes; `stub.ts` is the sample for `pnpm dev` and previews.
 */

export type RunKind = "interest" | "maturity";
export type RunStatus = "proposed" | "approved" | "declined" | "withdrawn";

export interface RunRequest {
  id: string;
  kind: RunKind;
  /** `YYYY-MM-DD`, the Kampala business day the run is for. */
  runDate: string;
  reason: string;
  status: RunStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was declined. */
  decisionNote: string | null;
}

export interface NewRunRequest {
  kind: RunKind;
  runDate: string;
  reason: string;
}

/** Each command takes an idempotency key, kept per attempt by the screen. */
export interface RunRequestsApi {
  /** Newest first. */
  list(): Promise<readonly RunRequest[]>;
  propose(key: string, request: NewRunRequest): Promise<RunRequest>;
  approve(key: string, requestId: string): Promise<RunRequest>;
  decline(key: string, requestId: string, note: string): Promise<RunRequest>;
  withdraw(key: string, requestId: string): Promise<RunRequest>;
}

export type RunField = "run_date" | "reason" | "note";

export interface RunProblem {
  field: RunField;
  code: string;
}

export type RunRequestsErrorCode =
  | "invalid"
  | "conflict"
  | "day_passed"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class RunRequestsError extends Error {
  constructor(
    readonly code: RunRequestsErrorCode,
    readonly problems: readonly RunProblem[] = [],
  ) {
    super(code);
    this.name = "RunRequestsError";
  }
}

const MESSAGES: Record<RunRequestsErrorCode, string> = {
  invalid: "Finvest refused this. Fix what is marked and try again.",
  conflict: "A request for this run and day is already waiting, or this one was decided already. Here is the list as it now stands.",
  day_passed: "This asked for an earlier day's maturity run. Today's own run already covers it, so decline it and ask again if needed.",
  same_person: "Someone else has to decide this. You asked for it, so you can only withdraw it.",
  forbidden: "You are not allowed to do this. Ask an admin.",
  not_found: "Finvest could not find that request.",
  not_connected: "Running again is not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function runRequestsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof RunRequestsError ? error.code : "unavailable"];
}

const PROBLEMS: Record<string, string> = {
  required: "Fill this in.",
  too_long: "Keep it to 500 characters.",
  not_yet: "That day has not come yet.",
  too_old: "Only the last 31 days can be run again.",
  maturity_today_only: "The maturity run is only run again for today. Today's run closes every term due before it.",
};

export function runProblemText(code: string): string {
  return PROBLEMS[code] ?? "Finvest did not accept this. Change it and try again.";
}

const notConnected = () => Promise.reject(new RunRequestsError("not_connected"));

export const notConnectedRunRequestsApi: RunRequestsApi = {
  list: notConnected,
  propose: notConnected,
  approve: notConnected,
  decline: notConnected,
  withdraw: notConnected,
};
