import type { DecideCloseIn, MonthCloseOut, MonthClosesOut, ProposeCloseIn } from "@finvest/api-client/staff";
import type { Http } from "@/api/http.ts";
import { toMonthCloseError, type MonthClose, type MonthCloseApi } from "./monthClose.ts";

function toClose(json: MonthCloseOut): MonthClose {
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
        const found = await http.request<MonthClosesOut>("GET", PATH);
        return {
          lastClosed: found.last_closed,
          next: { month: found.next.month, closedThrough: found.next.closed_through, refusal: found.next.refusal },
          closes: found.closes.map(toClose),
        };
      }),
    propose: (month, reason, idempotencyKey) => {
      const body: ProposeCloseIn = { month, reason };
      return guarded(async () => toClose(await http.request<MonthCloseOut>("POST", PATH, body, { idempotencyKey })));
    },
    decide: (id, decision, reason, idempotencyKey) => {
      const body: DecideCloseIn = { reason };
      return guarded(async () =>
        toClose(await http.request<MonthCloseOut>("POST", `${PATH}/${encodeURIComponent(id)}/${decision}`, body, decision === "approve" ? { idempotencyKey } : undefined)),
      );
    },
  };
}
