/**
 * The console's view of custodian statement checks, over the staff routes in
 * backend/app/modules/reconciliation/custodian_api.py (see "Custodian statement checks" in
 * docs/reconciliation.md).
 *
 * Staff upload the custodian's statement in any layout. AI copies each holding off it; Finvest's
 * code turns the copied amounts into numbers and compares them with the dealing desk's bills by
 * maturity date. One person checks every row and explains every difference, and a different
 * person signs it off. The backend checks every rule. The screen decides nothing.
 */

export type Currency = "UGX" | "USD";

export type CheckStatus = "reading" | "unreadable" | "to_review" | "waiting_sign_off" | "signed_off";
export type CheckAction = "correct" | "note" | "submit" | "sign_off" | "send_back";
export type ItemKind =
  | "match"
  | "amount_differs"
  | "missing_at_custodian"
  | "not_in_finvest"
  | "row_unreadable"
  | "statement_date_differs"
  | "statement_date_unread";

export interface CheckSummary {
  id: string;
  /** `YYYY-MM-DD`, the day staff said the statement states holdings at. */
  statementDate: string;
  status: CheckStatus;
  fileContentType: string;
  fileBytes: number;
  uploadedBy: string;
  uploadedAt: string;
  /** Which model read it, such as `claude:<model>`. */
  reader: string | null;
  readStatementDate: string | null;
  custodianName: string | null;
  accountReference: string | null;
  amountScale: "one" | "thousand" | "million" | null;
  failureCode: string | null;
  submittedBy: string | null;
  submitNote: string | null;
  signedOffBy: string | null;
  signedOffAt: string | null;
  signOffNote: string | null;
  sentBackBy: string | null;
  sentBackNote: string | null;
  allowed: CheckAction[];
}

export interface StatementRow {
  id: string;
  lineNo: number;
  /** Exactly as the reader copied it. */
  instrument: string;
  code: string | null;
  faceValueText: string;
  currencyText: string | null;
  maturesOnText: string | null;
  location: string;
  sourceText: string;
  /** What Finvest's code made of it, or why it could not. */
  currency: Currency | null;
  faceMinor: number | null;
  maturesOn: string | null;
  problem: string | null;
  correctedFaceMinor: number | null;
  correctedMaturesOn: string | null;
  leftOut: boolean;
  correctedBy: string | null;
  correctionReason: string | null;
}

export interface CheckItem {
  key: string;
  kind: ItemKind;
  currency: Currency | null;
  maturesOn: string | null;
  finvestFaceMinor: number;
  custodianFaceMinor: number;
  /** Custodian minus Finvest. */
  differenceMinor: number;
  productCodes: string[];
  rowIds: string[];
  problem: string | null;
  needsNote: boolean;
  note: string | null;
}

export interface CheckDetail {
  check: CheckSummary;
  rows: StatementRow[];
  items: CheckItem[];
  /** Differences with no note yet. */
  unexplained: number;
}

export interface CheckList {
  /** False when no reader is set up, so nothing can be uploaded. */
  readingOn: boolean;
  checks: CheckSummary[];
}

export interface Correction {
  leftOut: boolean;
  faceMinor: number | null;
  maturesOn: string | null;
  reason: string;
}

/** Upload, send, sign-off and send back take an idempotency key. */
export interface CustodianChecksApi {
  list(): Promise<CheckList>;
  get(checkId: string): Promise<CheckDetail>;
  upload(key: string, statementDate: string, file: File): Promise<CheckSummary>;
  /** A link to the original file that works for a few minutes. */
  fileLink(checkId: string): Promise<string>;
  correctRow(checkId: string, rowId: string, correction: Correction): Promise<CheckDetail>;
  note(checkId: string, itemKey: string, note: string): Promise<CheckDetail>;
  submit(key: string, checkId: string, note: string | null): Promise<CheckDetail>;
  signOff(key: string, checkId: string, note: string | null): Promise<CheckDetail>;
  sendBack(key: string, checkId: string, reason: string): Promise<CheckDetail>;
}

export type CustodianErrorCode =
  | "not_connected"
  | "unavailable"
  | "forbidden"
  | "refused"
  | "not_found"
  | "invalid"
  | "not_confirmed"
  | "custodian_reading_off"
  | "statement_file_size"
  | "statement_type_not_supported"
  | "statement_date_in_the_future"
  | "check_not_in_review"
  | "check_not_waiting"
  | "check_needs_second_person"
  | "differences_unexplained"
  | "note_invalid"
  | "correction_empty";

export class CustodianError extends Error {
  constructor(readonly code: CustodianErrorCode) {
    super(code);
    this.name = "CustodianError";
  }
}

const MESSAGES: Record<CustodianErrorCode, string> = {
  not_connected: "The console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Try again in a moment.",
  forbidden: "Your role cannot do this.",
  refused: "Finvest refused this as things now stand. The check has been refreshed.",
  not_found: "This check no longer exists. The list has been refreshed.",
  invalid: "Finvest did not accept what was entered. Check each field.",
  not_confirmed: "Nothing was done, because you did not confirm it is you.",
  custodian_reading_off: "Reading custodian statements is not set up on this Finvest yet.",
  statement_file_size: "Choose a file of at most 10 MB.",
  statement_type_not_supported: "Choose a PDF, an Excel workbook (.xlsx), a CSV file, or a photo (PNG or JPEG).",
  statement_date_in_the_future: "The statement date cannot be after today.",
  check_not_in_review: "This check is no longer in review. It has been refreshed.",
  check_not_waiting: "This check is no longer waiting for sign-off. It has been refreshed.",
  check_needs_second_person: "You sent this check for sign-off, so a different person must sign it off or send it back.",
  differences_unexplained: "Every difference needs a note before the check can be sent. The comparison was checked again and has been refreshed.",
  note_invalid: "Write a note of up to 1,000 characters.",
  correction_empty: "Enter the right amount or maturity date, or leave the row out.",
};

export function custodianErrorMessage(error: unknown): string {
  return error instanceof CustodianError ? MESSAGES[error.code] : MESSAGES.unavailable;
}

/** Why the reader could not read a statement, in words. */
export function failureMessage(code: string | null): string {
  switch (code) {
    case "reader_off":
      return "Reading is not set up on this Finvest, so nothing was read.";
    case "reader_declined":
      return "The AI reader declined to read this file.";
    case "statement_too_long":
    case "statement_too_many_rows":
      return "The statement is longer than the reader takes at once (500 holdings).";
    case "statement_not_a_spreadsheet":
      return "The file looks like a spreadsheet but could not be opened.";
    case "statement_not_text":
      return "The CSV file is not plain text.";
    case "reader_unavailable":
      return "The AI reader did not answer after several tries.";
    case "stub_reads_csv_only":
    case "stub_layout_unknown":
      return "The sample reader only reads its own CSV layout.";
    default:
      return "The statement could not be read.";
  }
}

const notConnected = () => Promise.reject(new CustodianError("not_connected"));

export const notConnectedCustodianChecksApi: CustodianChecksApi = {
  list: notConnected,
  get: notConnected,
  upload: notConnected,
  fileLink: notConnected,
  correctRow: notConnected,
  note: notConnected,
  submit: notConnected,
  signOff: notConnected,
  sendBack: notConnected,
};
