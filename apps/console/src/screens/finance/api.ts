/**
 * Withholding tax as the Finance screen sees it: each month's tax against what
 * has been paid to URA, one month client by client for URA's return, and
 * payments to URA that one person records and another approves.
 *
 * The shapes follow backend/app/modules/tax/api.py (`/staff/tax`). Every
 * figure is read from the ledger there. Amounts are integers in the smallest
 * unit; nothing here works one out.
 */

import { ApiError } from "@/api/http.ts";

export type Currency = "UGX" | "USD";
export const CURRENCIES: readonly Currency[] = ["UGX", "USD"];

export interface Amount {
  minor: number;
  currency: Currency;
}

export interface TaxMonth {
  /** YYYY-MM */
  month: string;
  withheld: Amount;
  remitted: Amount;
  waiting: Amount;
  /** Negative when more was paid than the month now owes: a credit against the next payment. */
  outstanding: Amount;
  /** YYYY-MM-DD, the 15th of the next month. */
  dueOn: string;
  overdue: boolean;
}

export type RemittanceStatus = "proposed" | "approved" | "declined";
export type Step = "approve" | "decline";

export interface Remittance {
  id: string;
  month: string;
  amount: Amount;
  /** URA's payment registration number (PRN). */
  uraReference: string;
  bankReference: string;
  paidOn: string;
  note: string;
  status: RemittanceStatus;
  proposedBy: string;
  proposedByName: string | null;
  proposedAt: string;
  decidedBy: string | null;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  /** What the reader may do now. Empty for the person who recorded it. */
  allowed: readonly Step[];
}

export interface TaxScreen {
  currency: Currency;
  heldForUra: Amount;
  /** The last twelve months, newest first. */
  months: readonly TaxMonth[];
  /** Newest first. */
  remittances: readonly Remittance[];
}

export interface Payee {
  clientId: string;
  name: string | null;
  accountNumber: string | null;
  tin: string | null;
  gross: Amount;
  tax: Amount;
}

export interface MonthReport {
  month: string;
  currency: Currency;
  gross: Amount;
  withheld: Amount;
  summary: TaxMonth;
  payees: readonly Payee[];
  tinsShown: boolean;
}

export interface NewRemittance {
  currency: Currency;
  month: string;
  amountMinor: number;
  uraReference: string;
  bankReference: string;
  paidOn: string;
  note: string;
}

export interface TaxExport {
  file: Blob;
  filename: string;
}

export interface TaxApi {
  getScreen(currency: Currency): Promise<TaxScreen>;
  getMonth(currency: Currency, month: string): Promise<MonthReport>;
  exportMonth(currency: Currency, month: string): Promise<TaxExport>;
  record(key: string, payment: NewRemittance): Promise<TaxScreen>;
  approve(key: string, remittanceId: string): Promise<TaxScreen>;
  decline(key: string, remittanceId: string, reason: string): Promise<TaxScreen>;
}

export type TaxErrorCode =
  | "month_not_ended"
  | "more_than_outstanding"
  | "not_enough_held"
  | "already_recorded"
  | "paid_in_the_future"
  | "paid_before_the_month"
  | "paid_on_in_a_closed_day"
  | "reason_required"
  | "invalid"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "not_connected"
  | "unavailable";

export class TaxError extends Error {
  constructor(readonly code: TaxErrorCode) {
    super(code);
    this.name = "TaxError";
  }
}

const MESSAGES: Record<TaxErrorCode, string> = {
  month_not_ended: "A month is paid once it has ended, so its figures can no longer change.",
  more_than_outstanding: "That is more than the month still owes URA, counting payments already recorded.",
  not_enough_held: "The ledger cannot cover that: the trust account or the tax held is short. Check both.",
  already_recorded: "That PRN or bank reference is already recorded against another payment.",
  paid_in_the_future: "The payment date cannot be after today.",
  paid_before_the_month: "The payment date cannot be before the month it pays for.",
  paid_on_in_a_closed_day: "That day is closed in the books. Record the payment on an open day.",
  reason_required: "Say why it is declined. The reason goes in the audit trail.",
  invalid: "Check the details and try again.",
  conflict: "Someone else has already decided this payment. The screen has been reloaded.",
  not_found: "This payment no longer exists.",
  forbidden: "Your role cannot do this, or a second person has to approve it.",
  not_connected: "This console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function taxErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof TaxError ? error.code : "unavailable"];
}

const FIELD_CODES = new Set<string>([
  "month_not_ended",
  "more_than_outstanding",
  "not_enough_held",
  "already_recorded",
  "paid_in_the_future",
  "paid_before_the_month",
  "paid_on_in_a_closed_day",
  "reason_required",
]);

export function toTaxError(error: unknown): TaxError {
  if (error instanceof TaxError) return error;
  if (!(error instanceof ApiError)) return new TaxError("unavailable");
  if (error.code === "validation_failed") {
    const field = error.fieldCode;
    return new TaxError(field && FIELD_CODES.has(field) ? (field as TaxErrorCode) : "invalid");
  }
  if (error.code === "conflict" || error.code === "not_found" || error.code === "forbidden") return new TaxError(error.code);
  return new TaxError("unavailable");
}

export const notConnectedTaxApi: TaxApi = {
  getScreen: () => Promise.reject(new TaxError("not_connected")),
  getMonth: () => Promise.reject(new TaxError("not_connected")),
  exportMonth: () => Promise.reject(new TaxError("not_connected")),
  record: () => Promise.reject(new TaxError("not_connected")),
  approve: () => Promise.reject(new TaxError("not_connected")),
  decline: () => Promise.reject(new TaxError("not_connected")),
};
