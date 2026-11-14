/**
 * Putting mistaken payments right, over the real API (`/staff/payments/reversals…` and
 * `/staff/payments/receivables…`, docs/payments.md).
 *
 * Approving can be refused with `confirm_it_is_you`. `createHttp` asks the confirm-it-is-you
 * dialog and sends the request again with the same key, so the refusal only reaches the screen
 * if the person closes the dialog.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "../api.ts";
import {
  CorrectionError,
  REFUSALS,
  kindOf,
  type CorrectionDetail,
  type CorrectionStatus,
  type CorrectionSummary,
  type Kind,
  type Method,
  type MistakenPaymentsApi,
  type Refusal,
} from "./api.ts";

interface Common {
  id: string;
  deposit_id: string;
  client_id: string;
  amount: Amount;
  reason: string;
  proposed_at: string;
  decided_at: string | null;
  decision_note: string | null;
}

interface ReversalJson extends Common {
  status: "proposed" | "approved" | "sent_back" | "withdrawn" | "done" | "failed";
  investment_id: string;
  failure_code: string | null;
}

interface StepJson extends Common {
  kind: "open" | "recover" | "write_off";
  status: "proposed" | "applied" | "sent_back" | "withdrawn";
}

interface Decision {
  method: Method;
  refusal: Refusal | null;
  can_decide: boolean;
  yours: boolean;
}

interface ReversalDetailJson extends Decision {
  reversal: ReversalJson;
  waiting: Amount;
}

interface StepDetailJson extends Decision {
  step: StepJson;
  owed: Amount;
}

function common(json: Common, status: CorrectionStatus) {
  return {
    id: json.id,
    status,
    depositId: json.deposit_id,
    clientId: json.client_id,
    amount: json.amount,
    reason: json.reason,
    proposedAt: json.proposed_at,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

export const fromReversal = (json: ReversalJson): CorrectionSummary => ({
  ...common(json, json.status),
  step: "reverse",
  investmentId: json.investment_id,
  failureCode: json.failure_code,
});

export const fromStep = (json: StepJson): CorrectionSummary => ({
  ...common(json, json.status),
  step: json.kind,
  investmentId: null,
  failureCode: null,
});

const decision = (json: Decision) => ({ method: json.method, refusal: json.refusal, canDecide: json.can_decide, yours: json.yours });

function fromDetail(kind: Kind, json: ReversalDetailJson | StepDetailJson): CorrectionDetail {
  if (kind === "reversal") {
    const detail = json as ReversalDetailJson;
    return { ...fromReversal(detail.reversal), ...decision(detail), balance: detail.waiting };
  }
  const detail = json as StepDetailJson;
  return { ...fromStep(detail.step), ...decision(detail), balance: detail.owed };
}

/** The API's refusal as the screen's own error, keeping which field it is about. */
export function toCorrectionError(error: unknown): CorrectionError {
  if (!(error instanceof ApiError)) return new CorrectionError("unavailable");
  if (error.code === "validation_failed") {
    const problem = error.fields[0];
    const field = problem?.field_path[problem.field_path.length - 1];
    const code = problem?.code ?? "";
    if (code in REFUSALS) return new CorrectionError(code as Refusal, "depositId");
    if (code === "insufficient_funds") return new CorrectionError("insufficient_funds", "depositId");
    if (field === "note") return new CorrectionError("invalid", "note");
    return new CorrectionError("invalid", field === "reason" ? "reason" : null);
  }
  switch (error.code) {
    // The key was spent on a different request: treated as someone having acted, so the step is
    // reloaded and the next try gets a key of its own.
    case "idempotency_key_reused":
      return new CorrectionError("conflict");
    case "not_found":
    case "conflict":
    case "forbidden":
    case "confirm_it_is_you":
    case "unavailable":
      return new CorrectionError(error.code);
    default:
      // Anything else is treated as an answer that never arrived, so a retry reuses the key.
      return new CorrectionError("unavailable");
  }
}

const BASES: Record<Kind, string> = {
  reversal: "/staff/payments/reversals",
  receivable: "/staff/payments/receivables",
};

export function createHttpMistakenPaymentsApi(http: Http): MistakenPaymentsApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toCorrectionError(error);
    }
  }
  const detail = (kind: Kind, method: "GET" | "POST", path: string, body?: unknown, key?: string) =>
    call(async () =>
      fromDetail(kind, await http.request<ReversalDetailJson | StepDetailJson>(method, `${BASES[kind]}${path}`, body, key ? { idempotencyKey: key } : undefined)),
    );
  const one = (id: string) => `/${encodeURIComponent(id)}`;

  return {
    list: () =>
      call(async () => {
        const [reversals, steps] = await Promise.all([
          http.request<ReversalJson[]>("GET", BASES.reversal),
          http.request<StepJson[]>("GET", BASES.receivable),
        ]);
        return [...reversals.map(fromReversal), ...steps.map(fromStep)].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt));
      }),
    outstanding: () => call(async () => (await http.request<StepJson[]>("GET", `${BASES.receivable}/outstanding`)).map(fromStep)),
    get: (kind, id) => detail(kind, "GET", one(id)),
    async propose(key, proposal) {
      const kind = kindOf(proposal.step);
      const body =
        kind === "reversal"
          ? { deposit_id: proposal.depositId, reason: proposal.reason }
          : { kind: proposal.step, deposit_id: proposal.depositId, reason: proposal.reason };
      try {
        return await detail(kind, "POST", "", body, key);
      } catch (error) {
        // A payment ID that names nothing is the route's own 404, about the form's first field.
        if (error instanceof CorrectionError && error.code === "not_found") throw new CorrectionError("deposit_not_found", "depositId");
        throw error;
      }
    },
    approve: (key, kind, id) => detail(kind, "POST", `${one(id)}/approve`, undefined, key),
    sendBack: (key, kind, id, note) => detail(kind, "POST", `${one(id)}/send-back`, { note }, key),
    withdraw: (key, kind, id) => detail(kind, "POST", `${one(id)}/withdraw`, undefined, key),
  };
}
