/**
 * The liquidity buffer over the real API (backend/app/modules/dealing/buffer_api.py).
 * Every command answers with the whole screen, so nothing is refetched after a write.
 */

import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import type { Amount, BufferLevel, Currency } from "./api.ts";
import {
  BufferError,
  type BufferApi,
  type BufferErrorCode,
  type BufferScreen,
  type BufferStep,
  type Direction,
  type Movement,
  type MovementStatus,
  type NewMovement,
  type NewRules,
  type ProposalStatus,
  type Rules,
} from "./buffer.ts";

interface RulesJson {
  effective_from: string;
  target: Amount;
  floor: Amount;
  from_buffer_up_to: Amount;
  pay_within_working_days: number;
}

interface ProposalJson {
  id: string;
  rules: RulesJson;
  reason: string;
  status: ProposalStatus;
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  allowed: BufferStep[];
}

interface MovementJson {
  id: string;
  direction: Direction;
  amount: Amount;
  bank_reference: string | null;
  value_date: string | null;
  reason: string;
  status: MovementStatus;
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  allowed: BufferStep[];
}

export interface BufferScreenJson {
  currency: Currency;
  buffer: Amount;
  level: BufferLevel;
  in_force: RulesJson;
  versions: RulesJson[];
  proposals: ProposalJson[];
  finvest_money: Amount;
  movements: MovementJson[];
}

const rules = (json: RulesJson): Rules => ({
  effectiveFrom: json.effective_from,
  target: json.target,
  floor: json.floor,
  fromBufferUpTo: json.from_buffer_up_to,
  payWithinWorkingDays: json.pay_within_working_days,
});

const movement = (json: MovementJson): Movement => ({
  id: json.id,
  direction: json.direction,
  amount: json.amount,
  bankReference: json.bank_reference,
  valueDate: json.value_date,
  reason: json.reason,
  status: json.status,
  proposedBy: json.proposed_by,
  proposedAt: json.proposed_at,
  decidedBy: json.decided_by,
  decidedAt: json.decided_at,
  decisionReason: json.decision_reason,
  allowed: json.allowed,
});

export function toBufferScreen(json: BufferScreenJson): BufferScreen {
  return {
    currency: json.currency,
    buffer: json.buffer,
    level: json.level,
    inForce: rules(json.in_force),
    versions: json.versions.map(rules),
    proposals: json.proposals.map((p) => ({
      id: p.id,
      rules: rules(p.rules),
      reason: p.reason,
      status: p.status,
      proposedBy: p.proposed_by,
      proposedAt: p.proposed_at,
      decidedBy: p.decided_by,
      decidedAt: p.decided_at,
      decisionNote: p.decision_note,
      allowed: p.allowed,
    })),
    finvestMoney: json.finvest_money,
    movements: json.movements.map(movement),
  };
}

const FIELD_CODES = new Set<BufferErrorCode>([
  "effective_date_not_in_future",
  "date_already_taken",
  "nothing_changes",
  "reference_required",
  "already_recorded",
  "value_date_in_future",
  "value_date_in_a_closed_day",
  "day_closed_decline_and_record_again",
  "more_than_paid_in",
  "more_than_the_buffer",
]);

/** The API's code, or the code of its first field problem, as the screen's own. */
export function toBufferError(error: unknown): BufferError {
  if (error instanceof BufferError) return error;
  if (!(error instanceof ApiError)) return new BufferError("unavailable");
  const field = error.fieldCode;
  if (field === "inconsistent") return new BufferError("rules_inconsistent");
  if (field && FIELD_CODES.has(field as BufferErrorCode)) return new BufferError(field as BufferErrorCode);
  switch (error.code) {
    case "not_found":
    case "conflict":
    case "forbidden":
    case "unavailable":
      return new BufferError(error.code);
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case CONFIRM_IT_IS_YOU:
      return new BufferError("not_confirmed");
    case "validation_failed":
      return new BufferError("invalid");
    default:
      return new BufferError("unavailable");
  }
}

export function createHttpBufferApi(http: Http): BufferApi {
  async function call(path: string, key: string, body?: unknown): Promise<BufferScreen> {
    try {
      const json = await http.request<BufferScreenJson>("POST", `/staff/dealing/buffer${path}`, body, { idempotencyKey: key });
      return toBufferScreen(json);
    } catch (error) {
      throw toBufferError(error);
    }
  }

  return {
    async getBuffer(currency) {
      try {
        return toBufferScreen(await http.request<BufferScreenJson>("GET", `/staff/dealing/buffer?currency=${currency}`));
      } catch (error) {
        throw toBufferError(error);
      }
    },
    proposeRules: (key, proposal: NewRules) =>
      call("/rules", key, {
        currency: proposal.target.currency,
        effective_from: proposal.effectiveFrom,
        target_minor: proposal.target.minor,
        floor_minor: proposal.floor.minor,
        from_buffer_up_to_minor: proposal.fromBufferUpTo.minor,
        pay_within_working_days: proposal.payWithinWorkingDays,
        reason: proposal.reason,
      }),
    approveRules: (key, id) => call(`/rules/${encodeURIComponent(id)}/approve`, key),
    sendBackRules: (key, id, note) => call(`/rules/${encodeURIComponent(id)}/send-back`, key, { note }),
    withdrawRules: (key, id) => call(`/rules/${encodeURIComponent(id)}/withdraw`, key),
    proposeMovement: (key, given: NewMovement) =>
      call("/capital", key, {
        direction: given.direction,
        currency: given.amount.currency,
        amount_minor: given.amount.minor,
        reason: given.reason,
        ...(given.direction === "in" ? { bank_reference: given.bankReference, value_date: given.valueDate } : {}),
      }),
    approveMovement: (key, id) => call(`/capital/${encodeURIComponent(id)}/approve`, key),
    declineMovement: (key, id, reason) => call(`/capital/${encodeURIComponent(id)}/decline`, key, { reason }),
  };
}
