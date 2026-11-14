/** Run requests over the real API: `/staff/investment-run-requests`. */

import type { RunRequestOut } from "@finvest/api-client/staff";
import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import { RunRequestsError, type RunField, type RunProblem, type RunRequest, type RunRequestsApi } from "./api.ts";

export function toRunRequest(json: RunRequestOut): RunRequest {
  return {
    id: json.id,
    kind: json.kind,
    runDate: json.run_date,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

const FIELDS: readonly string[] = ["run_date", "reason", "note"] satisfies RunField[];

function problems(error: ApiError): RunProblem[] {
  return error.fields.flatMap((problem) => {
    const field = problem.field_path[problem.field_path.length - 1];
    const code =
      problem.code === "string_too_long" ? "too_long" : problem.code === "missing" || problem.code === "string_too_short" ? "required" : problem.code;
    return typeof field === "string" && FIELDS.includes(field) ? [{ field: field as RunField, code }] : [];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toRunRequestsError(error: unknown, deciding = false): RunRequestsError {
  if (error instanceof RunRequestsError) return error;
  if (!(error instanceof ApiError)) return new RunRequestsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new RunRequestsError("invalid", problems(error));
    case "conflict":
      return new RunRequestsError(error.fields.some((f) => f.code === "run_date_passed") ? "day_passed" : "conflict");
    case CONFIRM_IT_IS_YOU:
      return new RunRequestsError("not_confirmed");
    case "forbidden":
      return new RunRequestsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new RunRequestsError("not_found");
    default:
      return new RunRequestsError("unavailable");
  }
}

const PATH = "/staff/investment-run-requests";

export function createHttpRunRequestsApi(http: Http): RunRequestsApi {
  async function command(key: string, path: string, body: unknown, deciding: boolean): Promise<RunRequest> {
    try {
      return toRunRequest(await http.request<RunRequestOut>("POST", path, body, { idempotencyKey: key }));
    } catch (error) {
      throw toRunRequestsError(error, deciding);
    }
  }
  const one = (id: string, step: string) => `${PATH}/${encodeURIComponent(id)}/${step}`;
  return {
    async list() {
      try {
        return (await http.request<RunRequestOut[]>("GET", `${PATH}?limit=50`)).map(toRunRequest);
      } catch (error) {
        throw toRunRequestsError(error);
      }
    },
    propose: (key, request) => command(key, PATH, { kind: request.kind, run_date: request.runDate, reason: request.reason }, false),
    approve: (key, id) => command(key, one(id, "approve"), undefined, true),
    decline: (key, id, note) => command(key, one(id, "decline"), { note }, true),
    withdraw: (key, id) => command(key, one(id, "withdraw"), undefined, false),
  };
}
