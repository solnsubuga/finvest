/**
 * The console's view of the complaints register.
 *
 * The shapes follow the staff routes in docs/support.md. `http.ts` calls them;
 * `stub.ts` is the sample for `pnpm dev` and previews.
 */

import type { Amount } from "@/screens/dealing-desk/api.ts";

export type Category = "payments" | "withdrawals" | "investments" | "identity" | "account" | "fees" | "service" | "other";
export type Status = "new" | "investigating" | "waiting_for_client" | "escalated" | "closed";
export type OpenStatus = Exclude<Status, "closed">;
/** The statuses staff can move a complaint to. It starts as new, and an outcome closes it once a second person confirms it. */
export type MoveTo = Exclude<OpenStatus, "new">;
export type ReceivedVia = "app" | "chat" | "phone" | "email" | "in_person" | "letter";
/** How staff can log a complaint. A client logs their own from the app. */
export type LoggedVia = Exclude<ReceivedVia, "app">;
export type Outcome = "upheld" | "partly_upheld" | "not_upheld";
export type Show = "open" | "closed" | "all";
export type EventAction = "logged" | "assigned" | "status" | "note" | "escalated" | "proposed" | "rejected" | "withdrawn" | "closed";
/** Confirming closes the complaint. Rejecting says why. Only the proposer withdraws. */
export type Decision = "confirmed" | "rejected" | "withdrawn";

export interface Complaint {
  id: string;
  /** "C-1001". */
  reference: string;
  clientId: string;
  conversationId: string | null;
  category: Category;
  receivedVia: ReceivedVia;
  summary: string;
  details: string;
  /** The sum the client says is in question. A note for staff: it moves no money. */
  amount: Amount | null;
  status: Status;
  ownerId: string | null;
  receivedAt: string;
  /** `YYYY-MM-DD`: the day Finvest promised to answer by. */
  dueOn: string;
  overdue: boolean;
  closedAt: string | null;
  outcome: Outcome | null;
  /** What was done about it. The client reads this. */
  resolution: string | null;
  clientAccepted: boolean | null;
}

export interface HistoryItem {
  id: string;
  action: EventAction;
  actor: "client" | "staff";
  actorId: string;
  fromStatus: Status | null;
  toStatus: Status | null;
  note: string | null;
  occurredAt: string;
}

/** An outcome one member of staff proposed. A different one confirms or rejects it, and confirming closes the complaint. */
export interface PendingOutcome {
  id: string;
  outcome: Outcome;
  resolution: string;
  clientAccepted: boolean;
  proposedBy: string;
  proposedAt: string;
}

export interface ComplaintDetail {
  complaint: Complaint;
  history: readonly HistoryItem[];
  /** At most one waits at a time. */
  pendingOutcome: PendingOutcome | null;
}

export interface Summary {
  open: number;
  overdue: number;
  receivedThisQuarter: number;
  closedThisQuarter: number;
  upheldThisQuarter: number;
  /** Working days, in tenths: 24 is 2.4 days. Null when nothing was closed this quarter. */
  averageDaysToCloseTenths: number | null;
  targetWorkingDays: number;
}

export interface Register {
  summary: Summary;
  complaints: readonly Complaint[];
}

export interface NewComplaint {
  /** Sent again with a retry after a lost answer, so the complaint is logged once. 8 to 64 of A-Z, a-z, 0-9 and . _ : - */
  clientKey: string;
  clientId: string;
  category: Category;
  receivedVia: LoggedVia;
  summary: string;
  details: string;
  amount: Amount | null;
}

export interface Closing {
  outcome: Outcome;
  resolution: string;
  clientAccepted: boolean;
}

export const MAX_SUMMARY = 120;
export const MAX_DETAILS = 4000;
export const MAX_NOTE = 2000;
export const MAX_RESOLUTION = 2000;

/** Every command answers with the complaint as it now stands, so the panel never shows a stale history. */
export interface ComplaintsApi {
  /** The register's figures always cover the whole register. `show` filters the rows. */
  register(show: Show): Promise<Register>;
  complaint(complaintId: string): Promise<ComplaintDetail>;
  log(complaint: NewComplaint): Promise<ComplaintDetail>;
  assign(complaintId: string, ownerId: string): Promise<ComplaintDetail>;
  move(complaintId: string, status: MoveTo, note: string): Promise<ComplaintDetail>;
  addNote(complaintId: string, note: string): Promise<ComplaintDetail>;
  /** Proposes the outcome. Someone else confirms it. */
  proposeOutcome(complaintId: string, closing: Closing): Promise<ComplaintDetail>;
  /** Names the proposal, so a newer one is never confirmed by mistake. A rejection needs a reason. */
  decideOutcome(complaintId: string, proposalId: string, decision: Decision, reason: string | null): Promise<ComplaintDetail>;
}

export type ComplaintsErrorCode = "invalid" | "not_found" | "conflict" | "forbidden" | "not_connected" | "unavailable";

export class ComplaintsError extends Error {
  constructor(readonly code: ComplaintsErrorCode) {
    super(code);
    this.name = "ComplaintsError";
  }
}

const MESSAGES: Record<ComplaintsErrorCode, string> = {
  invalid: "That could not be saved. Check what you wrote and try again.",
  not_found: "This complaint could not be found.",
  conflict: "This complaint has moved on. It is closed, or an outcome is already waiting. Open it again to see where it stands.",
  forbidden: "That needs a different person or a role you do not have. Ask compliance or an admin.",
  not_connected: "Complaints are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function complaintsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ComplaintsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new ComplaintsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedComplaintsApi: ComplaintsApi = {
  register: notConnected,
  complaint: notConnected,
  log: notConnected,
  assign: notConnected,
  move: notConnected,
  addNote: notConnected,
  proposeOutcome: notConnected,
  decideOutcome: notConnected,
};
