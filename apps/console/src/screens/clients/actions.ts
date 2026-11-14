/**
 * Acting for a client from their page, always with two people (`/staff/clients/{id}/actions`,
 * `/staff/clients/actions/{id}/decision`, `/staff/clients/{id}/limits` and
 * `/staff/clients/{id}/withdrawal-options` in backend/app/modules/console/actions_api.py).
 *
 * One member of staff proposes an action with a reason, and a different one approves or rejects
 * it. The proposer may withdraw it. Approving does it at once. Each kind has its own permission:
 *
 * - `pin_reset` (`client_pins.reset`): signs the client out of the phone app, so they log in
 *   again and choose a new PIN. Their web sessions stay.
 * - `withdrawal_limits` (`client_limits.change`): stricter limits for this client alone. They only
 *   ever tighten the shared limits.
 * - `statement` (`client_statements.send`): a statement of the days chosen, put in the app and
 *   emailed to the client's proved address.
 * - `withdrawal` (`client_withdrawals.start`): an early withdrawal the client asked for in their
 *   own help chat in the last seven days, paid only to an approved account of theirs. The owner
 *   decided this on 2026-11-14. The client is told by SMS and email.
 *
 * Refunds are not here: they are the two-person investment corrections on the Investments page.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Currency } from "./api.ts";

export const ACTION_KINDS = ["pin_reset", "withdrawal_limits", "statement", "withdrawal"] as const;
export type ActionKind = (typeof ACTION_KINDS)[number];
export type ActionStatus = "proposed" | "approved" | "rejected" | "withdrawn";
export type ActionDecision = "approved" | "rejected" | "withdrawn";

/** Withdrawal limits in whole shillings. Null is no limit of that sort. */
export interface Limits {
  mobileMoneyPerPayout: number | null;
  mobileMoneyPerDay: number | null;
  bankPerPayout: number | null;
  bankPerDay: number | null;
  reviewAbove: number | null;
}

export const LIMIT_KEYS = [
  "mobileMoneyPerPayout",
  "mobileMoneyPerDay",
  "bankPerPayout",
  "bankPerDay",
  "reviewAbove",
] as const satisfies readonly (keyof Limits)[];

export const LIMIT_LABELS: Record<keyof Limits, string> = {
  mobileMoneyPerPayout: "Mobile money, one payout",
  mobileMoneyPerDay: "Mobile money, one day",
  bankPerPayout: "Bank, one payout",
  bankPerDay: "Bank, one day",
  reviewAbove: "Held for review above",
};

export interface StatementDays {
  /** ISO dates, both included. */
  from: string;
  to: string;
}

export interface WithdrawalChoice {
  investmentId: string;
  destinationId: string;
  messageId: string;
}

export interface ClientAction {
  id: string;
  kind: ActionKind;
  reason: string;
  proposedByName: string;
  proposedAt: string;
  status: ActionStatus;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  limits: Limits | null;
  statement: StatementDays | null;
  withdrawal: WithdrawalChoice | null;
  /** The signed-in person proposed it: they may withdraw it, and someone else decides. */
  yours: boolean;
}

export interface ClientActions {
  /** Newest first. */
  actions: ClientAction[];
  /** The kinds the signed-in person may propose or decide. */
  allowed: ActionKind[];
}

export interface ClientLimits {
  shared: Limits;
  /** Staff's stricter limits for this client, or null when none are set. */
  own: Limits | null;
  /** What a payout today is checked against: the lower of the two for each sort. */
  applied: Limits;
}

export interface WithdrawableInvestment {
  id: string;
  productName: string;
  /** What the client would be paid if asked today, after tax. */
  valueMinor: number;
  currency: Currency;
  maturityDate: string | null;
}

export interface PayableAccount {
  id: string;
  kind: "mobile_money" | "bank";
  network: string | null;
  bankCode: string | null;
  maskedAccount: string;
  holderName: string;
}

export interface ClientRequest {
  id: string;
  body: string;
  createdAt: string;
}

export interface WithdrawalOptions {
  investments: WithdrawableInvestment[];
  /** Approved accounts of the client's own, past their first day's wait. */
  accounts: PayableAccount[];
  /** The client's own help chat messages from the last seven days, newest first. */
  requests: ClientRequest[];
  /** Early withdrawal is switched on. */
  open: boolean;
}

export type Proposal =
  | { kind: "pin_reset"; reason: string }
  | { kind: "withdrawal_limits"; reason: string; limits: Limits }
  | { kind: "statement"; reason: string; statement: StatementDays }
  | { kind: "withdrawal"; reason: string; withdrawal: WithdrawalChoice };

