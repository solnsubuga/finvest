/**
 * The console's view of investment corrections: undoing an investment started
 * by mistake, refunding money that never started one, and sending a matured
 * term's held money to another of the client's payout accounts.
 *
 * The shapes follow `/staff/investment-corrections` in
 * backend/app/modules/investments/api.py (see "Undoing a mistake" in
 * docs/investing.md). One person proposes, a different person approves, and
 * Finvest then moves the money. `http.ts` calls the routes; `stub.ts` is the
 * sample for `pnpm dev` and previews.
 */

import type { Amount } from "../../products/api.ts";

export type CorrectionKind = "unwind" | "refund" | "pay_out";
export type CorrectionStatus = "proposed" | "approved" | "sent_back" | "withdrawn" | "done" | "failed";

export interface Correction {
  id: string;
  kind: CorrectionKind;
  investmentId: string;
  clientId: string;
  /** The principal for an unwind, the money waiting for a refund or a pay out. Fixed when proposed. */
  amount: Amount;
  /** A refund's or pay out's payout account. */
  destinationId: string | null;
  reason: string;
  status: CorrectionStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was sent back. */
  decisionNote: string | null;
  doneAt: string | null;
  payoutId: string | null;
  /** Why Finvest moved nothing after it was approved. */
  failureCode: string | null;
}

export interface NewCorrection {
  kind: CorrectionKind;
  investmentId: string;
  reason: string;
  /** Required for a refund or a pay out, never sent for an unwind. */
  destinationId: string | null;
}

/**
 * Each command takes an idempotency key. The screen keeps one per attempt, so
 * a retry after a lost answer is the same request.
 */
export interface CorrectionsApi {
  /** Newest first. */
  list(): Promise<readonly Correction[]>;
  propose(key: string, correction: NewCorrection): Promise<Correction>;
  approve(key: string, correctionId: string): Promise<Correction>;
  sendBack(key: string, correctionId: string, note: string): Promise<Correction>;
  withdraw(key: string, correctionId: string): Promise<Correction>;
}

export type CorrectionField = "investment_id" | "reason" | "destination_id" | "note";

export interface CorrectionProblem {
  field: CorrectionField;
  /** The backend's code, such as `carried_over` or `over_mobile_money_limit`. */
  code: string;
}

export type CorrectionsErrorCode =
  | "invalid"
  | "conflict"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class CorrectionsError extends Error {
  constructor(
    readonly code: CorrectionsErrorCode,
    /** For `invalid`: what the backend refused, field by field. */
    readonly problems: readonly CorrectionProblem[] = [],
  ) {
    super(code);
    this.name = "CorrectionsError";
  }
}

const MESSAGES: Record<CorrectionsErrorCode, string> = {
  invalid: "Finvest refused this. Fix what is marked and try again.",
  conflict:
    "Finvest refused this as things now stand: the investment is not in a state for it, it already has one open, or it was decided already. Here is the list as it now stands.",
  same_person: "Someone else has to decide this. You proposed it, so you can only withdraw it.",
  forbidden: "You are not allowed to do this. Ask an admin.",
  not_found: "Finvest could not find that investment or correction.",
  not_connected: "Corrections are not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function correctionsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof CorrectionsError ? error.code : "unavailable"];
}

const PROBLEMS: Record<string, string> = {
  required: "Fill this in.",
  too_long: "Keep it to 500 characters.",
  invalid: "That is not an ID Finvest uses.",
  carried_over: "This investment came over from Finvest 1.0, so it is never undone here. Ask the migration team.",
  nothing_waiting: "No money is waiting to be refunded for this investment.",
  not_for_unwind: "An unwind puts the money back for the client. Leave the payout account empty.",
  not_usable: "That is not one of the client's approved payout accounts.",
  destination_in_safety_wait: "That payout account is still in its first day's safety wait.",
  over_mobile_money_limit: "The amount is over what one mobile money payout can carry. Choose a bank account.",
  over_bank_limit: "The amount is over what one bank payout can carry.",
  over_daily_limit: "The amount is over the client's daily payout limit.",
  currency_not_supported: "That payout account cannot be paid in this currency.",
};

export function correctionProblemText(code: string): string {
  return PROBLEMS[code] ?? "Finvest did not accept this. Change it and try again.";
}

const FAILURES: Record<string, string> = {
  investment_not_running: "The investment was no longer running.",
  investment_not_refundable: "The investment was no longer in a state to refund.",
  investment_not_held: "The money was no longer waiting: the client chose where to send it, or it was sent already.",
  amount_changed: "The amount changed after it was proposed.",
  holding_changed: "The money held for the investment changed after it was proposed.",
  interest_changed: "The interest owed changed after it was proposed.",
  tax_already_remitted: "The tax on its interest was already paid over to URA. Put that right with URA first.",
};

/** Why an approved correction moved nothing. Payouts codes for a refund or pay out fall back to the code itself. */
export function failureText(code: string): string {
  return FAILURES[code] ?? PROBLEMS[code] ?? `Refused by payouts: ${code}`;
}

const notConnected = () => Promise.reject(new CorrectionsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedCorrectionsApi: CorrectionsApi = {
  list: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
