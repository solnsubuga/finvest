/** Note reissues over the real API: `/staff/note-reissues`. */

import type { ReissueCount, ReissueOut } from "@finvest/api-client/staff";
import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import { ReissuesError, type Reissue, type ReissueField, type ReissueProblem, type ReissuesApi } from "./api.ts";

export function toReissue(json: ReissueOut): Reissue {
  return {
    id: json.id,
    clientId: json.client_id,
    issuedFrom: json.issued_from,
    issuedTo: json.issued_to,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
    noteCount: json.note_count,
    copies: { ...json.copies },
    finishedAt: json.finished_at,
  };
}

const FIELDS: readonly string[] = ["client_id", "issued_from", "issued_to", "reason", "note"] satisfies ReissueField[];

function problems(error: ApiError): ReissueProblem[] {
  return error.fields.flatMap((problem) => {
    const field = problem.field_path[problem.field_path.length - 1];
    const code =
      problem.code === "string_too_long" ? "too_long" : problem.code === "missing" || problem.code === "string_too_short" ? "required" : problem.code;
    return typeof field === "string" && FIELDS.includes(field) ? [{ field: field as ReissueField, code }] : [];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toReissuesError(error: unknown, deciding = false): ReissuesError {
  if (error instanceof ReissuesError) return error;
  if (!(error instanceof ApiError)) return new ReissuesError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new ReissuesError("invalid", problems(error));
    case "conflict":
      return new ReissuesError("conflict");
    case CONFIRM_IT_IS_YOU:
      return new ReissuesError("not_confirmed");
    case "forbidden":
      return new ReissuesError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new ReissuesError("not_found");
    default:
      return new ReissuesError("unavailable");
  }
}

const PATH = "/staff/note-reissues";

export function createHttpReissuesApi(http: Http): ReissuesApi {
  async function command(key: string, path: string, body: unknown, deciding: boolean): Promise<Reissue> {
    try {
      return toReissue(await http.request<ReissueOut>("POST", path, body, { idempotencyKey: key }));
    } catch (error) {
      throw toReissuesError(error, deciding);
    }
  }
  const one = (id: string, step: string) => `${PATH}/${encodeURIComponent(id)}/${step}`;
  return {
    async list() {
      try {
        return (await http.request<ReissueOut[]>("GET", `${PATH}?limit=50`)).map(toReissue);
      } catch (error) {
        throw toReissuesError(error);
      }
    },
    async count(scope) {
      const query = new URLSearchParams({ issued_from: scope.issuedFrom, issued_to: scope.issuedTo });
      if (scope.clientId) query.set("client_id", scope.clientId);
      try {
        return (await http.request<ReissueCount>("GET", `${PATH}/count?${query.toString()}`)).note_count;
      } catch (error) {
        throw toReissuesError(error);
      }
    },
    propose: (key, request) =>
      command(
        key,
        PATH,
        { client_id: request.clientId, issued_from: request.issuedFrom, issued_to: request.issuedTo, reason: request.reason },
        false,
      ),
    approve: (key, id) => command(key, one(id, "approve"), undefined, true),
    decline: (key, id, note) => command(key, one(id, "decline"), { note }, true),
    withdraw: (key, id) => command(key, one(id, "withdraw"), undefined, false),
  };
}
