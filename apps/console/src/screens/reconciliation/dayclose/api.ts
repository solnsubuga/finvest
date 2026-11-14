/**
 * The console's view of closing a day: each pot's statement against the ledger, the differences
 * still open, and the two-person close.
 *
 * The shapes follow the staff routes in backend/app/modules/reconciliation/api.py (see
 * docs/reconciliation.md). One person imports the statements, matches what the records could not
 * pair and prepares the close. A different person, who imported none of the day's statements,
 * approves it, and the ledger is closed through that day. Nothing here moves money. The backend
 * enforces every rule, and the screen only words what it answers.
 */

import type { Amount } from "../api.ts";

export type Source = "iotec" | "bank";

export type Blocker =
  | "previous_day_not_closed"
  | "ledger_balance_drift"
  | "pot_listed_twice"
  | "pot_missing"
  | "statement_missing"
  | "difference_unexplained"
  /** One this screen does not know yet. */
  | "other";

export type ExceptionKind = "missing_in_ledger" | "missing_in_statement" | "amount_differs" | "direction_differs" | "duplicate_reference";

export interface PotLine {
  /** The line's fingerprint, which a match names. */
  id: string;
  valueDate: string;
  direction: "in" | "out";
  amount: Amount;
  reference: string;
  /** The statement's own description. It can carry names, so it is shown and never logged. */
  counterpartyHint: string;
  /** Ids of open ledger movements on the same pot that could be this line. */
  suggestedMovements: string[];
}

export interface Movement {
  /** `entry_id:line_no`. */
  id: string;
  entryId: string;
  lineNo: number;
  effectiveDate: string;
  direction: "in" | "out";
  amount: Amount;
  reference: string;
  entryType: string;
}

export interface PotException {
  kind: ExceptionKind;
  difference: Amount;
  lines: PotLine[];
  movements: Movement[];
}

export interface Position {
  source: Source;
  accountReference: string;
  name: string;
  /** Null until the day's statement is imported. */
  statementClosing: Amount | null;
  ledgerClosing: Amount;
  openTotal: Amount;
  openCount: number;
  /** The difference the open exceptions do not explain. Null without a statement. */
  unexplained: Amount | null;
  exceptions: PotException[];
}

export interface Close {
  id: string;
  status: "prepared" | "closed";
  /** The person signed in prepared it. */
  preparedByYou: boolean;
  decidedByYou: boolean | null;
}

export interface Day {
  /** YYYY-MM-DD, a business day in Kampala. */
  date: string;
  lastClosed: string | null;
  close: Close | null;
  /** Why the day cannot be prepared yet. Empty once a close exists. */
  blockers: Blocker[];
  positions: Position[];
}

export interface StatementUpload {
  source: Source;
  accountReference: string;
  /** YYYY-MM-DD. */
  statementDate: string;
  /** As the statement prints it, such as "1,500,000". */
  openingBalance: string;
  closingBalance: string;
  file: File;
}

export interface Imported {
  id: string;
  businessDate: string;
  lineCount: number;
}

export interface HandMatch {
  statementLineId: string;
  movement: Movement;
  reason: string;
}

/**
 * None of these routes take an idempotency key: nothing here moves money, and a repeat is
 * refused (`statement_already_imported`, `line_already_matched`, `close_not_open`) rather than
 * done twice. The screen reloads the day after any refusal.
 */
export interface DayCloseApi {
  getDay(date: string): Promise<Day>;
  refresh(date: string): Promise<void>;
  importStatement(upload: StatementUpload): Promise<Imported>;
  match(match: HandMatch): Promise<void>;
  prepare(date: string): Promise<void>;
  approve(date: string): Promise<void>;
  sendBack(date: string, reason: string): Promise<void>;
}

/** The codes the backend refuses with, under `conflict` or `validation_failed`, and our own. */
export type DayCloseErrorCode =
  | Blocker
  | "day_not_ended"
  | "close_not_open"
  | "close_not_prepared"
  | "close_needs_second_person"
  | "close_needs_someone_who_did_not_import"
  | "close_changed_since_prepared"
  | "line_already_matched"
  | "movement_already_matched"
  | "match_not_found"
  | "match_not_same_money"
  | "statement_already_imported"
  | "statement_day_out_of_order"
  | "statement_opening_balance_differs"
  | "statement_day_not_over"
  | "statement_file_size"
  | "statement_not_text"
  | "statement_amount_invalid"
  | "statement_column_missing"
  | "statement_row_short"
  | "statement_date_invalid"
  | "statement_direction_invalid"
  | "statement_amount_not_positive"
  | "statement_text_too_long"
  | "statement_too_long"
  | "statement_mixed_currency"
  | "statement_line_after_statement_date"
  | "statement_does_not_add_up"
  | "statement_duplicate_fingerprint"
  | "invalid"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "confirm_it_is_you"
  | "not_connected"
  | "unavailable";

/** Which part of the import form a refusal is about. */
export type UploadField = "openingBalance" | "closingBalance" | "file" | null;

export class DayCloseError extends Error {
  readonly code: DayCloseErrorCode;
  readonly field: UploadField;
  /** For a problem in the file: the row, counted from 1 below the header. */
  readonly row: number | null;

