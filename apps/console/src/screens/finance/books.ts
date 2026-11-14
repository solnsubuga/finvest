/**
 * The books as the Finance screen sees them: each ledger code over a month,
 * Finvest's own income and costs, and the daily journal for the accounting
 * system.
 *
 * The shapes follow backend/app/modules/finance/api.py (`/staff/finance`).
 * Every figure is read from the ledger there. Amounts are integers in the
 * smallest unit; nothing here works one out.
 */

import { ApiError } from "@/api/http.ts";
import type { Amount, Currency } from "./api.ts";

export interface BookLine {
  code: string;
  name: string;
  kind: "asset" | "liability" | "equity" | "income" | "expense" | string;
  /** In the code's normal direction. */
  opening: Amount;
  debits: Amount;
  credits: Amount;
  closing: Amount;
}

export interface IncomeLine {
  code: string;
  name: string;
  amount: Amount;
}

export interface IncomeAndCosts {
  income: readonly IncomeLine[];
  costs: readonly IncomeLine[];
  incomeTotal: Amount;
  costTotal: Amount;
  net: Amount;
}

export interface Days {
  dateFrom: string;
  dateTo: string;
}

/** A file the journal can be downloaded as. The systems name their own accounts. */
export type JournalFormat = "csv" | "quickbooks" | "xero" | "sage" | "zoho";

export interface FormatOption {
  format: JournalFormat;
  name: string;
  /** False while the system has no approved account code map. */
  ready: boolean;
  /** Ledger codes the system's map does not name yet. */
  unmapped: readonly string[];
  currencies: readonly Currency[];
}

export interface JournalExport extends Days {
  id: string;
  format: JournalFormat;
  lines: number;
  debitTotal: Amount;
  exportedBy: string;
  exportedByName: string | null;
  exportedAt: string;
}

export interface Books {
  currency: Currency;
  /** YYYY-MM */
  month: string;
  /** The days the figures cover: the month, up to today when it is this month. */
  dateFrom: string;
  dateTo: string;
  accounts: readonly BookLine[];
  income: IncomeAndCosts;
  closedThrough: string | null;
  sentThrough: string | null;
  /** Closed days not yet downloaded for the accounting system, or null. */
  nextExport: Days | null;
  /** Newest first. */
  exports: readonly JournalExport[];
  /** The generic CSV first, then each accounting system. */
  formats: readonly FormatOption[];
}

export interface JournalFile {
  file: Blob;
  filename: string;
}

export interface BooksApi {
  getBooks(currency: Currency, month: string): Promise<Books>;
  exportJournal(currency: Currency, days: Days, format: JournalFormat): Promise<JournalFile>;
}

export type BooksErrorCode =
  | "day_not_closed"
  | "too_many_days"
  | "days_backwards"
  | "month_in_future"
  | "codes_not_mapped"
  | "currency_not_supported"
  | "invalid"
  | "forbidden"
  | "not_connected"
  | "unavailable";

export class BooksError extends Error {
  constructor(
    readonly code: BooksErrorCode,
    /** For `codes_not_mapped`: the ledger codes the system's map does not name. */
    readonly codes: readonly string[] = [],
  ) {
    super(code);
    this.name = "BooksError";
  }
}

const MESSAGES: Record<BooksErrorCode, string> = {
  day_not_closed: "Only days closed on the Reconciliation screen can be downloaded, so a journal never changes after it is imported.",
  too_many_days: "Download at most 31 days in one file.",
  days_backwards: "The first day comes before the last.",
  month_in_future: "That month has not started yet.",
  codes_not_mapped: "Some Finvest accounts in these days have no code in the accounting system yet. Finance or an admin adds codes under Settings, Accounting system codes.",
  currency_not_supported: "Accounting systems take the journal in shillings only. Download dollar journals as the Finvest CSV.",
  invalid: "Check the month and try again.",
  forbidden: "Your role cannot see the books.",
  not_connected: "This console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function booksErrorMessage(error: unknown): string {
  if (error instanceof BooksError && error.code === "codes_not_mapped" && error.codes.length > 0) {
    return `${error.codes.length === 1 ? "This Finvest account has" : "These Finvest accounts have"} no code in the accounting system yet: ${error.codes.join(", ")}. Finance or an admin adds codes under Settings, Accounting system codes.`;
  }
  return MESSAGES[error instanceof BooksError ? error.code : "unavailable"];
}

const FIELD_CODES = new Set<string>(["day_not_closed", "too_many_days", "days_backwards", "month_in_future", "currency_not_supported"]);

export function toBooksError(error: unknown): BooksError {
  if (error instanceof BooksError) return error;
  if (!(error instanceof ApiError)) return new BooksError("unavailable");
  const unmapped = error.fields.filter((f) => f.code === "not_mapped" && f.field_path[0] === "codes").map((f) => String(f.field_path[1]));
  if (unmapped.length > 0) return new BooksError("codes_not_mapped", unmapped);
  const field = error.fieldCode;
  if (field && FIELD_CODES.has(field)) return new BooksError(field as BooksErrorCode);
  if (error.code === "validation_failed") return new BooksError("invalid");
  if (error.code === "forbidden") return new BooksError("forbidden");
  return new BooksError("unavailable");
}

export const notConnectedBooksApi: BooksApi = {
  getBooks: () => Promise.reject(new BooksError("not_connected")),
  exportJournal: () => Promise.reject(new BooksError("not_connected")),
};
