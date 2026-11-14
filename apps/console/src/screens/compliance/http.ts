/**
 * Compliance over the real API: `/staff/compliance` in
 * backend/app/modules/screening/api.py (see docs/screening.md). The routes name
 * clients and staff themselves, so no directory lookup is needed.
 */

import type {
  CaseDetailOut,
  CaseOut,
  ClientOut,
  MatchOut,
  MonitoringRulesOut,
  RunOut,
  CaseProposalOut,
  StandingOut,
  SummaryOut,
  WaitingPayoutOut,
} from "@finvest/api-client/staff";
import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import {
  ComplianceError,
  type Case,
  type CaseDetail,
  type Client,
  type ComplianceApi,
  type Match,
  type Proposal,
  type Rules,
  type Run,
  type Standing,
  type Summary,
  type WaitingPayout,
} from "./api.ts";

const toClient = (json: ClientOut): Client => ({ id: json.id, name: json.name, accountNumber: json.account_number });

export function toProposal(json: CaseProposalOut): Proposal {
  return {
    id: json.id,
    outcome: json.outcome,
    reason: json.reason,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    status: json.status,
    decidedBy: json.decided_by,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

export function toCase(json: CaseOut): Case {
  return {
    id: json.id,
    client: toClient(json.client),
    source: json.source,
    code: json.code,
    openedAt: json.opened_at,
    status: json.status,
    outcome: json.outcome,
    closedAt: json.closed_at,
    waiting: json.waiting ? toProposal(json.waiting) : null,
    amount: json.amount ? { minor: json.amount.minor, currency: json.amount.currency } : null,
  };
}

const toRun = (json: RunOut): Run => ({
  id: json.id,
  reason: json.reason,
  requestedAt: json.requested_at,
  status: json.status,
  finishedAt: json.finished_at,
  provider: json.provider,
  failureCode: json.failure_code,
});

const toMatch = (json: MatchOut): Match => ({
  entityId: json.entity_id,
  name: json.name,
  category: json.category,
  strength: json.strength,
  sources: json.sources,
  known: json.known,
});

const toStanding = (json: StandingOut | null): Standing | null =>
  json ? { outcome: json.outcome, screenedAt: json.screened_at, caseId: json.case_id, pep: json.pep } : null;

export function toDetail(json: CaseDetailOut): CaseDetail {
  return {
    case: toCase(json.case),
    run: json.run ? toRun(json.run) : null,
    matches: json.matches.map(toMatch),
    detail: json.detail,
    proposals: json.proposals.map(toProposal),
    standing: toStanding(json.standing),
    outcomes: json.outcomes,
    canPropose: json.can_propose,
    canDecide: json.can_decide,
    yours: json.yours,
  };
}

const toSummary = (json: SummaryOut): Summary => ({
  screeningOn: json.screening_on,
  provider: json.provider,
  openCases: json.open_cases,
  openScreeningCases: json.open_screening_cases,
  openMonitoringCases: json.open_monitoring_cases,
  waitingForSecondPerson: json.waiting_for_second_person,
  screenedLast30Days: json.screened_last_30_days,
  unavailableLast30Days: json.unavailable_last_30_days,
  notRunLast30Days: json.not_run_last_30_days,
  pending: json.pending,
  lastScreenedAt: json.last_screened_at,
  payoutsWaiting: json.payouts_waiting,
  reportsToFiaThisYear: json.reports_to_fia_this_year,
});

export const toRules = (json: MonitoringRulesOut): Rules => ({
  currency: json.currency,
  largeDepositMinor: json.large_deposit_minor,
  splitDepositsDays: json.split_deposits_days,
  manyDepositsCount: json.many_deposits_count,
  manyDepositsHours: json.many_deposits_hours,
  unusualMultiple: json.unusual_multiple,
  unusualFloorMinor: json.unusual_floor_minor,
  unusualHistory: json.unusual_history,
  unusualLookbackDays: json.unusual_lookback_days,
  quickWithdrawalDays: json.quick_withdrawal_days,
  quickWithdrawalShare: [json.quick_withdrawal_share_numerator, json.quick_withdrawal_share_denominator],
  quickWithdrawalFloorMinor: json.quick_withdrawal_floor_minor,
  rescreenEveryDays: json.rescreen_every_days,
});

/** "finvest-compliance-register-2026-11-14.csv", named for the Kampala day. */
export function registerFilename(now: Date = new Date()): string {
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now);
  return `finvest-compliance-register-${day}.csv`;
}

const toWaitingPayout = (json: WaitingPayoutOut): WaitingPayout => ({
  id: json.id,
  client: toClient(json.client),
  kind: json.kind,
  amount: { minor: json.amount.minor, currency: json.amount.currency },
  createdAt: json.created_at,
  standing: toStanding(json.standing),
});

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toComplianceError(error: unknown, deciding = false): ComplianceError {
  if (error instanceof ComplianceError) return error;
  if (!(error instanceof ApiError)) return new ComplianceError("unavailable");
  const field = error.fieldCode;
  switch (error.code) {
    case "validation_failed":
      return new ComplianceError(field === "outcome_does_not_fit_case" ? "outcome_does_not_fit_case" : "invalid");
    case "conflict":
      return new ComplianceError(field === "proposal_waiting" ? "proposal_waiting" : "case_closed");
    case CONFIRM_IT_IS_YOU:
      return new ComplianceError("not_confirmed");
    case "forbidden":
      return new ComplianceError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new ComplianceError("not_found");
    default:
      return new ComplianceError("unavailable");
  }
}

const PATH = "/staff/compliance";

export function createHttpComplianceApi(http: Http): ComplianceApi {
  async function read<T, R>(path: string, map: (json: T) => R): Promise<R> {
    try {
      return map(await http.request<T>("GET", `${PATH}${path}`));
    } catch (error) {
      throw toComplianceError(error);
    }
  }
  async function command(path: string, body: unknown, deciding: boolean): Promise<CaseDetail> {
    try {
      return toDetail(await http.request<CaseDetailOut>("POST", `${PATH}${path}`, body));
    } catch (error) {
      throw toComplianceError(error, deciding);
    }
  }
  const proposal = (id: string, step: string) => `/proposals/${encodeURIComponent(id)}/${step}`;

  return {
    summary: () => read("/summary", toSummary),
    listCases: (filter) => {
      const query = new URLSearchParams({ status: filter.status });
      if (filter.source) query.set("source", filter.source);
      return read(`/cases?${query.toString()}`, (json: CaseOut[]) => json.map(toCase));
    },
    getCase: (id) => read(`/cases/${encodeURIComponent(id)}`, toDetail),
    waitingPayouts: () => read("/payouts", (json: WaitingPayoutOut[]) => json.map(toWaitingPayout)),
    rules: () => read("/rules", toRules),
    exportRegister: async () => {
      try {
        // A POST: every export is recorded on the audit trail.
        return { file: await http.download(`${PATH}/cases.csv`, { method: "POST" }), filename: registerFilename() };
      } catch (error) {
        throw toComplianceError(error);
      }
    },
    propose: (id, outcome, reason) => command(`/cases/${encodeURIComponent(id)}/proposals`, { outcome, reason }, false),
    approve: (id) => command(proposal(id, "approve"), undefined, true),
    sendBack: (id, note) => command(proposal(id, "send-back"), { note }, true),
    withdraw: (id) => command(proposal(id, "withdraw"), undefined, false),
  };
}
