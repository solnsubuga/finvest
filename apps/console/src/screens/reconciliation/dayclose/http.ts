/**
 * Closing a day over the real API (`/staff/reconciliation…`, docs/reconciliation.md).
 *
 * The routes refuse with `conflict` or `validation_failed` and put the reason in the first field
 * problem's code, so that code is what the screen words. Approving can be refused with
 * `confirm_it_is_you`: `createHttp` asks the dialog and sends it again, so that refusal only
 * reaches the screen if the person closes the dialog.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "../api.ts";
import {
  BLOCKERS,
  DayCloseError,
  type Blocker,
  type Day,
  type DayCloseApi,
  type DayCloseErrorCode,
  type ExceptionKind,
  type Movement,
  type PotLine,
  type Position,
  type Source,
  type UploadField,
} from "./api.ts";

interface LineJson {
  id: string;
  value_date: string;
  direction: "in" | "out";
  amount: Amount;
  reference: string;
  counterparty_hint: string;
  suggested_movements: string[];
}

interface MovementJson {
  id: string;
  entry_id: string;
  line_no: number;
  effective_date: string;
  direction: "in" | "out";
  amount: Amount;
  reference: string;
  entry_type: string;
}

interface PositionJson {
  source: Source;
  account_reference: string;
  name: string;
  statement_closing: Amount | null;
  ledger_closing: Amount;
  open_exceptions_total: Amount;
  open_exceptions_count: number;
  unexplained: Amount | null;
  exceptions: { kind: ExceptionKind; difference: Amount; statement_lines: LineJson[]; movements: MovementJson[] }[];
}

interface DayJson {
  business_date: string;
  last_closed: string | null;
  close: { id: string; status: "prepared" | "closed"; prepared_by: string; decided_by: string | null } | null;
  blockers: string[];
  positions: PositionJson[];
}

interface StatementJson {
  id: string;
  business_date: string;
  line_count: number;
}

const toLine = (json: LineJson): PotLine => ({
  id: json.id,
  valueDate: json.value_date,
  direction: json.direction,
  amount: json.amount,
  reference: json.reference,
  counterpartyHint: json.counterparty_hint,
  suggestedMovements: json.suggested_movements,
});

const toMovement = (json: MovementJson): Movement => ({
  id: json.id,
  entryId: json.entry_id,
  lineNo: json.line_no,
  effectiveDate: json.effective_date,
  direction: json.direction,
  amount: json.amount,
  reference: json.reference,
  entryType: json.entry_type,
});

const toPosition = (json: PositionJson): Position => ({
  source: json.source,
  accountReference: json.account_reference,
  name: json.name,
  statementClosing: json.statement_closing,
  ledgerClosing: json.ledger_closing,
  openTotal: json.open_exceptions_total,
  openCount: json.open_exceptions_count,
  unexplained: json.unexplained,
  exceptions: json.exceptions.map((each) => ({
    kind: each.kind,
    difference: each.difference,
    lines: each.statement_lines.map(toLine),
    movements: each.movements.map(toMovement),
  })),
});

export function toDay(json: DayJson, me: string): Day {
  return {
    date: json.business_date,
    lastClosed: json.last_closed,
    close: json.close && {
      id: json.close.id,
      status: json.close.status,
      preparedByYou: json.close.prepared_by === me,
      decidedByYou: json.close.decided_by === null ? null : json.close.decided_by === me,
    },
    // A blocker this screen does not know yet still stops the close, so it is kept.
    blockers: json.blockers.map((code) => (code in BLOCKERS ? (code as Blocker) : "other")),
    positions: json.positions.map(toPosition),
  };
}

const KNOWN = new Set<string>([
  ...Object.keys(BLOCKERS),
  "day_not_ended",
  "close_not_open",
  "close_not_prepared",
  "close_needs_second_person",
  "close_needs_someone_who_did_not_import",
  "close_changed_since_prepared",
  "line_already_matched",
  "movement_already_matched",
  "match_not_found",
  "match_not_same_money",
  "statement_already_imported",
  "statement_day_out_of_order",
  "statement_opening_balance_differs",
  "statement_day_not_over",
  "statement_file_size",
  "statement_not_text",
  "statement_amount_invalid",
  "statement_column_missing",
  "statement_row_short",
  "statement_date_invalid",
  "statement_direction_invalid",
  "statement_amount_not_positive",
  "statement_text_too_long",
  "statement_too_long",
  "statement_mixed_currency",
  "statement_line_after_statement_date",
  "statement_does_not_add_up",
  "statement_duplicate_fingerprint",
]);

const FIELDS: Record<string, UploadField> = {
  opening_balance: "openingBalance",
  closing_balance: "closingBalance",
  file: "file",
};

/** The API's refusal as the screen's own error, keeping the form field and the file's row. */
export function toDayCloseError(error: unknown): DayCloseError {
  if (error instanceof DayCloseError) return error;
  if (!(error instanceof ApiError)) return new DayCloseError("unavailable");
  if (error.code === "conflict" || error.code === "validation_failed") {
    const problem = error.fields[0];
    const code = problem?.code ?? "";
    // The path is ["body", field] or ["body", "file", row].
    const path = problem?.field_path ?? [];
    const field = FIELDS[String(path[1] ?? "")] ?? null;
    const row = typeof path[2] === "number" ? path[2] : null;
    if (KNOWN.has(code)) return new DayCloseError(code as DayCloseErrorCode, field, row);
    // A refusal we cannot word still means the day changed under us, or the form is wrong.
    return new DayCloseError(error.code === "conflict" ? "conflict" : "invalid", field);
  }
  switch (error.code) {
    case "not_found":
    case "forbidden":
    case "confirm_it_is_you":
      return new DayCloseError(error.code);
    default:
      return new DayCloseError("unavailable");
  }
}

const BASE = "/staff/reconciliation";

export function createHttpDayCloseApi(http: Http, me: string): DayCloseApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toDayCloseError(error);
    }
  }
  const day = (date: string) => `${BASE}/days/${encodeURIComponent(date)}`;

  return {
    getDay: (date) => call(async () => toDay(await http.request<DayJson>("GET", day(date)), me)),
    refresh: (date) => call(() => http.request<undefined>("POST", `${day(date)}/refresh`)),
    importStatement: (upload) =>
      call(async () => {
        const form = new FormData();
        form.append("source", upload.source);
        form.append("account_reference", upload.accountReference);
        form.append("statement_date", upload.statementDate);
        form.append("opening_balance", upload.openingBalance);
        form.append("closing_balance", upload.closingBalance);
        form.append("format", "standard");
        form.append("file", upload.file, upload.file.name);
        const made = await http.request<StatementJson>("POST", `${BASE}/statements`, form);
        return { id: made.id, businessDate: made.business_date, lineCount: made.line_count };
      }),
    match: ({ statementLineId, movement, reason }) =>
      call(async () => {
        await http.request<undefined>("POST", `${BASE}/matches`, {
          statement_line_id: statementLineId,
          entry_id: movement.entryId,
          line_no: movement.lineNo,
          reason,
        });
      }),
    prepare: (date) => call(async () => void (await http.request<unknown>("POST", `${day(date)}/prepare`))),
    approve: (date) => call(async () => void (await http.request<unknown>("POST", `${day(date)}/approve`))),
    sendBack: (date, reason) => call(() => http.request<undefined>("POST", `${day(date)}/send-back`, { reason })),
  };
}
