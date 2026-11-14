/**
 * The contract between the Documents screen and the backend's routes in
 * `backend/app/modules/documents/api.py` (docs/documents.md):
 *
 * - `POST /client/statements` with `{date_from, date_to}` and an Idempotency-Key
 * - `GET /client/documents`, newest first
 * - `GET /client/documents/{id}`, polled until a statement is ready
 * - `GET /client/documents/{id}/file`, the PDF
 *
 * Figures are only ever in the PDF, after tax. The list carries none.
 */

/**
 * `tax_certificate` covers Uganda's tax year, 1 July to 30 June, and shows the tax withheld.
 * `investment_note` confirms an investment started.
 */
export type DocumentKind =
  | "monthly_statement"
  | "requested_statement"
  | "receipt"
  | "tax_certificate"
  | "investment_note";
export type DocumentStatus = "pending" | "ready" | "failed";

export type DocumentItem = {
  id: string;
  kind: DocumentKind;
  status: DocumentStatus;
  /** YYYY-MM-DD. Null for a receipt. */
  dateFrom: string | null;
  dateTo: string | null;
  /** ISO time. */
  requestedAt: string;
  finishedAt: string | null;
  /** "Finvest statement October 2026.pdf". Set once ready. */
  fileName: string | null;
  byteSize: number | null;
  /**
   * For a statement reaching days the books have not closed yet: the first day
   * whose figures may still change. Null when every figure is final.
   */
  notFinalFrom: string | null;
  /** The investment an investment note confirms. Null on every other kind. */
  investmentId: string | null;
  /** The payment a receipt is for. Null on every other kind. */
  depositId: string | null;
};

/** Why a statement could not be asked for, from the backend's field codes. */
export type PeriodProblem =
  | "period_backwards"
  | "period_in_future"
  | "period_too_long";

export class DocumentsError extends Error {
  constructor(
    readonly code:
      | "unavailable"
      | "offline"
      | "rate_limited"
      | "validation_failed"
      | "not_found",
    readonly field: PeriodProblem | null = null,
  ) {
    super(code);
    this.name = "DocumentsError";
  }
}

export interface DocumentsClient {
  listDocuments(): Promise<DocumentItem[]>;
  getDocument(id: string): Promise<DocumentItem>;
  /** Answers with a `pending` statement. The same key asks only once. */
  requestStatement(
    dateFrom: string,
    dateTo: string,
    idempotencyKey: string,
  ): Promise<DocumentItem>;
  /** Saves or opens the PDF on this device. */
  download(document: DocumentItem): Promise<void>;
}
