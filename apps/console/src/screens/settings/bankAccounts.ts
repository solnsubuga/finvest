/**
 * The console's view of the bank account clients pay into by transfer, one per currency.
 *
 * The shapes follow the bank account routes in docs/payments.md ("The account clients pay
 * into"). `bankAccountsHttp.ts` calls them; `bankAccountsStub.ts` is the sample for
 * `pnpm dev` and previews.
 */

export type Currency = "UGX" | "USD";
export type BankAccountDecision = "approved" | "rejected" | "withdrawn";

export interface BankAccountDetails {
  bankName: string;
  accountName: string;
  accountNumber: string;
  branch: string | null;
}

export interface BankAccountProposal {
  id: string;
  currency: Currency;
  account: BankAccountDetails;
  /** Why it is changing. Staff only. */
  reason: string;
  proposedBy: string;
  proposedAt: string;
  /** The signed-in member of staff proposed it, so they can withdraw it but not decide it. */
  yours: boolean;
}

export interface CurrencyAccount {
  currency: Currency;
  /** The account clients are shown now. Null until a first one is approved. */
  inUse: BankAccountDetails | null;
  changedAt: string | null;
  /** A change waiting for a second person. At most one per currency. */
  proposal: BankAccountProposal | null;
}

export interface NewBankAccount {
  bankName: string;
  accountName: string;
  accountNumber: string;
  branch: string;
  reason: string;
}

export interface BankAccountsApi {
  /** Every currency, with its account in use and any change waiting. */
  list(): Promise<CurrencyAccount[]>;
  propose(currency: Currency, input: NewBankAccount): Promise<void>;
  /** `approved` or `rejected` by someone other than the proposer, `withdrawn` by the proposer. */
  decide(proposalId: string, decision: BankAccountDecision, reason: string): Promise<void>;
}

export type BankAccountField = keyof NewBankAccount;
export type BankAccountProblemCode =
  | "required"
  | "too_long"
  | "invalid"
  /** Refused for a reason this console does not know yet. */
  | "not_accepted";

export interface BankAccountProblem {
  field: BankAccountField;
  code: BankAccountProblemCode;
}

export type BankAccountsErrorCode =
  | "invalid"
  | "conflict"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class BankAccountsError extends Error {
  constructor(
    readonly code: BankAccountsErrorCode,
    /** For `invalid`: what the backend refused, field by field. */
    readonly problems: readonly BankAccountProblem[] = [],
  ) {
    super(code);
    this.name = "BankAccountsError";
  }
}

const MESSAGES: Record<BankAccountsErrorCode, string> = {
  invalid: "Finvest refused these details. Fix what is marked and send them again.",
  conflict: "Someone else proposed a change for this currency first. Here it is as it now stands.",
  same_person: "A change needs a second person. Someone other than you must decide it.",
  forbidden: "Your role can see the account but not change it.",
  not_found: "This change has already been decided. Here is the account as it now stands.",
  not_connected: "The bank account is not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bankAccountsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BankAccountsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new BankAccountsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedBankAccountsApi: BankAccountsApi = {
  list: notConnected,
  propose: notConnected,
  decide: notConnected,
};

/** The same limits as `checked` in the backend's `payments/domain/bank_accounts.py`. */
export const BANK_ACCOUNT_LIMITS: Record<BankAccountField, number> = {
  bankName: 100,
  accountName: 100,
  accountNumber: 20,
  branch: 100,
  reason: 500,
};

/** Spaces and dashes are a way of writing a number, not part of it. */
export function accountDigits(value: string): string {
  return value.replace(/[\s-]/g, "");
}

const tidy = (value: string) => value.split(/\s+/).filter(Boolean).join(" ");

/** What the backend would refuse, checked as the person types. The backend decides. */
export function checkBankAccount(input: NewBankAccount): BankAccountProblem[] {
  const problems: BankAccountProblem[] = [];
  for (const field of ["bankName", "accountName"] as const) {
    const value = tidy(input[field]);
    if (value.length === 0) problems.push({ field, code: "required" });
    else if (value.length > BANK_ACCOUNT_LIMITS[field]) problems.push({ field, code: "too_long" });
  }
  const digits = accountDigits(input.accountNumber);
  if (digits.length === 0) problems.push({ field: "accountNumber", code: "required" });
  else if (!/^[0-9]{6,20}$/.test(digits)) problems.push({ field: "accountNumber", code: "invalid" });
  if (tidy(input.branch).length > BANK_ACCOUNT_LIMITS.branch) problems.push({ field: "branch", code: "too_long" });
  const reason = input.reason.trim();
  if (reason.length === 0) problems.push({ field: "reason", code: "required" });
  else if (reason.length > BANK_ACCOUNT_LIMITS.reason) problems.push({ field: "reason", code: "too_long" });
  return problems;
}

/** The details as Finvest will keep them: spaces tidied, the number as digits, no empty branch. */
export function tidiedAccount(input: NewBankAccount): BankAccountDetails {
  const branch = tidy(input.branch);
  return {
    bankName: tidy(input.bankName),
    accountName: tidy(input.accountName),
    accountNumber: accountDigits(input.accountNumber),
    branch: branch.length > 0 ? branch : null,
  };
}

export function bankAccountProblemText(code: BankAccountProblemCode, field: BankAccountField): string {
  switch (code) {
    case "required":
      return field === "reason" ? "Say why, for the second person and the audit trail." : "Write something here.";
    case "too_long":
      return `Keep it to ${BANK_ACCOUNT_LIMITS[field]} characters.`;
    case "invalid":
      return "Use 6 to 20 digits. Spaces and dashes are fine.";
    case "not_accepted":
      return "Finvest did not accept this. Change it and try again.";
  }
}

export const CURRENCY_NAMES: Record<Currency, string> = {
  UGX: "Uganda shillings",
  USD: "US dollars",
};
