/**
 * Moving payments credited to the wrong client, over the real API
 * (`/staff/payments/reassignments…`, docs/payments.md).
 *
 * Approving can be refused with `confirm_it_is_you`. `createHttp` asks the confirm-it-is-you
 * dialog and sends the request again with the same key, so the refusal only reaches the screen
 * if the person closes the dialog.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "../api.ts";
import {
  REFUSALS,
  ReassignmentError,
  type Party,
  type ReassignmentDetail,
  type ReassignmentsApi,
  type ReassignmentStatus,
  type ReassignmentSummary,
  type Refusal,
} from "./api.ts";

interface ReassignmentJson {
  id: string;
  status: ReassignmentStatus;
  deposit_id: string;
  from_investment_id: string;
  to_investment_id: string;
  amount: Amount;
  reason: string;
  proposed_at: string;
  decided_at: string | null;
  decision_note: string | null;
  new_deposit_id: string | null;
}

interface PartyJson {
  client_id: string;
  name: string | null;
  account_number: string | null;
}

interface DetailJson {
  reassignment: ReassignmentJson;
  from_client: PartyJson;
  to_client: PartyJson;
  line: {
    id: string;
    account_reference: string;
    value_date: string;
    amount: Amount;
    reference: string;
    narrative: string;
  } | null;
  reference_names_to_client: boolean;
  reference_names_from_client: boolean;
  refusal: Refusal | null;
  can_decide: boolean;
  yours: boolean;
}

function toSummary(json: ReassignmentJson): ReassignmentSummary {
  return {
    id: json.id,
    status: json.status,
    depositId: json.deposit_id,
    fromInvestmentId: json.from_investment_id,
    toInvestmentId: json.to_investment_id,
    amount: json.amount,
    reason: json.reason,
    proposedAt: json.proposed_at,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
    newDepositId: json.new_deposit_id,
  };
}

const toParty = (json: PartyJson): Party => ({ clientId: json.client_id, name: json.name, accountNumber: json.account_number });

export function toDetail(json: DetailJson): ReassignmentDetail {
  const { line } = json;
  return {
    ...toSummary(json.reassignment),
    fromClient: toParty(json.from_client),
    toClient: toParty(json.to_client),
    line: line && {
      id: line.id,
      accountReference: line.account_reference,
      valueDate: line.value_date,
      amount: line.amount,
      reference: line.reference,
      narrative: line.narrative,
    },
    referenceNamesToClient: json.reference_names_to_client,
    referenceNamesFromClient: json.reference_names_from_client,
    refusal: json.refusal,
    canDecide: json.can_decide,
    yours: json.yours,
  };
}

/** The API's refusal as the screen's own error, keeping which field it is about. */
export function toReassignmentError(error: unknown): ReassignmentError {
  if (!(error instanceof ApiError)) return new ReassignmentError("unavailable");
  if (error.code === "validation_failed") {
    const problem = error.fields[0];
    const field = problem?.field_path[problem.field_path.length - 1];
    const code = problem?.code ?? "";
    if (code in REFUSALS) {
      return new ReassignmentError(code as Refusal, field === "deposit_id" ? "depositId" : "toInvestmentId");
    }
    if (code === "not_found" && field === "to_investment_id") return new ReassignmentError("investment_not_found", "toInvestmentId");
    if (field === "note") return new ReassignmentError("invalid", "note");
    return new ReassignmentError("invalid", field === "reason" ? "reason" : null);
  }
  switch (error.code) {
    case "not_found":
    case "conflict":
    case "forbidden":
    case "confirm_it_is_you":
    case "unavailable":
      return new ReassignmentError(error.code);
    default:
      // Anything else is treated as an answer that never arrived, so a retry reuses the key.
      return new ReassignmentError("unavailable");
  }
}

const BASE = "/staff/payments/reassignments";

export function createHttpReassignmentsApi(http: Http): ReassignmentsApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toReassignmentError(error);
    }
  }
  const detail = (method: "GET" | "POST", path: string, body?: unknown, key?: string) =>
    call(async () => toDetail(await http.request<DetailJson>(method, path, body, key ? { idempotencyKey: key } : undefined)));

  return {
    list: () => call(async () => (await http.request<ReassignmentJson[]>("GET", BASE)).map(toSummary)),
    get: (id) => detail("GET", `${BASE}/${encodeURIComponent(id)}`),
    async propose(key, proposal) {
      try {
        return await detail(
          "POST",
          BASE,
          { deposit_id: proposal.depositId, to_investment_id: proposal.toInvestmentId, reason: proposal.reason },
          key,
        );
      } catch (error) {
        // A payment ID that names nothing is the route's own 404, about the form's first field.
        if (error instanceof ReassignmentError && error.code === "not_found") {
          throw new ReassignmentError("deposit_not_found", "depositId");
        }
        throw error;
      }
    },
    approve: (key, id) => detail("POST", `${BASE}/${encodeURIComponent(id)}/approve`, undefined, key),
    sendBack: (key, id, note) => detail("POST", `${BASE}/${encodeURIComponent(id)}/send-back`, { note }, key),
    withdraw: (key, id) => detail("POST", `${BASE}/${encodeURIComponent(id)}/withdraw`, undefined, key),
  };
}
