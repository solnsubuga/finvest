/** Payouts that came back, over `/staff/payout-returns` and `/staff/payouts/{id}/returns`. */

import { ApiError, type Http } from "@/api/http.ts";
import { ReturnsError, type PayoutReturn, type ReturnsApi, type ReturnsErrorCode } from "./api.ts";

interface ReturnJson {
  id: string;
  payout_id: string;
  client_id: string;
  statement_line_id: string;
  reason: string;
  status: PayoutReturn["status"];
  proposed_by: string;
  proposed_at: string;
}

export function toReturn(json: ReturnJson): PayoutReturn {
  return {
    id: json.id,
    payoutId: json.payout_id,
    clientId: json.client_id,
    statementLineId: json.statement_line_id,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
  };
}

const NAMED: ReadonlySet<string> = new Set<ReturnsErrorCode>([
  "payout_not_paid",
  "statement_line_not_this_payout",
  "statement_line_in_use",
  "return_already_recorded",
  "return_already_decided",
  "proposed_by_you",
  "not_proposed_by_you",
]);

export function toReturnsError(error: unknown): ReturnsError {
  if (error instanceof ReturnsError) return error;
  if (!(error instanceof ApiError)) return new ReturnsError("unavailable");
  const named = error.fieldCode;
  if (named && NAMED.has(named)) return new ReturnsError(named as ReturnsErrorCode);
  switch (error.code) {
    case "validation_failed":
      return new ReturnsError("invalid");
    case "not_found":
      return new ReturnsError("not_found");
    case "confirm_it_is_you":
      return new ReturnsError("not_confirmed");
    case "forbidden":
      return new ReturnsError("forbidden");
    case "conflict":
      return new ReturnsError("refused");
    default:
      return new ReturnsError("unavailable");
  }
}

export function createHttpReturnsApi(http: Http): ReturnsApi {
  const one = (id: string, action: string) => `/staff/payout-returns/${encodeURIComponent(id)}/${action}`;

  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toReturnsError(error);
    }
  }

  return {
    listWaiting: () => call(async () => (await http.request<ReturnJson[]>("GET", "/staff/payout-returns")).map(toReturn)),
    propose: (key, payoutId, statementLineId, reason) =>
      call(async () => {
        await http.request(
          "POST",
          `/staff/payouts/${encodeURIComponent(payoutId)}/returns`,
          { statement_line_id: statementLineId, reason },
          { idempotencyKey: key },
        );
      }),
    approve: (key, id, reason) =>
      call(async () => {
        await http.request("POST", one(id, "approve"), { reason }, { idempotencyKey: key });
      }),
    decline: (id, reason) =>
      call(async () => {
        await http.request("POST", one(id, "decline"), { reason });
      }),
    withdraw: (id, reason) =>
      call(async () => {
        await http.request("POST", one(id, "withdraw"), { reason });
      }),
  };
}
