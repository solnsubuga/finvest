/**
 * Payouts that need a person, over the real API (`/staff/payouts/held-up`, `/staff/payouts/stranded`
 * and `/staff/payout-resolutions`, docs/payouts.md).
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  PayoutsError,
  type Amount,
  type HeldUpPayout,
  type Outcome,
  type PayoutsApi,
  type PayoutsErrorCode,
  type StrandedPayout,
} from "./api.ts";

interface HeldUpJson {
  id: string;
  client_id: string;
  kind: "mobile_money";
  amount: Amount;
  created_at: string;
  first_tried_at: string;
  last_tried_at: string;
  tries: number;
}

interface ResolutionJson {
  id: string;
  payout_id: string;
  outcome: Outcome;
  statement_line_id: string | null;
  statement_id: string | null;
  reason: string;
  status: "proposed" | "approved" | "declined" | "withdrawn";
  proposed_by: string;
  proposed_at: string;
}

interface StrandedJson {
  id: string;
  client_id: string;
  status: "sent" | "uncertain";
  amount: Amount;
  first_sent_at: string;
  resolution: ResolutionJson | null;
}

export function toHeldUp(json: HeldUpJson): HeldUpPayout {
  return {
    id: json.id,
    clientId: json.client_id,
    amount: json.amount,
    createdAt: json.created_at,
    firstTriedAt: json.first_tried_at,
    lastTriedAt: json.last_tried_at,
    tries: json.tries,
  };
}

export function toStranded(json: StrandedJson): StrandedPayout {
  const open = json.resolution?.status === "proposed" ? json.resolution : null;
  return {
    id: json.id,
    clientId: json.client_id,
    status: json.status,
    amount: json.amount,
    firstSentAt: json.first_sent_at,
    resolution: open
      ? {
          id: open.id,
          outcome: open.outcome,
          statementLineId: open.statement_line_id,
          statementId: open.statement_id,
          reason: open.reason,
          proposedBy: open.proposed_by,
          proposedAt: open.proposed_at,
        }
      : null,
  };
}

/** The refusals the backend names in `fields`, which the screen words for staff. */
const NAMED: ReadonlySet<string> = new Set<PayoutsErrorCode>([
  "provider_still_being_read",
  "payout_not_stranded",
  "statement_line_required",
  "statement_required",
  "statement_line_not_this_payout",
  "payout_on_statement",
  "statement_not_this_wallet",
  "statement_too_early",
  "statement_day_missing",
  "resolution_already_proposed",
  "proposed_by_you",
  "not_proposed_by_you",
  "resolution_already_decided",
]);

export function toPayoutsError(error: unknown): PayoutsError {
  if (error instanceof PayoutsError) return error;
  if (!(error instanceof ApiError)) return new PayoutsError("unavailable");
  const named = error.fieldCode;
  if (named && NAMED.has(named)) return new PayoutsError(named as PayoutsErrorCode);
  switch (error.code) {
    case "validation_failed":
      return new PayoutsError("invalid");
    case "not_found":
      return new PayoutsError("not_found");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new PayoutsError("not_confirmed");
    case "forbidden":
      return new PayoutsError("forbidden");
    // A refusal of the current state that this screen does not name yet. It is a definite
    // answer, not a lost one.
    case "conflict":
      return new PayoutsError("refused");
    default:
      return new PayoutsError("unavailable");
  }
}

export function createHttpPayoutsApi(http: Http): PayoutsApi {
  const resolution = (id: string, action: string) => `/staff/payout-resolutions/${encodeURIComponent(id)}/${action}`;

  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toPayoutsError(error);
    }
  }

  return {
    listHeldUp: () => call(async () => (await http.request<HeldUpJson[]>("GET", "/staff/payouts/held-up")).map(toHeldUp)),
    listStranded: () =>
      call(async () => (await http.request<StrandedJson[]>("GET", "/staff/payouts/stranded")).map(toStranded)),
    propose: (key, payoutId, proposal) =>
      call(async () => {
        await http.request(
          "POST",
          `/staff/payouts/${encodeURIComponent(payoutId)}/resolutions`,
          {
            outcome: proposal.outcome,
            statement_line_id: proposal.outcome === "paid" ? proposal.statementLineId || null : null,
            statement_id: proposal.outcome === "failed" ? proposal.statementId || null : null,
            reason: proposal.reason,
          },
          { idempotencyKey: key },
        );
      }),
    approve: (key, id, reason) =>
      call(async () => {
        await http.request("POST", resolution(id, "approve"), { reason }, { idempotencyKey: key });
      }),
    decline: (id, reason) =>
      call(async () => {
        await http.request("POST", resolution(id, "decline"), { reason });
      }),
    withdraw: (id, reason) =>
      call(async () => {
        await http.request("POST", resolution(id, "withdraw"), { reason });
      }),
  };
}
