import type { Http } from "@/api/http.ts";
import type { Currency } from "./api.ts";
import { toMonthCloseError, type CloseStatus, type MonthClose, type MonthCloseApi } from "./monthClose.ts";

interface CloseJson {
  id: string;
  month: string;
  status: CloseStatus;
  books: { currency: Currency; code: string; kind: string; opening: number; debits: number; credits: number; closing: number }[];
  books_sha256: string;
  reason: string;
  proposed_by: string;
  proposed_by_name: string | null;
  proposed_at: string;
  decided_by: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  yours: boolean;
}

function toClose(json: CloseJson): MonthClose {
  return {
    id: json.id,
    month: json.month,
    status: json.status,
    books: json.books,
    booksSha256: json.books_sha256,
    reason: json.reason,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    yours: json.yours,
  };
}

async function guarded<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toMonthCloseError(error);
  }
}

const PATH = "/staff/finance/month-closes";

export function createHttpMonthCloseApi(http: Http): MonthCloseApi {
  return {
    getCloses: () =>
      guarded(async () => {
        const found = await http.request<{
          last_closed: string | null;
          next: { month: string; closed_through: string | null; refusal: string | null };
          closes: CloseJson[];
        }>("GET", PATH);
        return {
          lastClosed: found.last_closed,
          next: { month: found.next.month, closedThrough: found.next.closed_through, refusal: found.next.refusal },
          closes: found.closes.map(toClose),
        };
      }),
    propose: (month, reason, idempotencyKey) => guarded(async () => toClose(await http.request<CloseJson>("POST", PATH, { month, reason }, { idempotencyKey }))),
    decide: (id, decision, reason, idempotencyKey) =>
      guarded(async () =>
        toClose(
          await http.request<CloseJson>("POST", `${PATH}/${encodeURIComponent(id)}/${decision}`, { reason }, decision === "approve" ? { idempotencyKey } : undefined),
        ),
      ),
  };
}
