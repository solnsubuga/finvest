/**
 * The console's view of the liquidity buffer: its rules, and Finvest's own money in it.
 *
 * The shapes follow backend/app/modules/dealing/buffer_api.py and docs/dealing-desk.md.
 * Each change is proposed by one member of staff and approved by a different one. The
 * console never works out a money figure, a date rule or who may act: every amount, status
 * and allowed step comes from the backend. Forms check what was typed only to save a round
 * trip, and the backend checks it again.
 */

import type { Amount, BufferLevel, Currency } from "./api.ts";

export type ProposalStatus = "proposed" | "approved" | "sent_back" | "withdrawn";
export type MovementStatus = "proposed" | "approved" | "declined";
export type Direction = "in" | "out";
/** What the signed-in person may do with a change as it stands. The backend decides. */
export type BufferStep = "approve" | "send_back" | "decline" | "withdraw";

export interface Rules {
  effectiveFrom: string;
  target: Amount;
  floor: Amount;
  fromBufferUpTo: Amount;
  payWithinWorkingDays: number;
}

export interface RulesProposal {
  id: string;
  rules: Rules;
  reason: string;
  status: ProposalStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  allowed: readonly BufferStep[];
}

export interface Movement {
  id: string;
  direction: Direction;
  amount: Amount;
  /** Paying in: the bank's reference. Taking out: the transfer quotes `id`. */
  bankReference: string | null;
  /** Paying in: the day it arrived. Taking out: the day it was approved, once it is. */
  valueDate: string | null;
  reason: string;
  status: MovementStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  allowed: readonly BufferStep[];
}

export interface BufferScreen {
  currency: Currency;
  /** The buffer today, after the early withdrawals already promised. */
  buffer: Amount;
  level: BufferLevel;
  inForce: Rules;
  /** Every version, oldest first, including any that start later. */
  versions: readonly Rules[];
  /** Newest first. */
  proposals: readonly RulesProposal[];
  /** What Finvest has in the buffer: paid in, less taken out. */
  finvestMoney: Amount;
  /** Newest first. */
  movements: readonly Movement[];
}

export interface NewRules extends Rules {
  reason: string;
}

export type NewMovement =
  | { direction: "in"; amount: Amount; reason: string; bankReference: string; valueDate: string }
  | { direction: "out"; amount: Amount; reason: string };

/** Every command takes an idempotency key made once per attempt by the screen. */
export interface BufferApi {
  getBuffer(currency: Currency): Promise<BufferScreen>;
  proposeRules(key: string, rules: NewRules): Promise<BufferScreen>;
  approveRules(key: string, proposalId: string): Promise<BufferScreen>;
  sendBackRules(key: string, proposalId: string, note: string): Promise<BufferScreen>;
  withdrawRules(key: string, proposalId: string): Promise<BufferScreen>;
  proposeMovement(key: string, movement: NewMovement): Promise<BufferScreen>;
  approveMovement(key: string, movementId: string): Promise<BufferScreen>;
  declineMovement(key: string, movementId: string, reason: string): Promise<BufferScreen>;
}

export type BufferErrorCode =
  | "not_found"
  | "conflict"
  | "forbidden"
  | "not_confirmed"
  | "effective_date_not_in_future"
  | "date_already_taken"
  | "nothing_changes"
  | "rules_inconsistent"
  | "reference_required"
  | "already_recorded"
  | "value_date_in_future"
  | "value_date_in_a_closed_day"
  | "day_closed_decline_and_record_again"
  | "more_than_paid_in"
  | "more_than_the_buffer"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class BufferError extends Error {
  readonly code: BufferErrorCode;

  constructor(code: BufferErrorCode) {
    super(code);
    this.name = "BufferError";
    this.code = code;
  }
}

const MESSAGES: Record<BufferErrorCode, string> = {
  not_found: "This change no longer exists.",
  conflict: "Someone else has already decided this. The buffer has been reloaded.",
  forbidden: "A second person has to do this, and you may not approve your own change.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  effective_date_not_in_future: "New rules start on a later day than today.",
  date_already_taken: "Another version of the rules already starts on that day.",
  nothing_changes: "These are the rules already in force on that day.",
  rules_inconsistent: "The floor must be below the target, and one withdrawal from the buffer no more than the floor.",
  reference_required: "Enter the bank's reference for the transfer.",
  already_recorded: "A payment with this bank reference is already recorded.",
  value_date_in_future: "Record money once it is on the trust account, on or before today.",
  value_date_in_a_closed_day: "That day is closed in the books. Record it on an open day.",
  day_closed_decline_and_record_again: "The day this money arrived has been closed since it was recorded. Decline it, and record it again on an open day.",
  more_than_paid_in: "That is more than Finvest has paid into the buffer.",
  more_than_the_buffer: "That is more than the buffer holds now. Client money never leaves with it.",
  invalid: "Check the figures and try again.",
  not_connected: "The buffer is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bufferErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BufferError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new BufferError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedBufferApi: BufferApi = {
  getBuffer: notConnected,
  proposeRules: notConnected,
  approveRules: notConnected,
  sendBackRules: notConnected,
  withdrawRules: notConnected,
  proposeMovement: notConnected,
  approveMovement: notConnected,
  declineMovement: notConnected,
};
