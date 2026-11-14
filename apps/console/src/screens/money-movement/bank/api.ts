/**
 * The console's view of bank payouts, over the staff routes in
 * backend/app/modules/payouts/staff_bank_api.py (see "Paying bank payouts" in docs/payouts.md).
 *
 * Finvest pays bank payouts from the trust account through the bank's portal:
 *
 * 1. One member of staff puts queued bank payouts in one currency into a batch.
 * 2. A different one approves it, and the payouts count as sent.
 * 3. Staff download the batch's transfer file and upload it in the bank's portal.
 * 4. Each payout is settled from the trust statement: one person proposes paid (the line going
 *    out) or failed (the statements after it, with no such line), and a different one approves.
 *
 * The backend checks the two people, the evidence and every state. The screen decides nothing.
 */

import type { Amount, Currency } from "../api.ts";

export type { Amount, Currency };

export interface BankPayout {
  id: string;
  clientId: string;
  destinationId: string;
  amount: Amount;
  status: "queued" | "sent" | "paid" | "failed";
  /** The Kampala business day it was asked for, `YYYY-MM-DD`. */
  businessDate: string;
  createdAt: string;
}

export type BatchStatus = "prepared" | "approved" | "declined" | "withdrawn";

/** One payout in a batch, as the second person checks it. The account is masked. */
export interface BatchItem {
  payoutId: string;
  clientId: string;
  amount: Amount;
  bankName: string | null;
  maskedAccount: string;
  holderName: string;
}

export interface Batch {
  id: string;
  currency: Currency;
  reason: string;
  status: BatchStatus;
  preparedBy: string;
  preparedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  payoutIds: string[];
  total: Amount;
  /** Each payout with its masked account. The list gives them for a prepared batch only. */
  items: BatchItem[] | null;
}

export type Outcome = "paid" | "failed";

export interface Settlement {
  id: string;
  payoutId: string;
  outcome: Outcome;
  statementLineId: string | null;
  statementId: string | null;
  reason: string;
  proposedBy: string;
  proposedAt: string;
}

export interface SentBankPayout {
  payout: BankPayout;
  batchId: string;
  sentAt: string;
  /** The open proposal, waiting for a different person. Null until someone proposes. */
  settlement: Settlement | null;
}

export interface SettlementProposal {
  outcome: Outcome;
  /** For `paid`: the trust statement line going out for this payout's amount. */
  statementLineId?: string;
  /** For `failed`: a trust statement at least two days after the batch was approved. */
  statementId?: string;
  reason: string;
}

/**
 * Commands that move money take an idempotency key made once per attempt by the screen, so a
 * repeat from a weak connection is replayed and never applied twice.
 */
export interface BankPayoutsApi {
  listWaiting(currency: Currency): Promise<BankPayout[]>;
  /** Batches waiting for a second person, and approved ones whose file can be downloaded. */
  listBatches(): Promise<Batch[]>;
  prepare(key: string, currency: Currency, payoutIds: string[], reason: string): Promise<void>;
  approve(key: string, batchId: string, reason: string): Promise<void>;
  decline(key: string, batchId: string, reason: string): Promise<void>;
  withdraw(batchId: string, reason: string): Promise<void>;
  /** The approved batch's transfer file for the bank's portal, with whole account numbers. */
  downloadFile(batchId: string): Promise<Blob>;
  listSent(): Promise<SentBankPayout[]>;
  propose(key: string, payoutId: string, proposal: SettlementProposal): Promise<void>;
  approveSettlement(key: string, settlementId: string, reason: string): Promise<void>;
  declineSettlement(settlementId: string, reason: string): Promise<void>;
  withdrawSettlement(settlementId: string, reason: string): Promise<void>;
}

export type BankPayoutsErrorCode =
  | "not_connected"
  | "unavailable"
  | "forbidden"
  | "refused"
  | "not_found"
  | "invalid"
  | "not_confirmed"
  | "batch_payouts_invalid"
  | "currency_not_supported"
  | "payout_not_bank"
  | "payout_not_queued"
  | "payout_in_another_batch"
  | "batch_already_decided"
  | "batch_not_approved"
  | "prepared_by_you"
  | "not_prepared_by_you"
  | "payout_not_sent"
  | "statement_line_required"
  | "statement_required"
  | "statement_line_not_this_payout"
  | "statement_not_enough"
  | "statement_line_in_use"
  | "settlement_already_proposed"
  | "settlement_already_decided"
  | "proposed_by_you"
  | "not_proposed_by_you";

export class BankPayoutsError extends Error {
  constructor(readonly code: BankPayoutsErrorCode) {
    super(code);
    this.name = "BankPayoutsError";
  }
}

const MESSAGES: Record<BankPayoutsErrorCode, string> = {
  not_connected: "The console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Try again in a moment.",
  forbidden: "Your role cannot do this.",
  refused: "Finvest refused this as things now stand. The list has been refreshed.",
  not_found: "This batch or payout no longer exists. The list has been refreshed.",
  invalid: "Finvest did not accept what was entered. Check the reason and the IDs.",
  not_confirmed: "Nothing was done, because you did not confirm it is you.",
  batch_payouts_invalid: "Choose between 1 and 200 payouts, each once.",
  currency_not_supported: "Every payout in a batch must be in the batch's currency.",
  payout_not_bank: "One of these payouts is not a bank payout.",
  payout_not_queued: "One of these payouts is no longer waiting to be sent. The list has been refreshed.",
  payout_in_another_batch: "One of these payouts is already in another batch. The list has been refreshed.",
  batch_already_decided: "This batch has already been decided. The list has been refreshed.",
  batch_not_approved: "The transfer file exists only once a second person has approved the batch.",
  prepared_by_you: "You prepared this batch, so a different person must approve or decline it.",
  not_prepared_by_you: "Only the person who prepared this batch can withdraw it.",
  payout_not_sent: "This payout is no longer waiting to be settled. The list has been refreshed.",
  statement_line_required: "Enter the ID of the trust statement line that shows this payout going out.",
  statement_required: "Enter the ID of the trust statement that shows this payout never left.",
  statement_line_not_this_payout:
    "That line is not this payout going out of the trust account for its amount, on or after the day the batch was approved.",
  statement_not_enough:
    "That statement does not show the payout never left. It must be at least two days after the batch was approved, every day in between must be imported, and no line going out for this amount may be unaccounted for.",
  statement_line_in_use: "That statement line already settles another payout.",
  settlement_already_proposed: "Someone has already proposed how this payout ended. The list has been refreshed.",
  settlement_already_decided: "This proposal has already been decided. The list has been refreshed.",
  proposed_by_you: "You proposed this, so a different person must decide it.",
  not_proposed_by_you: "Only the person who proposed this can withdraw it.",
};

export function bankPayoutsErrorMessage(error: unknown): string {
  return error instanceof BankPayoutsError ? MESSAGES[error.code] : MESSAGES.unavailable;
}

const notConnected = () => Promise.reject(new BankPayoutsError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedBankPayoutsApi: BankPayoutsApi = {
  listWaiting: notConnected,
  listBatches: notConnected,
  prepare: notConnected,
  approve: notConnected,
  decline: notConnected,
  withdraw: notConnected,
  downloadFile: notConnected,
  listSent: notConnected,
  propose: notConnected,
  approveSettlement: notConnected,
  declineSettlement: notConnected,
  withdrawSettlement: notConnected,
};
