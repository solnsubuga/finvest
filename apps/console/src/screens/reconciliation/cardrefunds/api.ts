/**
 * The console's view of card refunds: paying a confirmed card payment back to the same card
 * through Pesapal, by two people.
 *
 * The shapes follow the staff routes in backend/app/modules/payments/provider_refund_routes.py
 * (see "Paying a card payment back" in docs/payments.md). One member of staff proposes, a
 * different one approves, and the worker holds the money and asks Pesapal once. Pesapal's yes
 * only means it took the request, so a refund waits as `requested` until Pesapal shows it paid,
 * or a member of staff who is not the proposer says what Pesapal shows. The backend enforces
 * every rule. The screen only shows what the backend allows.
 */

import type { Amount } from "../api.ts";

export type RefundStatus =
  | "proposed"
  | "approved"
  | "sent_back"
  | "withdrawn"
  | "sending"
  | "requested"
  | "uncertain"
  | "done"
  | "failed";

/** What a member of staff saw at Pesapal. */
export type Found = "requested" | "paid" | "not_paid";

/** Why the backend would refuse approving right now. */
export type Refusal =
  | "not_confirmed"
  | "not_for_an_investment"
  | "not_refundable"
  | "already_taken_back"
  | "not_a_payment"
  | "no_confirmation_code"
  | "money_moved_on";

export interface RefundSummary {
  id: string;
  status: RefundStatus;
  depositId: string;
  clientId: string;
  investmentId: string;
  /** The investment's amount. */
  amount: Amount;
  /** The card charge the client paid on top. */
  charge: Amount;
  /** Finvest's own mistake, so the card charge goes back too. */
  finvestErred: boolean;
  /** What goes back to the card. */
  total: Amount;
  reason: string;
  proposedAt: string;
  decidedAt: string | null;
  /** Why it was sent back. */
  decisionNote: string | null;
  sentAt: string | null;
  found: Found | null;
  foundAt: string | null;
  foundNote: string | null;
  doneAt: string | null;
  /** Why it failed. The money was put back for the investment. */
  failureCode: string | null;
}

export interface RefundDetail extends RefundSummary {
  cardBrand: string | null;
  cardLastFour: string | null;
  /** What waits for the investment now. Paying back needs at least the payment's amount. */
  waiting: Amount;
  /** For an open proposal: why approving would be refused now. */
  refusal: Refusal | null;
  /** The person reading may approve or send it back. */
  canDecide: boolean;
  /** The person reading may say what Pesapal shows. */
  canRecordFinding: boolean;
  /** The person reading proposed it. They may only withdraw it. */
  yours: boolean;
}

export interface Proposal {
  depositId: string;
  reason: string;
  finvestErred: boolean;
}

/**
 * Every command takes an idempotency key made once per attempt by the screen, so a repeat from a
 * weak connection is replayed and never applied twice.
 */
export interface CardRefundsApi {
  /** Card refunds, newest first. */
  list(): Promise<RefundSummary[]>;
  get(id: string): Promise<RefundDetail>;
  propose(key: string, proposal: Proposal): Promise<RefundDetail>;
  approve(key: string, id: string): Promise<RefundDetail>;
  sendBack(key: string, id: string, note: string): Promise<RefundDetail>;
  withdraw(key: string, id: string): Promise<RefundDetail>;
  recordFinding(key: string, id: string, found: Found, note: string): Promise<RefundDetail>;
}

export type RefundErrorCode =
  | Refusal
  | "deposit_not_found"
  | "not_found"
  | "conflict"
  | "forbidden"
  | "confirm_it_is_you"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class RefundError extends Error {
  readonly code: RefundErrorCode;
  /** The form field the refusal is about, when there is one. */
  readonly field: "depositId" | "reason" | "note" | null;

  constructor(code: RefundErrorCode, field: RefundError["field"] = null) {
    super(code);
    this.name = "RefundError";
    this.code = code;
    this.field = field;
  }
}

/** What a refusal means, worded for staff. Used for the live check on a proposal too. */
export const REFUSALS: Record<Refusal, string> = {
  not_confirmed: "This payment is not confirmed, so there is nothing to pay back.",
  not_for_an_investment: "This payment is not for an investment.",
  not_refundable: "Only a card payment through Pesapal can be paid back to the card.",
  already_taken_back: "This payment has already been taken back or paid back.",
  not_a_payment:
    "This payment was saved when another payment was moved to this client. Put it right by moving it again, under payments credited to the wrong client.",
  no_confirmation_code: "Pesapal has not given this payment a confirmation code, which a refund needs. Check the payment on Pesapal's dashboard.",
  money_moved_on:
    "The whole amount is no longer waiting for the investment. If the investment has started, undo it first on the investments screen.",
};

const MESSAGES: Record<Exclude<RefundErrorCode, Refusal>, string> = {
  deposit_not_found: "No payment has that ID.",
  not_found: "This refund no longer exists.",
  conflict: "Someone else has already acted on this, or this payment already has a refund under way. It has been reloaded.",
  forbidden: "You cannot do this. The person who proposed a refund cannot approve it or say what Pesapal shows, and only they can withdraw it.",
  confirm_it_is_you: "This needs you to confirm it is you with your passkey or authenticator app. Try again when you are ready.",
  invalid: "Check the details and try again.",
  not_connected: "Card refunds are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function refundErrorMessage(error: unknown): string {
  if (!(error instanceof RefundError)) return MESSAGES.unavailable;
  return error.code in REFUSALS ? REFUSALS[error.code as Refusal] : MESSAGES[error.code as Exclude<RefundErrorCode, Refusal>];
}

const notConnected = () => Promise.reject(new RefundError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedCardRefundsApi: CardRefundsApi = {
  list: notConnected,
  get: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
  recordFinding: notConnected,
};