export interface ActionsApi {
  actionsFor(clientId: string): Promise<ClientActions>;
  limitsFor(clientId: string): Promise<ClientLimits>;
  withdrawalOptions(clientId: string): Promise<WithdrawalOptions>;
  propose(clientId: string, proposal: Proposal): Promise<void>;
  decide(actionId: string, decision: ActionDecision, reason: string): Promise<void>;
}

export type ActionErrorCode =
  | "invalid"
  | "not_below_shared"
  | "period"
  | "too_old"
  | "account"
  | "waiting"
  | "cannot_now"
  | "not_active"
  | "closed"
  | "not_found"
  | "no_client"
  | "forbidden"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class ActionError extends Error {
  readonly code: ActionErrorCode;

  constructor(code: ActionErrorCode) {
    super(code);
    this.name = "ActionError";
    this.code = code;
  }
}

const MESSAGES: Record<ActionErrorCode, string> = {
  invalid: "Something in the form is missing or not allowed. Check it and send it again.",
  not_below_shared: "A client's own limit must be lower than the limit every client has. Leave a box empty to keep the shared limit.",
  period: "Choose days that end today or earlier, start before they end, and cover at most a year.",
  too_old: "That message is more than seven days old. Ask the client to write again in their help chat.",
  account: "That payout account cannot be paid now. Choose an approved account of the client's own.",
  waiting: "One of these is already waiting for this client. Decide or withdraw it first.",
  cannot_now: "This can no longer be done as proposed, for example because the investment is no longer running or early withdrawal is off. Nothing changed.",
  not_active: "Staff act only for an open account. Unlock the client first.",
  closed: "This account is closed, so nothing more can be done for it.",
  not_found: "That proposal has already been decided. The page now shows where it stands.",
  no_client: "Finvest could not find this client. Reload the page.",
  forbidden: "You proposed this, or your role cannot do it, so someone else must decide it.",
  not_confirmed: "Nothing was changed, because you did not confirm it is you.",
  not_connected: "Acting for a client is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function actionErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ActionError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new ActionError("not_connected"));

export const notConnectedActionsApi: ActionsApi = {
  actionsFor: notConnected,
  limitsFor: notConnected,
  withdrawalOptions: notConnected,
  propose: notConnected,
  decide: notConnected,
};

// Over HTTP ---------------------------------------------------------------------------------------

interface LimitsJson {
  mobile_money_per_payout: number | null;
  mobile_money_per_day: number | null;
  bank_per_payout: number | null;
  bank_per_day: number | null;
  review_above: number | null;
}

interface ActionJson {
  id: string;
  kind: ActionKind;
  reason: string;
  proposed_by_name: string;
  proposed_at: string;
  status: ActionStatus;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  limits: LimitsJson | null;
  statement: { date_from: string; date_to: string } | null;
  withdrawal: {
    investment_id: string;
    destination_id: string;
    message_id: string;
  } | null;
  yours: boolean;
}

interface OptionsJson {
  investments: {
    id: string;
    product_name: string;
    value_minor: number;
    currency: Currency;
    maturity_date: string | null;
  }[];
  accounts: {
    id: string;
    kind: "mobile_money" | "bank";
    network: string | null;
    bank_code: string | null;
    masked_account: string;
    holder_name: string;
  }[];
  requests: { id: string; body: string; created_at: string }[];
  open: boolean;
}

function fromLimits(json: LimitsJson): Limits {
  return {
    mobileMoneyPerPayout: json.mobile_money_per_payout,
    mobileMoneyPerDay: json.mobile_money_per_day,
    bankPerPayout: json.bank_per_payout,
    bankPerDay: json.bank_per_day,
    reviewAbove: json.review_above,
  };
}

function toLimits(limits: Limits): LimitsJson {
  return {
    mobile_money_per_payout: limits.mobileMoneyPerPayout,
    mobile_money_per_day: limits.mobileMoneyPerDay,
    bank_per_payout: limits.bankPerPayout,
    bank_per_day: limits.bankPerDay,
    review_above: limits.reviewAbove,
  };
}

function toAction(json: ActionJson): ClientAction {
  return {
    id: json.id,
    kind: json.kind,
    reason: json.reason,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    status: json.status,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    limits: json.limits ? fromLimits(json.limits) : null,
    statement: json.statement ? { from: json.statement.date_from, to: json.statement.date_to } : null,
    withdrawal: json.withdrawal
      ? {
          investmentId: json.withdrawal.investment_id,
          destinationId: json.withdrawal.destination_id,
          messageId: json.withdrawal.message_id,
        }
      : null,
    yours: json.yours,
  };
}

function body(proposal: Proposal): Record<string, unknown> {
  switch (proposal.kind) {
    case "pin_reset":
      return { kind: proposal.kind, reason: proposal.reason };
    case "withdrawal_limits":
      return {
        kind: proposal.kind,
        reason: proposal.reason,
        limits: toLimits(proposal.limits),
      };
    case "statement":
      return {
        kind: proposal.kind,
        reason: proposal.reason,
        statement: {
          date_from: proposal.statement.from,
          date_to: proposal.statement.to,
        },
      };
    case "withdrawal":
      return {
        kind: proposal.kind,
        reason: proposal.reason,
        withdrawal: {
          investment_id: proposal.withdrawal.investmentId,
          destination_id: proposal.withdrawal.destinationId,
          message_id: proposal.withdrawal.messageId,
        },
      };
  }
}

/** `deciding` reads a plain conflict as the action no longer being possible, not one waiting. */
export function toActionError(error: unknown, deciding = false): ActionError {
  if (error instanceof ActionError) return error;
  if (!(error instanceof ApiError)) return new ActionError("unavailable");
  const codes = error.fields.map((field) => field.code);
  const paths = error.fields.flatMap((field) => field.field_path.map(String));
  switch (error.code) {
    case "validation_failed":
      if (codes.includes("not_below_shared")) return new ActionError("not_below_shared");
      if (codes.some((code) => code.startsWith("period_"))) return new ActionError("period");
      if (codes.includes("too_old")) return new ActionError("too_old");
      if (paths.includes("destination_id")) return new ActionError("account");
      return new ActionError("invalid");
    case "conflict":
      if (codes.includes("closed")) return new ActionError("closed");
      if (codes.includes("not_active")) return new ActionError("not_active");
      return new ActionError(deciding ? "cannot_now" : "waiting");
    case "not_found":
      return new ActionError(deciding ? "not_found" : "no_client");
    case "forbidden":
      return new ActionError("forbidden");
    case "confirm_it_is_you":
      return new ActionError("not_confirmed");
    default:
      return new ActionError("unavailable");
  }
}

export function createHttpActionsApi(http: Http): ActionsApi {
  const client = (clientId: string, rest: string) => `/staff/clients/${encodeURIComponent(clientId)}/${rest}`;
  async function call<T>(run: () => Promise<T>, deciding = false): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toActionError(error, deciding);
    }
  }
  return {
    actionsFor: (clientId) =>
      call(async () => {
        const json = await http.request<{
          actions: ActionJson[];
          allowed: ActionKind[];
        }>("GET", client(clientId, "actions"));
        return { actions: json.actions.map(toAction), allowed: json.allowed };
      }),
    limitsFor: (clientId) =>
      call(async () => {
        const json = await http.request<{
          shared: LimitsJson;
          own: LimitsJson | null;
          applied: LimitsJson;
        }>("GET", client(clientId, "limits"));
        return {
          shared: fromLimits(json.shared),
          own: json.own ? fromLimits(json.own) : null,
          applied: fromLimits(json.applied),
        };
      }),
    withdrawalOptions: (clientId) =>
      call(async () => {
        const json = await http.request<OptionsJson>("GET", client(clientId, "withdrawal-options"));
        return {
          investments: json.investments.map((item) => ({
            id: item.id,
            productName: item.product_name,
            valueMinor: item.value_minor,
            currency: item.currency,
            maturityDate: item.maturity_date,
          })),
          accounts: json.accounts.map((item) => ({
            id: item.id,
            kind: item.kind,
            network: item.network,
            bankCode: item.bank_code,
            maskedAccount: item.masked_account,
            holderName: item.holder_name,
          })),
          requests: json.requests.map((item) => ({
            id: item.id,
            body: item.body,
            createdAt: item.created_at,
          })),
          open: json.open,
        };
      }),
    propose: (clientId, proposal) =>
      call(async () => {
        await http.request("POST", client(clientId, "actions"), body(proposal));
      }),
    decide: (actionId, decision, reason) =>
      call(async () => {
        await http.request("POST", `/staff/clients/actions/${encodeURIComponent(actionId)}/decision`, { decision, reason });
      }, true),
  };
}
