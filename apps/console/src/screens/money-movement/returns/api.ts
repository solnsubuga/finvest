/**
 * The console's view of payouts that came back, over the staff routes in
 * backend/app/modules/payouts/staff_returns_api.py (see "A payout that came back" in
 * docs/payouts.md).
 *
 * A paid payout can still come back: a bank account closed after the transfer left, or a mobile
 * money reversal. One member of staff picks the paid payout and the statement line that brought
 * the money back, from lists the backend offers, and
 * a different one approves. The ledger then owes the money to the client again, in their payout
 * pending account, and the client is told so they can send it again. The backend checks the
 * line, the two people and the entry. The screen decides nothing.
 */

export interface PayoutReturn {
  id: string;
  payoutId: string;
  clientId: string;
  /** The statement line on the pot the payout was paid from, coming in, for its amount. */
  statementLineId: string;
  reason: string;
  status: "proposed" | "approved" | "declined" | "withdrawn";
  proposedBy: string;
  proposedAt: string;
  /** What the second person checks: the payout and where it went, masked. */
  payout: ReturnedPayout;
  /** The line that brought it back. Null only once it has left the statement search window. */
  line: ReturnLine | null;
}

/** The payout a return names and where it went. The account is masked. */
export interface ReturnedPayout {
  kind: "bank" | "mobile_money";
  amount: Amount;
  bankName: string | null;
  /** `mtn` or `airtel` for mobile money. */
  network: string | null;
  maskedAccount: string;
  holderName: string;
}

export interface Amount {
  minor: number;
  currency: "UGX" | "USD";
}

/** A paid payout that could have come back, as staff pick it. The account is masked. */
export interface ReturnablePayout {
  id: string;
  clientId: string;
  kind: "bank" | "mobile_money";
  amount: Amount;
  paidAt: string;
  bankName: string | null;
  /** `mtn` or `airtel` for mobile money. */
  network: string | null;
  maskedAccount: string;
  holderName: string;
}

/** A statement line coming in that could be the chosen payout coming back. */
export interface ReturnLine {
  id: string;
  /** `YYYY-MM-DD`. */
  valueDate: string;
  amount: Amount;
  reference: string;
  narrative: string;
}

export interface ReturnsApi {
  /** Returns waiting for a second person, oldest first. */
  listWaiting(): Promise<PayoutReturn[]>;
  /** Paid payouts of the last 120 days with no return, newest paid first; only of this amount if given. */
  listReturnable(amount?: Amount): Promise<ReturnablePayout[]>;
  /** The lines coming in that could be this payout coming back, named by no other return. */
  listReturnLines(payoutId: string): Promise<ReturnLine[]>;
  propose(key: string, payoutId: string, statementLineId: string, reason: string): Promise<void>;
  approve(key: string, returnId: string, reason: string): Promise<void>;
  decline(returnId: string, reason: string): Promise<void>;
  withdraw(returnId: string, reason: string): Promise<void>;
}

export type ReturnsErrorCode =
  | "not_connected"
  | "unavailable"
  | "forbidden"
  | "refused"
  | "not_found"
  | "invalid"
  | "not_confirmed"
  | "payout_not_paid"
  | "statement_line_not_this_payout"
  | "statement_line_in_use"
  | "return_already_recorded"
  | "return_already_decided"
  | "proposed_by_you"
  | "not_proposed_by_you";

export class ReturnsError extends Error {
  constructor(readonly code: ReturnsErrorCode) {
    super(code);
    this.name = "ReturnsError";
  }
}

const MESSAGES: Record<ReturnsErrorCode, string> = {
  not_connected: "The console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Try again in a moment.",
  forbidden: "Your role cannot do this.",
  refused: "Finvest refused this as things now stand. The list has been refreshed.",
  not_found: "No payout or return has that ID.",
  invalid: "Finvest did not accept what was entered. Check the amount and the reason.",
  not_confirmed: "Nothing was done, because you did not confirm it is you.",
  payout_not_paid: "That payout is not paid, so nothing can come back from it.",
  statement_line_not_this_payout:
    "That line is not this payout coming back: it must be money in, on the account the payout was paid from, for its amount and currency, on or after the day it was paid.",
  statement_line_in_use: "Another return already names that statement line.",
  return_already_recorded: "This payout already has a return, open or approved. A payout comes back once.",
  return_already_decided: "This return has already been decided. The list has been refreshed.",
  proposed_by_you: "You proposed this, so a different person must decide it.",
  not_proposed_by_you: "Only the person who proposed this can withdraw it.",
};

export function returnsErrorMessage(error: unknown): string {
  return error instanceof ReturnsError ? MESSAGES[error.code] : MESSAGES.unavailable;
}

const notConnected = () => Promise.reject(new ReturnsError("not_connected"));

export const notConnectedReturnsApi: ReturnsApi = {
  listWaiting: notConnected,
  listReturnable: notConnected,
  listReturnLines: notConnected,
  propose: notConnected,
  approve: notConnected,
  decline: notConnected,
  withdraw: notConnected,
};
