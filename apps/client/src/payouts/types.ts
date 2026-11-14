import type { Money, Network, ReceiptFile } from "@/payments/types";

/**
 * The contract between the payout screens and whatever serves them.
 *
 * The shapes follow the client routes in backend/app/modules/payouts/api.py
 * (`/client/payout-destinations`, `/client/payouts`, `/client/withdrawal-limits`).
 * `apiPayoutsClient.ts` answers it from the API; `stubPayoutsClient.ts` is the
 * in-memory demo. docs/payouts.md has the rules: a client is paid only to their own
 * account, in the name on their ID, one day after staff approve it.
 *
 * No route here creates a payout. Money is paid out by the investment it came from: at
 * maturity, or when the client withdraws early.
 */

export type AccountKind = "mobile_money" | "bank";

/**
 * Where a new account is on its way to being paid. `awaiting_code`: the client has not
 * entered the texted code yet. `awaiting_proof`: a bank account waits for its proof, a
 * mobile money number for us to read the name the network holds. `in_review`: staff are
 * checking the proof against the client's ID. `approved`: payable once `payableFrom` passes.
 */
export type AccountStatus = "awaiting_code" | "awaiting_proof" | "in_review" | "approved" | "rejected";

export type RejectionReason =
  | "name_different"
  | "proof_unreadable"
  | "proof_too_old"
  | "proof_wrong_account"
  | "proof_wrong_document"
  | "suspected_fraud";

export type ProofKind = "bank_statement" | "bank_letter" | "bank_card";

export type PayoutAccount = {
  id: string;
  kind: AccountKind;
  /** Mobile money only. */
  network: Network | null;
  /** Bank accounts only, a code from `banks.ts`. */
  bankCode: string | null;
  /** Only the last digits, as the API shows them: "+256 77• ••• 456", "•••• 4821". */
  account: string;
  /** The name the client said the account is in. Staff compare the proof with the ID, never with this. */
  holderName: string;
  status: AccountStatus;
  proofKind: ProofKind | "network_registered_name" | null;
  rejectionReason: RejectionReason | null;
  /** Approval plus the 24 hour safety wait. Null until approved. ISO 8601, UTC. */
  payableFrom: string | null;
  firstPaidAt: string | null;
  createdAt: string;
};

export type NewAccount =
  | { kind: "mobile_money"; phone: string; holderName: string }
  | { kind: "bank"; bankCode: string; accountNumber: string; holderName: string };

/** `processing` covers everything from queued to the provider's answer. */
export type PayoutStatus = "in_review" | "processing" | "paid" | "failed" | "cancelled";

export type Payout = {
  id: string;
  destinationId: string;
  kind: AccountKind;
  amount: Money;
  status: PayoutStatus;
  /** The Kampala business day it was asked for. YYYY-MM-DD. */
  businessDate: string;
  createdAt: string;
};

/** The withdrawal limits in force today. Null is not set. Never the review threshold. */
export type Limits = {
  effectiveFrom: string;
  mobileMoneyPerPayout: Money;
  mobileMoneyPerDay: Money | null;
  bankPerPayout: Money | null;
  bankPerDay: Money | null;
};

export interface PayoutsClient {
  /** Every account not removed, newest first. */
  listAccounts(): Promise<PayoutAccount[]>;
  getAccount(accountId: string): Promise<PayoutAccount>;
  /** Saves it waiting for its code. Text the code with `sendCode`. */
  addAccount(account: NewAccount): Promise<PayoutAccount>;
  /** Texts a code: to the mobile money number itself, or to the client's own number for a bank account. */
  sendCode(accountId: string): Promise<void>;
  verifyCode(accountId: string, code: string): Promise<PayoutAccount>;
  /** Bank accounts only. Sends the proof to staff. */
  uploadProof(accountId: string, proofKind: ProofKind, file: ReceiptFile): Promise<PayoutAccount>;
  /** For good. A payout already on its way is not stopped. */
  removeAccount(accountId: string): Promise<void>;
  /** Newest first, at most 50. */
  listPayouts(): Promise<Payout[]>;
  getPayout(payoutId: string): Promise<Payout>;
  getLimits(): Promise<Limits>;
}

export type PayoutsErrorCode =
  | "already_saved"
  | "too_many_unconfirmed"
  | "phone_invalid"
  | "phone_network_unsupported"
  | "bank_invalid"
  | "account_number_invalid"
  | "holder_name_invalid"
  | "invalid_code"
  | "code_expired"
  | "too_many_codes"
  | "moved_on"
  | "file_not_accepted"
  | "not_found"
  | "invalid"
  | "unavailable";

export class PayoutsError extends Error {
  readonly code: PayoutsErrorCode;

  constructor(code: PayoutsErrorCode) {
    super(code);
    this.name = "PayoutsError";
    this.code = code;
  }
}

const messages: Record<PayoutsErrorCode, string> = {
  already_saved: "You have already saved this account. Open it from your payout accounts.",
  too_many_unconfirmed: "You have five accounts waiting for their code. Finish or remove one of those first.",
  phone_invalid: "Enter a mobile number like 772 123 456.",
  phone_network_unsupported: "We can pay MTN and Airtel numbers only.",
  bank_invalid: "Choose your bank from the list.",
  account_number_invalid: "Enter the account number as the bank shows it: 6 to 20 digits.",
  holder_name_invalid: "Enter the name the account is in.",
  invalid_code: "That code is not right. Check it and try again.",
  code_expired: "That code has run out. Send a new one.",
  too_many_codes: "We have sent several codes already. Wait a few minutes, then send a new one.",
  moved_on: "This account has moved on since you opened it. Go back and open it again.",
  file_not_accepted: "We could not accept that file. Use a clear PDF, JPG or PNG under 10 MB.",
  not_found: "We could not find that account.",
  invalid: "We could not accept that. Check the details and try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function describePayoutsError(error: unknown): string {
  return messages[error instanceof PayoutsError ? error.code : "unavailable"];
}
