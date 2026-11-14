/**
 * The console's view of the review queue: payout accounts waiting for a proof check and its
 * second approval, and payouts held because they are large.
 *
 * The shapes follow the staff routes in docs/payouts.md ("Staff routes" and "Held payouts").
 * `http.ts` calls them; `stub.ts` is the sample for `pnpm dev` and previews.
 */

export type AccountKind = "mobile_money" | "bank";
export type Network = "mtn" | "airtel";
export type AccountStatus = "awaiting_code" | "awaiting_proof" | "in_review" | "approved" | "rejected" | "removed";
export type ProofKind = "bank_statement" | "bank_letter" | "bank_card" | "network_registered_name";
export type NameMatch = "exact" | "partial";
export type RejectionReason =
  | "name_different"
  | "proof_unreadable"
  | "proof_too_old"
  | "proof_wrong_account"
  | "proof_wrong_document"
  | "suspected_fraud";
export type Currency = "UGX" | "USD";

/** An amount in the smallest unit of its currency. Never a float. */
export interface Amount {
  minor: number;
  currency: Currency;
}

/** The first person's check of the current proof. */
export interface AccountCheck {
  proofName: string;
  nameMatch: NameMatch;
  /** `YYYY-MM-DD`, the date printed on a bank statement. */
  statementDate: string | null;
  checkedBy: string;
  checkedAt: string;
}

/** An account as the queue lists it. The number is masked. */
export interface PayoutAccount {
  id: string;
  clientId: string;
  kind: AccountKind;
  network: Network | null;
  bankCode: string | null;
  /** Masked, such as `••••4821`. */
  account: string;
  /** The name the client gave for the account. */
  holderName: string;
  status: AccountStatus;
  proofKind: ProofKind | null;
  /** Null until the first person checks it. With a check it waits for a different person. */
  check: AccountCheck | null;
  rejectionReason: RejectionReason | null;
  approvedBy: string | null;
  approvedAt: string | null;
  /** When it can first be paid: 24 hours after the approval. */
  payableFrom: string | null;
  createdAt: string;
}

/** One account opened to check it. Opening is written to the audit trail. */
export interface PayoutAccountDetail extends PayoutAccount {
  accountNumber: string;
  /** The name on the client's verified ID. Null without an approved identity check. */
  idName: string | null;
  /** A link to the proof that works for five minutes. Null when there is no file to show. */
  proofLink: { url: string; expiresAt: string } | null;
}

export interface NewCheck {
  proofName: string;
  /** Needed for a bank statement, ignored otherwise. */
  statementDate: string | null;
  reason: string;
}

/** A payout above the review threshold, waiting for a member of staff. */
export interface HeldPayout {
  id: string;
  clientId: string;
  destinationId: string;
  kind: AccountKind;
  amount: Amount;
  /** `YYYY-MM-DD`, the Kampala business day it was asked for. */
  businessDate: string;
  createdAt: string;
}

export type HeldDecision = "approve" | "decline";

export interface DecidedPayout {
  id: string;
  decision: "approved" | "declined";
  status: "queued" | "sent" | "uncertain" | "paid" | "failed" | "cancelled";
}

export interface ReviewQueueApi {
  /** Accounts in review, oldest first. */
  listAccounts(): Promise<PayoutAccount[]>;
  /** The whole number, the ID name and a proof link. Each call is an audit event. */
  openAccount(id: string): Promise<PayoutAccountDetail>;
  check(id: string, check: NewCheck): Promise<PayoutAccount>;
  /** Approves someone else's check. Needs the person to have confirmed it is them. */
  approve(id: string, reason: string): Promise<PayoutAccount>;
  reject(id: string, rejectionReason: RejectionReason, reason: string): Promise<PayoutAccount>;
  remove(id: string, reason: string): Promise<PayoutAccount>;
  /** Held payouts, oldest first, at most 100. */
  listHeld(): Promise<HeldPayout[]>;
  /**
   * Approves or declines a held payout. The key belongs to the decision: the same key for a
   * retry of the same decision and reason, a new one when either changes.
   */
  decideHeld(id: string, decision: HeldDecision, reason: string, idempotencyKey: string): Promise<DecidedPayout>;
}

export type ReviewQueueErrorCode =
  | "invalid"
  | "name_different"
  | "statement_date_required"
  | "statement_too_old"
  | "proof_kind_not_accepted"
  | "client_not_verified"
  | "destination_not_checked"
  | "account_in_use"
  | "checked_by_you"
  | "payout_not_held"
  | "key_reused"
  | "conflict"
  | "forbidden"
  | "not_found"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class ReviewQueueError extends Error {
  readonly code: ReviewQueueErrorCode;

  constructor(code: ReviewQueueErrorCode) {
    super(code);
    this.name = "ReviewQueueError";
    this.code = code;
  }
}

const MESSAGES: Record<ReviewQueueErrorCode, string> = {
  invalid: "Finvest refused what you typed. Check it, keep the reason under 500 characters and send again.",
  name_different:
    "The name on the proof is not the name on the client's ID. A different name can never be approved, so refuse the proof instead.",
  statement_date_required: "Give the date printed on the bank statement.",
  statement_too_old: "The statement is more than three calendar months old. Refuse the proof as too old.",
  proof_kind_not_accepted: "This kind of proof is not accepted for this account. Refuse the proof.",
  client_not_verified: "This client has no approved identity check, so the account cannot be checked yet.",
  destination_not_checked: "This account has no check to approve. It has been reloaded.",
  account_in_use:
    "This account is already approved for another client. Do not approve it: look at both clients, and refuse the proof if it is not this client's.",
  checked_by_you: "You made this check, so a different person must approve it.",
  payout_not_held: "Someone has already decided this payout. The list has been reloaded.",
  key_reused: "This decision was already sent with other details. Reload the page and decide again.",
  conflict: "Someone else has already acted on this account. It has been reloaded.",
  forbidden: "You are not allowed to decide payout accounts or held payouts.",
  not_found: "This no longer exists.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_connected: "The review queue is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function reviewQueueErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ReviewQueueError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new ReviewQueueError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedReviewQueueApi: ReviewQueueApi = {
  listAccounts: notConnected,
  openAccount: notConnected,
  check: notConnected,
  approve: notConnected,
  reject: notConnected,
  remove: notConnected,
  listHeld: notConnected,
  decideHeld: notConnected,
};
