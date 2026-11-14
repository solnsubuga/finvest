/**
 * The console's view of payments credited by mistake: taking one back while its money still
 * waits, and recording what the client owes once the money has left.
 *
 * The shapes follow the staff routes in backend/app/modules/payments/reversal_routes.py and
 * receivable_routes.py (see "Correcting a mistaken credit" in docs/payments.md).
 *
 * Every step is proposed by one member of staff and approved by a different one. A reversal is
 * then carried out by the worker; a receivable step posts its entry as it is approved. The
 * backend enforces every rule. The screen only shows what the backend allows.
 */

import type { Amount } from "../api.ts";

/** `reverse` takes the payment back. The other three are the steps of a receivable. */
export type Step = "reverse" | "open" | "recover" | "write_off";

export type Kind = "reversal" | "receivable";

export const kindOf = (step: Step): Kind => (step === "reverse" ? "reversal" : "receivable");

/**
 * A reversal is approved, then done or failed by the worker. A receivable step is applied as it
 * is approved.
 */
export type CorrectionStatus = "proposed" | "approved" | "done" | "failed" | "applied" | "sent_back" | "withdrawn";

export type Method = "mtn" | "airtel" | "bank_transfer";

/** Why the backend would refuse the step right now. */
export type Refusal =
  | "not_confirmed"
  | "not_for_an_investment"
  | "already_reversed"
  | "not_a_payment"
  | "money_moved_on"
  | "unwind_first"
  | "reverse_instead"
  | "already_open"
  | "not_open"
  | "already_cleared";

export interface CorrectionSummary {
  id: string;
  step: Step;
  status: CorrectionStatus;
  depositId: string;
  clientId: string;
  /** Only for a reversal: the investment the payment was for. */
  investmentId: string | null;
  amount: Amount;
  reason: string;
  proposedAt: string;
  decidedAt: string | null;
  /** Why it was sent back. */
  decisionNote: string | null;
  /** Only for a reversal the worker could not carry out: why. Nothing moved. */
  failureCode: string | null;
}

export interface CorrectionDetail extends CorrectionSummary {
  method: Method;
  /**
   * For a reversal, what still waits for the investment. For a receivable step, what the client
   * owes for this payment now.
   */
  balance: Amount;
  /** For an open proposal: why approving would be refused now. */
  refusal: Refusal | null;
  /** The person reading may approve or send it back. */
  canDecide: boolean;
  /** The person reading proposed it. They may only withdraw it. */
  yours: boolean;
}

export interface Proposal {
  step: Step;
  depositId: string;
  reason: string;
}

/**
 * Every command takes an idempotency key made once per attempt by the screen, so a repeat from a
 * weak connection is replayed and never applied twice.
 */
export interface MistakenPaymentsApi {
  /** Reversals and receivable steps, newest first. */
  list(): Promise<CorrectionSummary[]>;
  /** The opening step of every receivable not yet recovered or written off. */
  outstanding(): Promise<CorrectionSummary[]>;
  get(kind: Kind, id: string): Promise<CorrectionDetail>;
  propose(key: string, proposal: Proposal): Promise<CorrectionDetail>;
  approve(key: string, kind: Kind, id: string): Promise<CorrectionDetail>;
  sendBack(key: string, kind: Kind, id: string, note: string): Promise<CorrectionDetail>;
  withdraw(key: string, kind: Kind, id: string): Promise<CorrectionDetail>;
}

export type CorrectionErrorCode =
  | Refusal
  | "insufficient_funds"
  | "deposit_not_found"
  | "not_found"
  | "conflict"
  | "forbidden"
  | "confirm_it_is_you"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class CorrectionError extends Error {
  readonly code: CorrectionErrorCode;
  /** The form field the refusal is about, when there is one. */
  readonly field: "depositId" | "reason" | "note" | null;

  constructor(code: CorrectionErrorCode, field: CorrectionError["field"] = null) {
    super(code);
    this.name = "CorrectionError";
    this.code = code;
    this.field = field;
  }
}

export const STEP_LABELS: Record<Step, string> = {
  reverse: "Take the payment back",
  open: "Record what the client owes",
  recover: "The money arrived late",
  write_off: "Write it off as a loss",
};

/** What a refusal means, worded for staff. Used for the live check on a proposal too. */
export const REFUSALS: Record<Refusal, string> = {
  not_confirmed: "This payment is not confirmed, so no money was credited. Check it on the bank transfers list instead.",
  not_for_an_investment: "This payment is not for an investment.",
  already_reversed: "This payment has already been taken back.",
  not_a_payment:
    "This payment was saved when another payment was moved to this client. Put it right by moving it again, under payments credited to the wrong client.",
  money_moved_on:
    "The whole amount is no longer waiting for the investment. If the investment has started, undo it first on the investments screen. If the money has left, record what the client owes instead.",
  unwind_first:
    "The investment is still running. Undo it first on the investments screen, then take the payment back.",
  reverse_instead: "The money is still waiting for the investment, so take the payment back instead.",
  already_open: "What the client owes for this payment is already recorded.",
  not_open: "Nothing owed is recorded for this payment yet. Record what the client owes first.",
  already_cleared: "What the client owed for this payment has already been recovered or written off.",
};

const MESSAGES: Record<Exclude<CorrectionErrorCode, Refusal>, string> = {
  insufficient_funds:
    "The account this money came from no longer holds the amount. Finance needs to look at it before anything is recorded.",
  deposit_not_found: "No payment has that ID.",
  not_found: "This step no longer exists.",
  conflict: "Someone else has already acted on this, or another step for this payment is open. It has been reloaded.",
  forbidden: "You cannot do this. The person who proposed a step cannot approve it, and only they can withdraw it.",
  confirm_it_is_you: "Approving needs you to confirm it is you with your passkey or authenticator app. Try again when you are ready.",
  invalid: "Check the details and try again.",
  not_connected: "Putting mistaken payments right is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function correctionErrorMessage(error: unknown): string {
  if (!(error instanceof CorrectionError)) return MESSAGES.unavailable;
  return error.code in REFUSALS ? REFUSALS[error.code as Refusal] : MESSAGES[error.code as Exclude<CorrectionErrorCode, Refusal>];
}

const notConnected = () => Promise.reject(new CorrectionError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedMistakenPaymentsApi: MistakenPaymentsApi = {
  list: notConnected,
  outstanding: notConnected,
  get: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
