/**
 * Sending investment notes again: `/staff/note-reissues` in backend/app/modules/documents/api.py
 * (see "Sending notes again" in docs/documents.md).
 *
 * One person asks with a reason, for one client or every client and the days the notes were
 * issued on. A different person approves, and approving emails each note again: the same stored
 * PDF, with the same number, locked as before. `http.ts` calls the routes; `stub.ts` is the
 * sample for `pnpm dev` and previews.
 */

export type ReissueStatus = "proposed" | "approved" | "declined" | "withdrawn" | "done";

export interface CopyCounts {
  queued: number;
  sent: number;
  skipped: number;
  failed: number;
}

export interface Reissue {
  id: string;
  /** Null for every client's notes. */
  clientId: string | null;
  /** `YYYY-MM-DD`, the Kampala days the notes were issued on, both included. */
  issuedFrom: string;
  issuedTo: string;
  reason: string;
  status: ReissueStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was declined. */
  decisionNote: string | null;
  /** How many notes it covers: now while it waits, as sent once done. */
  noteCount: number;
  copies: CopyCounts;
  finishedAt: string | null;
}

export interface ReissueScope {
  clientId: string | null;
  issuedFrom: string;
  issuedTo: string;
}

export interface NewReissue extends ReissueScope {
  reason: string;
}

/** Each command takes an idempotency key, kept per attempt by the screen. */
export interface ReissuesApi {
  /** Newest first. */
  list(): Promise<readonly Reissue[]>;
  /** How many notes a request for this scope would send again. */
  count(scope: ReissueScope): Promise<number>;
  propose(key: string, request: NewReissue): Promise<Reissue>;
  approve(key: string, requestId: string): Promise<Reissue>;
  decline(key: string, requestId: string, note: string): Promise<Reissue>;
  withdraw(key: string, requestId: string): Promise<Reissue>;
}

export type ReissueField = "client_id" | "issued_from" | "issued_to" | "reason" | "note";

export interface ReissueProblem {
  field: ReissueField;
  code: string;
}

export type ReissuesErrorCode = "invalid" | "conflict" | "same_person" | "forbidden" | "not_found" | "not_connected" | "not_confirmed" | "unavailable";

export class ReissuesError extends Error {
  constructor(
    readonly code: ReissuesErrorCode,
    readonly problems: readonly ReissueProblem[] = [],
  ) {
    super(code);
    this.name = "ReissuesError";
  }
}

const MESSAGES: Record<ReissuesErrorCode, string> = {
  invalid: "Finvest refused this. Fix what is marked and try again.",
  conflict: "This was decided already. Here is the list as it now stands.",
  same_person: "Someone else has to decide this. You asked for it, so you can only withdraw it.",
  forbidden: "You are not allowed to do this. Ask an admin.",
  not_found: "Finvest could not find that request.",
  not_connected: "Reissuing notes is not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function reissuesErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ReissuesError ? error.code : "unavailable"];
}

const PROBLEMS: Record<string, string> = {
  required: "Fill this in.",
  too_long: "Keep it to 500 characters.",
  not_yet: "That day has not come yet.",
  too_old: "Only notes from the last 400 days can be sent again.",
  after_issued_to: "The first day comes after the last.",
  no_notes: "No notes were issued on these days, so there is nothing to send.",
};

export function reissueProblemText(code: string, field?: ReissueField): string {
  if (code === "too_long" && field === "issued_to") return "Choose at most 31 days.";
  return PROBLEMS[code] ?? "Finvest did not accept this. Change it and try again.";
}

const notConnected = () => Promise.reject(new ReissuesError("not_connected"));

export const notConnectedReissuesApi: ReissuesApi = {
  list: notConnected,
  count: notConnected,
  propose: notConnected,
  approve: notConnected,
  decline: notConnected,
  withdraw: notConnected,
};
