/**
 * The console's view of bank transfers waiting to be checked.
 *
 * A stand-in for the generated client in packages/api-client, like AuthApi.
 * The shapes follow the staff routes in backend/app/modules/payments/routes.py
 * (see docs/payments.md). When the console calls them, an implementation of
 * this interface replaces the stub in index.tsx and the screen does not change.
 *
 * Two people confirm a transfer: one matches it to a line of an imported bank
 * statement, a different one approves the match. Refusing a transfer needs one
 * person and moves no money. The backend enforces all of it. The screen only
 * shows what the backend allows.
 */

export type Currency = "UGX" | "USD";

/** An integer in the smallest unit: whole shillings for UGX, cents for USD. */
export interface Amount {
  minor: number;
  currency: Currency;
}

/**
 * Where a transfer is. `confirming` is approved and waiting the moment it
 * takes the service to post it; `refused` failed with a reason.
 */
export type Stage = "to_match" | "to_approve" | "confirming" | "confirmed" | "refused";

export type RefuseReason = "amount_differs" | "not_on_statement" | "unreadable" | "name_differs" | "other";

/** What the signed-in person may do to this transfer as it stands. The backend decides. */
export type TransferAction = "match" | "refuse" | "approve";

export interface QueueItem {
  id: string;
  amount: Amount;
  /** The calendar day the client says they sent it. */
  sentOn: string;
  bankName: string;
  submittedAt: string;
  stage: Stage;
}

export interface StatementLine {
  id: string;
  /** The bank account the statement is for. */
  accountReference: string;
  valueDate: string;
  amount: Amount;
  reference: string;
  /** The bank's own description. It can name the sender, so it is shown and never logged. */
  narrative: string;
}

export interface Review {
  outcome: "matched" | "not_matched";
  reason: RefuseReason | null;
  /** True when the signed-in person made this review. */
  reviewedByYou: boolean;
  reviewedAt: string;
  approvedByYou: boolean | null;
  approvedAt: string | null;
}

export interface Receipt {
  contentType: string;
  byteSize: number;
  /** A link that opens the file for a few minutes. Null when storage could not make one. */
  url: string | null;
}

export interface TransferDetail extends QueueItem {
  investmentId: string;
  transactionNumber: string;
  receipt: Receipt;
  review: Review | null;
  /** The line the first person chose, for the second person to check beside the receipt. */
  matchedLine: StatementLine | null;
  /** Lines that could be this transfer: money in, the same amount, within 7 days. Empty once reviewed. */
  candidates: readonly StatementLine[];
  allowed: readonly TransferAction[];
}

/**
 * Every command takes an idempotency key made once per attempt by the screen,
 * so a repeat from a weak connection is replayed and never applied twice.
 */
export interface BankTransfersApi {
  listQueue(): Promise<QueueItem[]>;
  getTransfer(id: string): Promise<TransferDetail>;
  match(key: string, id: string, statementLineId: string): Promise<TransferDetail>;
  refuse(key: string, id: string, reason: RefuseReason): Promise<TransferDetail>;
  approve(key: string, id: string): Promise<TransferDetail>;
}

export type BankTransferErrorCode =
  | "not_found"
  | "conflict"
  | "same_person"
  | "line_already_used"
  | "line_not_found"
  | "forbidden"
  | "not_connected"
  | "unavailable";

export class BankTransferError extends Error {
  readonly code: BankTransferErrorCode;

  constructor(code: BankTransferErrorCode) {
    super(code);
    this.name = "BankTransferError";
    this.code = code;
  }
}

const MESSAGES: Record<BankTransferErrorCode, string> = {
  not_found: "This transfer no longer exists.",
  conflict: "Someone else has already acted on this transfer. It has been reloaded.",
  same_person: "A second person has to approve this. You matched it yourself.",
  line_already_used: "That statement line already confirms another payment. Choose another line or refuse the transfer.",
  line_not_found: "That statement line no longer fits this transfer. It has been reloaded.",
  forbidden: "You are not allowed to decide bank transfers.",
  not_connected: "Bank transfers are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bankTransferErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BankTransferError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new BankTransferError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedBankTransfersApi: BankTransfersApi = {
  listQueue: notConnected,
  getTransfer: notConnected,
  match: notConnected,
  refuse: notConnected,
  approve: notConnected,
};
