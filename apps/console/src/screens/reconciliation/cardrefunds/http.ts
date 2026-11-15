/**
 * Card refunds over the real API (`/staff/payments/card-refunds…`, docs/payments.md).
 *
 * Approving and saying what Pesapal shows can be refused with `confirm_it_is_you`. `createHttp`
 * asks the confirm-it-is-you dialog and sends the request again with the same key, so the
 * refusal only reaches the screen if the person closes the dialog.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "../api.ts";
import { REFUSALS, RefundError, type CardRefundsApi, type Found, type Refusal, type RefundDetail, type RefundStatus, type RefundSummary } from "./api.ts";

interface RefundJson {
  id: string;
  status: RefundStatus;
  deposit_id: string;
  client_id: string;
  investment_id: string;
  provider: string;
  amount: Amount;
  charge: Amount;
  finvest_erred: boolean;
  total: Amount;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  sent_at: string | null;
  found: Found | null;
  found_by: string | null;
  found_at: string | null;
  found_note: string | null;
  done_at: string | null;
  failure_code: string | null;
}

interface DetailJson {
  refund: RefundJson;
  card_brand: string | null;
  card_last_four: string | null;
  waiting: Amount;
  refusal: Refusal | null;
  can_decide: boolean;
  can_record_finding: boolean;
  yours: boolean;
}

export const fromRefund = (json: RefundJson): RefundSummary => ({
  id: json.id,
  status: json.status,
  depositId: json.deposit_id,
  clientId: json.client_id,
  investmentId: json.investment_id,
  amount: json.amount,
  charge: json.charge,
  finvestErred: json.finvest_erred,
  total: json.total,
  reason: json.reason,
  proposedAt: json.proposed_at,
  decidedAt: json.decided_at,
  decisionNote: json.decision_note,
  sentAt: json.sent_at,
  found: json.found,
  foundAt: json.found_at,
  foundNote: json.found_note,
  doneAt: json.done_at,
  failureCode: json.failure_code,
});

export const fromDetail = (json: DetailJson): RefundDetail => ({
  ...fromRefund(json.refund),
  cardBrand: json.card_brand,
  cardLastFour: json.card_last_four,
  waiting: json.waiting,
  refusal: json.refusal,
  canDecide: json.can_decide,
  canRecordFinding: json.can_record_finding,
  yours: json.yours,
});

/** The API's refusal as the screen's own error, keeping which field it is about. */
export function toRefundError(error: unknown): RefundError {
  if (!(error instanceof ApiError)) return new RefundError("unavailable");
  if (error.code === "validation_failed") {
    const problem = error.fields[0];
    const field = problem?.field_path[problem.field_path.length - 1];
    const code = problem?.code ?? "";
    if (code in REFUSALS) return new RefundError(code as Refusal, "depositId");
    if (field === "note") return new RefundError("invalid", "note");
    return new RefundError("invalid", field === "reason" ? "reason" : null);
  }
  switch (error.code) {
    // The key was spent on a different request: treated as someone having acted, so the refund
    // is reloaded and the next try gets a key of its own.
    case "idempotency_key_reused":
      return new RefundError("conflict");
    case "not_found":
    case "conflict":
    case "forbidden":
    case "confirm_it_is_you":
    case "unavailable":
      return new RefundError(error.code);
    default:
      // Anything else is treated as an answer that never arrived, so a retry reuses the key.
      return new RefundError("unavailable");
  }
}

const BASE = "/staff/payments/card-refunds";

export function createHttpCardRefundsApi(http: Http): CardRefundsApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toRefundError(error);
    }
  }
  const post = (path: string, key: string, body?: unknown) =>
    call(async () => fromDetail(await http.request<DetailJson>("POST", `${BASE}${path}`, body, { idempotencyKey: key })));
  const one = (id: string) => `/${encodeURIComponent(id)}`;

  return {
    list: () => call(async () => (await http.request<RefundJson[]>("GET", BASE)).map(fromRefund)),
    get: (id) => call(async () => fromDetail(await http.request<DetailJson>("GET", `${BASE}${one(id)}`))),
    async propose(key, proposal) {
      try {
        return await post("", key, { deposit_id: proposal.depositId, reason: proposal.reason, finvest_erred: proposal.finvestErred });
      } catch (error) {
        // A payment ID that names nothing is the route's own 404, about the form's first field.
        if (error instanceof RefundError && error.code === "not_found") throw new RefundError("deposit_not_found", "depositId");
        throw error;
      }
    },
    approve: (key, id) => post(`${one(id)}/approve`, key),
    sendBack: (key, id, note) => post(`${one(id)}/send-back`, key, { note }),
    withdraw: (key, id) => post(`${one(id)}/withdraw`, key),
    recordFinding: (key, id, found, note) => post(`${one(id)}/finding`, key, { found, note }),
  };
}
