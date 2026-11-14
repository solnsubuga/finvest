/**
 * The console's view of payments credited to the wrong client, and moving them.
 *
 * The shapes follow the staff routes in backend/app/modules/payments/reassignment_routes.py
 * (see "A transfer credited to the wrong client" in docs/payments.md).
 *
 * One member of staff proposes moving a confirmed bank transfer to the right client's
 * investment, which must be waiting for exactly that amount. A different one approves, and the
 * money moves at that moment. The backend enforces every rule. The screen only shows what the
 * backend allows.
 */

import type { Amount, StatementLine } from "../api.ts";

export type ReassignmentStatus = "proposed" | "applied" | "sent_back" | "withdrawn";

/** Why the backend would refuse to move it right now. */
export type Refusal =
  | "mobile_money_refund_instead"
  | "not_confirmed"
  | "not_for_an_investment"
  | "not_matched_to_a_line"
  | "money_moved_on"
  | "same_client"
  | "not_waiting_for_payment"
  | "amount_differs_from_investment"
  | "already_has_a_payment";

export interface Party {
  clientId: string;
  name: string | null;
  /** The client's Finvest account number, which clients write on a transfer. */
  accountNumber: string | null;
}

export interface ReassignmentSummary {
  id: string;
  status: ReassignmentStatus;
  /** The confirmed payment credited to the wrong client. */
  depositId: string;
  fromInvestmentId: string;
  /** The right client's investment it pays for. */
  toInvestmentId: string;
  amount: Amount;
  reason: string;
  proposedAt: string;
  decidedAt: string | null;
  /** Why it was sent back. */
  decisionNote: string | null;
  /** Once moved: the right client's new payment. */
  newDepositId: string | null;
}

export interface ReassignmentDetail extends ReassignmentSummary {
  fromClient: Party;
  toClient: Party;
  /** The bank statement line the original check matched. Null if it can no longer be read. */
  line: StatementLine | null;
  referenceNamesToClient: boolean;
  referenceNamesFromClient: boolean;
  /** For an open proposal: why approving would be refused now. */
  refusal: Refusal | null;
  /** The person reading may approve or send it back. */
  canDecide: boolean;
  /** The person reading proposed it. They may only withdraw it. */
  yours: boolean;
}

export interface Proposal {
  depositId: string;
  toInvestmentId: string;
  reason: string;
}

/**
 * Every command takes an idempotency key made once per attempt by the screen, so a repeat from a
 * weak connection is replayed and never applied twice.
 */
export interface ReassignmentsApi {
  list(): Promise<ReassignmentSummary[]>;
  get(id: string): Promise<ReassignmentDetail>;
  propose(key: string, proposal: Proposal): Promise<ReassignmentDetail>;
  approve(key: string, id: string): Promise<ReassignmentDetail>;
  sendBack(key: string, id: string, note: string): Promise<ReassignmentDetail>;
  withdraw(key: string, id: string): Promise<ReassignmentDetail>;
}

export type ReassignmentErrorCode =
  | Refusal
  | "deposit_not_found"
  | "investment_not_found"
  | "not_found"
  | "conflict"
  | "forbidden"
  | "confirm_it_is_you"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class ReassignmentError extends Error {
  readonly code: ReassignmentErrorCode;
  /** The form field the refusal is about, when there is one. */
  readonly field: "depositId" | "toInvestmentId" | "reason" | "note" | null;

  constructor(code: ReassignmentErrorCode, field: ReassignmentError["field"] = null) {
    super(code);
    this.name = "ReassignmentError";
    this.code = code;
    this.field = field;
  }
}

/** What a refusal means, worded for staff. Used for the live check on a proposal too. */
export const REFUSALS: Record<Refusal, string> = {
  mobile_money_refund_instead:
    "This is a mobile money payment. It came from the first client's own number, so it cannot be credited to anyone else. Refund it to that number instead.",
  not_confirmed: "This payment is not confirmed, so no money was credited. Check it on the bank transfers list instead.",
  not_for_an_investment: "This payment is not for an investment.",
  not_matched_to_a_line: "This payment was not matched to a bank statement line by two people, so there is no evidence to move it on.",
  money_moved_on:
    "The money is no longer waiting with the first client. If their investment has started, undo it first on the investments screen. If it was paid out, record a receivable instead.",
  same_client: "That investment belongs to the same client the payment is already credited to.",
  not_waiting_for_payment: "That investment is not waiting for payment. It may have started, been cancelled, or its time to pay has passed.",
  amount_differs_from_investment: "That investment is for a different amount. Only an investment for exactly the payment's amount can take it.",
  already_has_a_payment: "That investment already has a payment of its own. If that one is the client's receipt for this money, refuse it first.",
};

const MESSAGES: Record<Exclude<ReassignmentErrorCode, Refusal>, string> = {
  deposit_not_found: "No payment has that ID.",
  investment_not_found: "No investment has that ID.",
  not_found: "This move no longer exists.",
  conflict: "Someone else has already acted on this, or another move for this payment or investment is open. It has been reloaded.",
  forbidden: "You cannot do this. The person who proposed a move cannot approve it, and only they can withdraw it.",
  confirm_it_is_you: "Approving needs you to confirm it is you with your passkey or authenticator app. Try again when you are ready.",
  invalid: "Check the details and try again.",
  not_connected: "Moving payments is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function reassignmentErrorMessage(error: unknown): string {
  if (!(error instanceof ReassignmentError)) return MESSAGES.unavailable;
  return error.code in REFUSALS ? REFUSALS[error.code as Refusal] : MESSAGES[error.code as Exclude<ReassignmentErrorCode, Refusal>];
}

const notConnected = () => Promise.reject(new ReassignmentError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedReassignmentsApi: ReassignmentsApi = {
  list: notConnected,
  get: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
