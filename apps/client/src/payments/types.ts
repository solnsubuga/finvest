/**
 * The contract between the pay screens and whatever takes a payment.
 *
 * Today the only implementation is the in-memory stub in
 * `stubPaymentsClient.ts`. When the backend publishes its client routes, an
 * implementation backed by the generated `packages/api-client` types replaces
 * it, and the screens do not change.
 *
 * There is no wallet. A payment always pays for one thing, an investment or a
 * goal, and that thing is the `Payable`.
 */

/** An amount in the smallest unit of its currency. Whole shillings for UGX. Always an integer. */
export type Money = { currency: "UGX" | "USD"; minor: number };

export type Network = "mtn" | "airtel";

/** What the client is paying for. The amount is fixed by the investment, not typed on the pay screen. */
export type Payable = {
  id: string;
  kind: "investment" | "goal";
  /** "91 Day Treasury Bill" */
  title: string;
  amount: Money;
};

/**
 * Where a payment stands, as the client needs to hear it.
 *
 * - `sending`: we are asking the network to send the prompt.
 * - `awaiting_approval`: the prompt is on the phone, waiting for the PIN.
 * - `checking`: we could not get a clear answer from the network. We are
 *   asking it what happened, and the client must not pay again meanwhile.
 * - `confirmed`: the money arrived. Final.
 * - `failed`: the network says the payment did not happen. Final.
 */
export type DepositStatus = "sending" | "awaiting_approval" | "checking" | "confirmed" | "failed";

/** Why a payment failed, in the provider's terms. Mirrors the backend's rejection codes. */
export type FailureReason =
  | "declined"
  | "insufficient_funds"
  | "approval_timed_out"
  | "account_not_found"
  | "limit_exceeded"
  | "other";

export type Deposit = {
  id: string;
  payable: Payable;
  amount: Money;
  network: Network;
  /** The paying number in E.164 form: the number on the client's own Finvest account. */
  payer: string;
  status: DepositStatus;
  failure: FailureReason | null;
  /** ISO 8601, UTC. */
  requestedAt: string;
  /** When the payment was confirmed, or null. ISO 8601, UTC. */
  confirmedAt: string | null;
  /** The network's own reference, once it has given one. */
  networkReference: string | null;
  /** Our reference, which support can look up. */
  reference: string;
};

export type MobileMoneyRequest = {
  payableId: string;
  network: Network;
  /**
   * E.164. Always the number on the client's own account. A payment from a
   * number registered to someone else is refused, so the app never offers one.
   */
  payer: string;
  /**
   * Made once when the pay screen opens and sent with every try from that
   * screen, so a double tap or a retry after a lost connection asks for the
   * money once.
   */
  idempotencyKey: string;
};

/** Where to send a bank transfer. Comes from the server, so the app holds no bank details. */
export type BankInstructions = {
  payable: Payable;
  bankName: string;
  accountName: string;
  accountNumber: string;
  branch: string | null;
  /** The client's Finvest account number. Put on the transfer, it lets staff match the money. */
  reference: string;
};

export type ReceiptMimeType = "image/jpeg" | "image/png" | "application/pdf";

/** A receipt chosen on the device: a bank slip photo, a screenshot or the bank's PDF. */
export type ReceiptFile = {
  uri: string;
  name: string;
  mimeType: ReceiptMimeType;
  sizeBytes: number;
};

/** What the client says they sent. Staff check it against the receipt and the bank statement. */
export type BankReceiptSubmission = {
  payableId: string;
  file: ReceiptFile;
  amountSent: Money;
  /** The day the transfer was made, YYYY-MM-DD, Kampala time. */
  sentOn: string;
  bankName: string;
  transactionNumber: string;
  /** One per visit to the upload screen, like a mobile money payment. */
  idempotencyKey: string;
};

/**
 * - `checking`: we have the receipt and staff are matching it to the bank statement.
 * - `confirmed`: the money was found on the statement. Final.
 * - `not_matched`: staff could not match it. The client can upload another receipt.
 */
export type BankTransferStatus = "checking" | "confirmed" | "not_matched";

/** Why a receipt could not be matched, in words staff choose from. */
export type MismatchReason =
  | "amount_differs"
  | "not_on_statement"
  | "unreadable"
  | "name_differs"
  | "other";

export type BankTransfer = {
  id: string;
  payable: Payable;
  /** What the client said they sent. */
  amountSent: Money;
  status: BankTransferStatus;
  reason: MismatchReason | null;
  /** ISO 8601, UTC. */
  submittedAt: string;
  confirmedAt: string | null;
  /** The reference the client was asked to put on the transfer. */
  reference: string;
};

export type PaymentErrorCode =
  /** The thing to pay for does not exist, is not this client's, or is already paid. */
  | "not_payable"
  /** A payment for this is already under way. */
  | "already_paying"
  /** The network does not allow this amount in one payment. */
  | "amount_not_allowed"
  /** The receipt is not a JPG, PNG or PDF, or is too large. */
  | "receipt_not_accepted"
  /** The same key was sent with different details. The screens prevent it, so it means a bug. */
  | "key_reused"
  | "not_found"
  | "unavailable";

export class PaymentError extends Error {
  readonly code: PaymentErrorCode;

  constructor(code: PaymentErrorCode) {
    super(code);
    this.name = "PaymentError";
    this.code = code;
  }
}

export interface PaymentsClient {
  getPayable(payableId: string): Promise<Payable>;
  /**
   * Asks the network to send an approval prompt to the phone. It answers as
   * soon as the request is saved, before the network has been asked, so the
   * first status is `sending`.
   */
  payWithMobileMoney(request: MobileMoneyRequest): Promise<Deposit>;
  getDeposit(depositId: string): Promise<Deposit>;

  getBankInstructions(payableId: string): Promise<BankInstructions>;
  /** Uploads the receipt and what the client says they sent. Staff do the matching. */
  submitBankReceipt(submission: BankReceiptSubmission): Promise<BankTransfer>;
  getBankTransfer(transferId: string): Promise<BankTransfer>;
}

export function isFinal(status: DepositStatus): boolean {
  return status === "confirmed" || status === "failed";
}

const messages: Record<PaymentErrorCode, string> = {
  not_payable: "This can no longer be paid for. Go back and check your investment.",
  already_paying:
    "A payment for this is already under way. Wait for it to finish before you try again.",
  amount_not_allowed:
    "This amount is more than mobile money allows in one payment. Pay by bank transfer instead.",
  key_reused: "Something changed since your last try. Go back and start the payment again.",
  receipt_not_accepted: "We could not accept that file. Use a JPG, PNG or PDF of up to 10 MB.",
  not_found: "We could not find this payment.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

/** A sentence a client can act on. Never shows codes or technical detail. */
export function describePaymentError(error: unknown): string {
  if (error instanceof PaymentError) {
    return messages[error.code];
  }
  return messages.unavailable;
}