  constructor(code: DayCloseErrorCode, field: UploadField = null, row: number | null = null) {
    super(code);
    this.name = "DayCloseError";
    this.code = code;
    this.field = field;
    this.row = row;
  }
}

export const BLOCKERS: Record<Blocker, string> = {
  previous_day_not_closed: "The day before has not been closed. Days are closed in order.",
  ledger_balance_drift: "The ledger's balances do not agree with its own entries. Tell engineering before going on.",
  pot_listed_twice: "A pot is set up twice. Tell engineering before going on.",
  pot_missing: "A pot has no ledger account. Tell engineering before going on.",
  statement_missing: "A pot's statement for this day has not been imported.",
  difference_unexplained: "A pot's statement and the ledger differ by more than the open differences explain.",
  other: "Something this screen does not recognise stops the close. Tell engineering.",
};

export const KIND_LABELS: Record<ExceptionKind, string> = {
  missing_in_ledger: "On the statement, not in the ledger",
  missing_in_statement: "In the ledger, not on the statement",
  amount_differs: "Same reference, different amount",
  direction_differs: "Same reference, money moving the other way",
  duplicate_reference: "One reference used more than once",
};

const MESSAGES: Record<Exclude<DayCloseErrorCode, Blocker>, string> = {
  day_not_ended: "This day has not ended in Kampala yet. It can be closed from the next morning.",
  close_not_open: "This day has already been prepared or closed. It has been reloaded.",
  close_not_prepared: "This day is no longer waiting for approval. It has been reloaded.",
  close_needs_second_person: "You prepared this close, so a different person has to approve it.",
  close_needs_someone_who_did_not_import:
    "You imported one of this day's statements, so someone who imported none of them has to approve it.",
  close_changed_since_prepared:
    "Something changed after the close was prepared, so it was sent back. Check the day again and prepare it once more.",
  line_already_matched: "That statement line has already been matched. The day has been reloaded.",
  movement_already_matched: "That ledger movement has already been matched to another line. The day has been reloaded.",
  match_not_found: "That line or movement is no longer open. The day has been reloaded.",
  match_not_same_money: "The line and the movement are not the same money: the pot, amount or direction differs.",
  statement_already_imported: "This pot's statement for that day has already been imported.",
  statement_day_out_of_order: "Import this pot's statements in order. An earlier day is missing.",
  statement_opening_balance_differs: "The opening balance is not the closing balance of this pot's last statement.",
  statement_day_not_over: "That day has not ended yet. Import its statement from the next morning.",
  statement_file_size: "The file is empty or larger than 2 MB.",
  statement_not_text: "The file is not a text CSV. Export the statement as CSV and try again.",
  statement_amount_invalid: "Enter the amount as the statement prints it, such as 1,500,000.",
  statement_column_missing: "The file needs the columns Date, Details, Reference, In and Out.",
  statement_row_short: "has fewer columns than the header.",
  statement_date_invalid: "has a date that is not written as YYYY-MM-DD.",
  statement_direction_invalid: "needs an amount in exactly one of In and Out.",
  statement_amount_not_positive: "has an amount of zero or less.",
  statement_text_too_long: "has a reference or description longer than the statement allows.",
  statement_too_long: "The file has more lines than one day's statement allows.",
  statement_mixed_currency: "The statement mixes currencies.",
  statement_line_after_statement_date: "is dated after the statement's day.",
  statement_does_not_add_up: "The opening balance plus the lines does not come to the closing balance. Check both balances.",
  statement_duplicate_fingerprint: "Two rows are the same line. Check the export for a repeated row.",
  invalid: "Check the details and try again.",
  conflict: "Someone else has acted on this day. It has been reloaded.",
  not_found: "This day could not be found.",
  forbidden: "You cannot do this. Closing a day needs the permission to reconcile.",
  confirm_it_is_you: "Approving needs you to confirm it is you with your passkey or authenticator app. Try again when you are ready.",
  not_connected: "Closing the day is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function dayCloseErrorMessage(error: unknown): string {
  if (!(error instanceof DayCloseError)) return MESSAGES.unavailable;
  if (error.code in BLOCKERS) return BLOCKERS[error.code as Blocker];
  const message = MESSAGES[error.code as Exclude<DayCloseErrorCode, Blocker>];
  // A problem on one row is worded about that row.
  if (error.row !== null && /^[a-z]/.test(message)) return `Row ${error.row} ${message}`;
  if (error.row !== null) return `Row ${error.row}: ${message}`;
  if (/^[a-z]/.test(message)) return `A row ${message}`;
  return message;
}

/** The errors after which what is on screen is out of date. */
export function needsReload(error: unknown): boolean {
  return error instanceof DayCloseError && !["unavailable", "confirm_it_is_you", "not_connected", "invalid"].includes(error.code);
}

/** The business day in Kampala, which is three hours ahead of UTC all year. */
export function kampalaToday(now: Date): string {
  return new Date(now.getTime() + 3 * 3_600_000).toISOString().slice(0, 10);
}

/** A YYYY-MM-DD day moved by whole days. */
export function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
}

const notConnected = () => Promise.reject(new DayCloseError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedDayCloseApi: DayCloseApi = {
  getDay: notConnected,
  refresh: notConnected,
  importStatement: notConnected,
  match: notConnected,
  prepare: notConnected,
  approve: notConnected,
  sendBack: notConnected,
};
