/**
 * The console's view of payouts that need a person, over the staff routes in
 * backend/app/modules/payouts/staff_payouts_api.py (see docs/payouts.md).
 *
 * Held up: queued mobile money payouts the provider has taken nothing of for
 * two hours. The worker keeps sending them; a person finds out why.
 *
 * Never answered for (stranded): sent or uncertain payouts whose status reads
 * ended with no answer. Two people settle one from the ioTec statement: one
 * proposes paid or failed with the evidence, a different one approves. The
 * backend checks the evidence and the two people. The screen decides nothing.
 */

export type Currency = "UGX" | "USD";

/** An integer in the smallest unit: whole shillings for UGX, cents for USD. */
export interface Amount {
  minor: number;
  currency: Currency;
}

export interface HeldUpPayout {
  id: string;
  clientId: string;
  amount: Amount;
  createdAt: string;
  firstTriedAt: string;
  lastTriedAt: string;
  /** Sends tried so far. Each ended with the provider taking nothing. */
  tries: number;
}

export type Outcome = "paid" | "failed";

export interface Resolution {
  id: string;
  outcome: Outcome;
  statementLineId: string | null;
  statementId: string | null;
  reason: string;
  proposedBy: string;
  proposedAt: string;
}

export interface StrandedPayout {
  id: string;
  clientId: string;
  status: "sent" | "uncertain";
  amount: Amount;
  firstSentAt: string;
  /** The open proposal, waiting for a different person. Null until someone proposes. */
  resolution: Resolution | null;
}

export interface Proposal {
  outcome: Outcome;
  /** For `paid`: the ioTec statement line with the payout's ID, going out, for its amount. */
  statementLineId?: string;
  /** For `failed`: an ioTec statement for the wallet at least three days after the send. */
  statementId?: string;
  reason: string;
}

export interface PayoutsApi {
  listHeldUp(): Promise<HeldUpPayout[]>;
  listStranded(): Promise<StrandedPayout[]>;
  /** `key` is the idempotency key. Every try at the same proposal carries the same key. */
  propose(key: string, payoutId: string, proposal: Proposal): Promise<void>;
  approve(key: string, resolutionId: string, reason: string): Promise<void>;
  decline(resolutionId: string, reason: string): Promise<void>;
  withdraw(resolutionId: string, reason: string): Promise<void>;
}

/** Refusals the screen words for staff. Anything else reads as "could not reach Finvest". */
export type PayoutsErrorCode =
  | "not_connected"
  | "unavailable"
  | "forbidden"
  | "not_found"
  | "invalid"
  | "not_confirmed"
  | "provider_still_being_read"
  | "payout_not_stranded"
  | "statement_line_required"
  | "statement_required"
  | "statement_line_not_this_payout"
  | "payout_on_statement"
  | "statement_not_this_wallet"
  | "statement_too_early"
  | "statement_day_missing"
  | "resolution_already_proposed"
  | "proposed_by_you"
  | "not_proposed_by_you"
  | "resolution_already_decided";

export class PayoutsError extends Error {
  constructor(readonly code: PayoutsErrorCode) {
    super(code);
    this.name = "PayoutsError";
  }
}

const MESSAGES: Record<PayoutsErrorCode, string> = {
  not_connected: "The console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Try again in a moment.",
  forbidden: "Your role cannot do this.",
  not_found: "This payout or proposal no longer exists. The list has been refreshed.",
  invalid: "Finvest did not accept what was entered. Check the reason and the IDs.",
  not_confirmed: "Nothing was done, because you did not confirm it is you.",
  provider_still_being_read:
    "ioTec is still being asked about this payout. Its answer counts until the reads end.",
  payout_not_stranded: "This payout is no longer waiting for an answer.",
  statement_line_required: "Enter the ID of the statement line that shows this payout going out.",
  statement_required: "Enter the ID of the statement that shows this payout never left.",
  statement_line_not_this_payout:
    "That line is not this payout going out of its wallet for its amount. Check the line ID.",
  payout_on_statement:
    "This payout's ID is on an ioTec statement, so it did not simply fail. Look at that line, and propose paid if it went out.",
  statement_not_this_wallet:
    "That statement is not for the wallet this payout left from, in its currency.",
  statement_too_early: "Name a statement for a day at least three days after the payout was sent.",
  statement_day_missing:
    "A day between the send and that statement has no imported statement for this wallet. Import every day first.",
  resolution_already_proposed: "Someone has already proposed how this payout ended. The list has been refreshed.",
  proposed_by_you: "You proposed this, so a different person must decide it.",
  not_proposed_by_you: "Only the person who proposed this can withdraw it.",
  resolution_already_decided: "This proposal has already been decided. The list has been refreshed.",
};

export function payoutsErrorMessage(error: unknown): string {
  return error instanceof PayoutsError ? MESSAGES[error.code] : MESSAGES.unavailable;
}

/** Until the API client exists, a normal build has nothing to show. */
export const notConnectedPayoutsApi: PayoutsApi = {
  listHeldUp: () => Promise.reject(new PayoutsError("not_connected")),
  listStranded: () => Promise.reject(new PayoutsError("not_connected")),
  propose: () => Promise.reject(new PayoutsError("not_connected")),
  approve: () => Promise.reject(new PayoutsError("not_connected")),
  decline: () => Promise.reject(new PayoutsError("not_connected")),
  withdraw: () => Promise.reject(new PayoutsError("not_connected")),
};
