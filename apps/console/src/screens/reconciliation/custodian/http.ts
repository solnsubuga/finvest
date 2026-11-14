/** Custodian statement checks over `/staff/reconciliation/custodian-checks`. */

import { ApiError, type Http } from "@/api/http.ts";
import {
  CustodianError,
  type CheckDetail,
  type CheckItem,
  type CheckSummary,
  type CustodianChecksApi,
  type CustodianErrorCode,
  type StatementRow,
} from "./api.ts";

interface CheckJson {
  id: string;
  statement_date: string;
  status: CheckSummary["status"];
  file_content_type: string;
  file_bytes: number;
  uploaded_by: string;
  uploaded_at: string;
  reader: string | null;
  read_statement_date: string | null;
  custodian_name: string | null;
  account_reference: string | null;
  amount_scale: CheckSummary["amountScale"];
  failure_code: string | null;
  submitted_by: string | null;
  submit_note: string | null;
  signed_off_by: string | null;
  signed_off_at: string | null;
  sign_off_note: string | null;
  sent_back_by: string | null;
  sent_back_note: string | null;
  allowed: CheckSummary["allowed"];
}

interface RowJson {
  id: string;
  line_no: number;
  instrument: string;
  code: string | null;
  face_value_text: string;
  currency_text: string | null;
  matures_on_text: string | null;
  location: string;
  source_text: string;
  currency: StatementRow["currency"];
  face_minor: number | null;
  matures_on: string | null;
  problem: string | null;
  corrected_face_minor: number | null;
  corrected_matures_on: string | null;
  left_out: boolean;
  corrected_by: string | null;
  correction_reason: string | null;
}

interface ItemJson {
  key: string;
  kind: CheckItem["kind"];
  currency: CheckItem["currency"];
  matures_on: string | null;
  finvest_face_minor: number;
  custodian_face_minor: number;
  difference_minor: number;
  product_codes: string[];
  row_ids: string[];
  problem: string | null;
  needs_note: boolean;
  note: string | null;
}

interface DetailJson {
  check: CheckJson;
  rows: RowJson[];
  items: ItemJson[];
  unexplained: number;
}

export function toCheck(json: CheckJson): CheckSummary {
  return {
    id: json.id,
    statementDate: json.statement_date,
    status: json.status,
    fileContentType: json.file_content_type,
    fileBytes: json.file_bytes,
    uploadedBy: json.uploaded_by,
    uploadedAt: json.uploaded_at,
    reader: json.reader,
    readStatementDate: json.read_statement_date,
    custodianName: json.custodian_name,
    accountReference: json.account_reference,
    amountScale: json.amount_scale,
    failureCode: json.failure_code,
    submittedBy: json.submitted_by,
    submitNote: json.submit_note,
    signedOffBy: json.signed_off_by,
    signedOffAt: json.signed_off_at,
    signOffNote: json.sign_off_note,
    sentBackBy: json.sent_back_by,
    sentBackNote: json.sent_back_note,
    allowed: json.allowed,
  };
}

export function toDetail(json: DetailJson): CheckDetail {
  return {
    check: toCheck(json.check),
    rows: json.rows.map((row) => ({
      id: row.id,
      lineNo: row.line_no,
      instrument: row.instrument,
      code: row.code,
      faceValueText: row.face_value_text,
      currencyText: row.currency_text,
      maturesOnText: row.matures_on_text,
      location: row.location,
      sourceText: row.source_text,
      currency: row.currency,
      faceMinor: row.face_minor,
      maturesOn: row.matures_on,
      problem: row.problem,
      correctedFaceMinor: row.corrected_face_minor,
      correctedMaturesOn: row.corrected_matures_on,
      leftOut: row.left_out,
      correctedBy: row.corrected_by,
      correctionReason: row.correction_reason,
    })),
    items: json.items.map((item) => ({
      key: item.key,
      kind: item.kind,
      currency: item.currency,
      maturesOn: item.matures_on,
      finvestFaceMinor: item.finvest_face_minor,
      custodianFaceMinor: item.custodian_face_minor,
      differenceMinor: item.difference_minor,
      productCodes: item.product_codes,
      rowIds: item.row_ids,
      problem: item.problem,
      needsNote: item.needs_note,
      note: item.note,
    })),
    unexplained: json.unexplained,
  };
}

const NAMED: ReadonlySet<string> = new Set<CustodianErrorCode>([
  "custodian_reading_off",
  "statement_file_size",
  "statement_type_not_supported",
  "statement_date_in_the_future",
  "check_not_in_review",
  "check_not_waiting",
  "check_needs_second_person",
  "differences_unexplained",
  "note_invalid",
  "correction_empty",
]);

export function toCustodianError(error: unknown): CustodianError {
  if (error instanceof CustodianError) return error;
  if (!(error instanceof ApiError)) return new CustodianError("unavailable");
  const named = error.fieldCode;
  if (named && NAMED.has(named)) return new CustodianError(named as CustodianErrorCode);
  switch (error.code) {
    case "validation_failed":
      return new CustodianError("invalid");
    case "not_found":
      return new CustodianError("not_found");
    case "confirm_it_is_you":
      return new CustodianError("not_confirmed");
    case "forbidden":
      return new CustodianError("forbidden");
    case "conflict":
      return new CustodianError("refused");
    default:
      return new CustodianError("unavailable");
  }
}

export function createHttpCustodianChecksApi(http: Http): CustodianChecksApi {
  const base = "/staff/reconciliation/custodian-checks";
  const one = (id: string) => `${base}/${encodeURIComponent(id)}`;
  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toCustodianError(error);
    }
  }
  const detail = (method: "PUT" | "POST", path: string, body: unknown, key?: string) =>
    call(async () => toDetail(await http.request<DetailJson>(method, path, body, key ? { idempotencyKey: key } : undefined)));
  return {
    list: () =>
      call(async () => {
        const json = await http.request<{ reading_on: boolean; checks: CheckJson[] }>("GET", base);
        return { readingOn: json.reading_on, checks: json.checks.map(toCheck) };
      }),
    get: (id) => call(async () => toDetail(await http.request<DetailJson>("GET", one(id)))),
    upload: (key, statementDate, file) =>
      call(async () => {
        const form = new FormData();
        form.append("statement_date", statementDate);
        form.append("file", file, file.name);
        return toCheck(await http.request<CheckJson>("POST", base, form, { idempotencyKey: key }));
      }),
    fileLink: (id) => call(async () => (await http.request<{ url: string }>("GET", `${one(id)}/file`)).url),
    correctRow: (id, rowId, correction) =>
      detail("PUT", `${one(id)}/rows/${encodeURIComponent(rowId)}`, {
        left_out: correction.leftOut,
        face_minor: correction.leftOut ? null : correction.faceMinor,
        matures_on: correction.leftOut ? null : correction.maturesOn,
        reason: correction.reason,
      }),
    note: (id, itemKey, note) => detail("PUT", `${one(id)}/notes/${encodeURIComponent(itemKey)}`, { note }),
    submit: (key, id, note) => detail("POST", `${one(id)}/submit`, { note }, key),
    signOff: (key, id, note) => detail("POST", `${one(id)}/sign-off`, { note }, key),
    sendBack: (key, id, reason) => detail("POST", `${one(id)}/send-back`, { reason }, key),
  };
}
