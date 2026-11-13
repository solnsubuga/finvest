import type { DocumentItem as ApiDocument } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { savePdf } from "./savePdf";
import { DocumentItem, DocumentsClient, DocumentsError, PeriodProblem } from "./types";

/**
 * The Documents screen's contract, answered by the real API
 * (backend/app/modules/documents/api.py). The PDF is a binary answer, which
 * `api()` does not read, so `savePdf` fetches it with the same session.
 */

const PERIOD_PROBLEMS: ReadonlySet<string> = new Set<PeriodProblem>([
  "period_backwards",
  "period_in_future",
  "period_too_long",
]);

export function fromApi(item: ApiDocument): DocumentItem {
  return {
    id: item.id,
    kind: item.kind,
    status: item.status,
    dateFrom: item.date_from,
    dateTo: item.date_to,
    requestedAt: item.requested_at,
    finishedAt: item.finished_at,
    fileName: item.file_name,
    byteSize: item.byte_size,
    notFinalFrom: item.not_final_from,
  };
}

/** What the API or the network said, as a reason the screen can show. */
export function toDocumentsError(error: unknown): DocumentsError {
  if (error instanceof DocumentsError) return error;
  if (error instanceof ApiError) {
    if (error.code === "validation_failed") {
      const problem = error.fields.find((field) => PERIOD_PROBLEMS.has(field.code));
      return new DocumentsError("validation_failed", (problem?.code as PeriodProblem | undefined) ?? null);
    }
    if (error.code === "rate_limited" || error.code === "not_found") {
      return new DocumentsError(error.code);
    }
  }
  return new DocumentsError("unavailable");
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toDocumentsError(error);
  }
}

export function createApiDocumentsClient(): DocumentsClient {
  return {
    listDocuments: () =>
      wrap(async () => (await api("GET /client/documents", { query: { limit: 100 } })).items.map(fromApi)),
    getDocument: (id) =>
      wrap(async () => fromApi(await api("GET /client/documents/{document_id}", { params: { document_id: id } }))),
    requestStatement: (dateFrom, dateTo, idempotencyKey) =>
      wrap(async () =>
        fromApi(
          await api("POST /client/statements", {
            body: { date_from: dateFrom, date_to: dateTo },
            idempotencyKey,
          }),
        ),
      ),
    download: (document) =>
      wrap(async () => {
        if (document.status !== "ready") throw new DocumentsError("not_found");
        await savePdf(`/client/documents/${encodeURIComponent(document.id)}/file`, safeFileName(document));
      }),
  };
}

/** The name the PDF is saved under: the API's own, kept to plain characters. */
export function safeFileName(document: DocumentItem): string {
  const name = (document.fileName ?? "").replace(/[^A-Za-z0-9 ._-]/g, "").trim();
  return /\.pdf$/i.test(name) ? name : `Finvest document ${document.id.replace(/[^A-Za-z0-9_-]/g, "")}.pdf`;
}
