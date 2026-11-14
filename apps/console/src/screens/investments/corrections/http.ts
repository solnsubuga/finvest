/**
 * Investment corrections over the real API: `/staff/investment-corrections`
 * in backend/app/modules/investments/api.py.
 */

import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import type { Currency } from "../../products/api.ts";
import {
  CorrectionsError,
  type Correction,
  type CorrectionField,
  type CorrectionKind,
  type CorrectionProblem,
  type CorrectionStatus,
  type CorrectionsApi,
} from "./api.ts";

interface CorrectionJson {
  id: string;
  kind: CorrectionKind;
  investment_id: string;
  client_id: string;
  amount_minor: number;
  currency: Currency;
  destination_id: string | null;
  reason: string;
  status: CorrectionStatus;
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  done_at: string | null;
  entry_id: string | null;
  payout_id: string | null;
  failure_code: string | null;
}

export function toCorrection(json: CorrectionJson): Correction {
  return {
    id: json.id,
    kind: json.kind,
    investmentId: json.investment_id,
    clientId: json.client_id,
    amount: { minor: json.amount_minor, currency: json.currency },
    destinationId: json.destination_id,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
    doneAt: json.done_at,
    payoutId: json.payout_id,
    failureCode: json.failure_code,
  };
}

const FIELDS: readonly string[] = ["investment_id", "reason", "destination_id", "note"] satisfies CorrectionField[];

function problems(error: ApiError): CorrectionProblem[] {
  return error.fields.flatMap((problem) => {
    const field = problem.field_path[problem.field_path.length - 1];
    // Pydantic's own codes for an empty or long note become the screen's.
    const code =
      problem.code === "string_too_long"
        ? "too_long"
        : problem.code === "missing" || problem.code === "string_too_short"
          ? "required"
          : problem.code;
    return typeof field === "string" && FIELDS.includes(field) ? [{ field: field as CorrectionField, code }] : [];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toCorrectionsError(error: unknown, deciding = false): CorrectionsError {
  if (error instanceof CorrectionsError) return error;
  if (!(error instanceof ApiError)) return new CorrectionsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new CorrectionsError("invalid", problems(error));
    case "conflict":
      return new CorrectionsError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case CONFIRM_IT_IS_YOU:
      return new CorrectionsError("not_confirmed");
    case "forbidden":
      return new CorrectionsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new CorrectionsError("not_found");
    default:
      return new CorrectionsError("unavailable");
  }
}

const PATH = "/staff/investment-corrections";

export function createHttpCorrectionsApi(http: Http): CorrectionsApi {
  async function command(key: string, path: string, body: unknown, deciding: boolean): Promise<Correction> {
    try {
      return toCorrection(await http.request<CorrectionJson>("POST", path, body, { idempotencyKey: key }));
    } catch (error) {
      throw toCorrectionsError(error, deciding);
    }
  }
  const one = (id: string, step: string) => `${PATH}/${encodeURIComponent(id)}/${step}`;

  return {
    async list() {
      try {
        return (await http.request<CorrectionJson[]>("GET", `${PATH}?limit=200`)).map(toCorrection);
      } catch (error) {
        throw toCorrectionsError(error);
      }
    },
    propose: (key, correction) =>
      command(
        key,
        PATH,
        {
          kind: correction.kind,
          investment_id: correction.investmentId,
          reason: correction.reason,
          // Never sent for an unwind, which the backend refuses.
          ...(correction.kind === "unwind" ? {} : { destination_id: correction.destinationId }),
        },
        false,
      ),
    approve: (key, id) => command(key, one(id, "approve"), undefined, true),
    sendBack: (key, id, note) => command(key, one(id, "send-back"), { note }, true),
    withdraw: (key, id) => command(key, one(id, "withdraw"), undefined, false),
  };
}
