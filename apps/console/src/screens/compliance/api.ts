/**
 * The console's view of compliance: sanctions and politically exposed person
 * screening, transaction monitoring, and the cases staff close with two people.
 *
 * The shapes follow `/staff/compliance` in backend/app/modules/screening/api.py
 * (see docs/screening.md). One person proposes how a case closes, a different
 * person approves it or sends it back, and only the proposer withdraws it.
 * `http.ts` calls the routes; `stub.ts` is the sample for `pnpm dev` and previews.
 */

import type { Amount } from "../products/api.ts";

export type CaseSource = "screening" | "monitoring";
export type CaseStatus = "open" | "closed";
export type CaseOutcome = "not_a_match" | "pep_accepted" | "sanctioned" | "no_concern" | "report_to_fia";
export type ProposalStatus = "proposed" | "approved" | "sent_back" | "withdrawn";
export type StandingOutcome = "clear" | "possible_match" | "confirmed_match" | "unavailable" | "pending" | "not_run";
export type RunStatus = "pending" | "clear" | "possible_match" | "unavailable" | "not_run" | "no_subject";
export type RunReason = "onboarding" | "rescreen" | "payout" | "staff";
export type MatchCategory = "sanctions" | "pep" | "adverse_media" | "other";

export interface Client {
  id: string;
  /** Null when the client has no identity check to name them. */
  name: string | null;
  accountNumber: string | null;
}

export interface Proposal {
  id: string;
  outcome: CaseOutcome;
  reason: string;
  proposedBy: string;
  proposedByName: string;
  proposedAt: string;
  status: ProposalStatus;
  decidedBy: string | null;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
}

export interface Case {
  id: string;
  client: Client;
  source: CaseSource;
  /** `possible_match` for screening, the rule for monitoring, such as `large_deposit`. */
  code: string;
  openedAt: string;
  status: CaseStatus;
  outcome: CaseOutcome | null;
  closedAt: string | null;
  /** The proposal waiting for a second person, if any. */
  waiting: Proposal | null;
  /** For a monitoring case, the payment that raised it, or for split deposits their total. */
  amount: Amount | null;
}

export interface Run {
  id: string;
  reason: RunReason;
  requestedAt: string;
  status: RunStatus;
  finishedAt: string | null;
  provider: string | null;
  failureCode: string | null;
}

export interface Match {
  entityId: string;
  name: string;
  category: MatchCategory;
  /** How strongly the provider thinks it is the client, as a whole percentage. */
  strength: number;
  sources: readonly string[];
  /** Already ruled out or accepted for this client on an earlier case. */
  known: boolean;
}

export interface Standing {
  outcome: StandingOutcome;
  screenedAt: string | null;
  caseId: string | null;
  pep: boolean;
}

export interface CaseDetail {
  case: Case;
  run: Run | null;
  matches: readonly Match[];
  /** For monitoring: what the rule saw, as IDs and whole numbers. */
  detail: Readonly<Record<string, unknown>>;
  proposals: readonly Proposal[];
  standing: Standing | null;
  /** The outcomes that fit this case. */
  outcomes: readonly CaseOutcome[];
  canPropose: boolean;
  canDecide: boolean;
  /** The reader made the waiting proposal, so they may only withdraw it. */
  yours: boolean;
}

export interface Summary {
  screeningOn: boolean;
  provider: string;
  openCases: number;
  openScreeningCases: number;
  openMonitoringCases: number;
  waitingForSecondPerson: number;
  screenedLast30Days: number;
  unavailableLast30Days: number;
  notRunLast30Days: number;
  pending: number;
  lastScreenedAt: string | null;
  payoutsWaiting: number;
  /** Cases closed since 1 January with a report owed to the Financial Intelligence Authority. */
  reportsToFiaThisYear: number;
}

/** The monitoring rules as they run, read from the backend's rules. Amounts in the smallest unit. */
export interface Rules {
  currency: "UGX" | "USD";
  largeDepositMinor: number;
  splitDepositsDays: number;
  manyDepositsCount: number;
  manyDepositsHours: number;
  unusualMultiple: number;
  unusualFloorMinor: number;
  unusualHistory: number;
  unusualLookbackDays: number;
  quickWithdrawalDays: number;
  quickWithdrawalShare: readonly [number, number];
  quickWithdrawalFloorMinor: number;
  rescreenEveryDays: number;
}

export interface RegisterFile {
  file: Blob;
  filename: string;
}

export interface WaitingPayout {
  id: string;
  client: Client;
  kind: "mobile_money" | "bank";
  amount: Amount;
  createdAt: string;
  standing: Standing | null;
}

export interface CaseFilter {
  status: CaseStatus;
  source: CaseSource | null;
}

export interface ComplianceApi {
  summary(): Promise<Summary>;
  /** Open ones oldest first, closed ones newest first. */
  listCases(filter: CaseFilter): Promise<readonly Case[]>;
  getCase(caseId: string): Promise<CaseDetail>;
  waitingPayouts(): Promise<readonly WaitingPayout[]>;
  rules(): Promise<Rules>;
  /** Every case as a CSV file. The export is on the audit trail. */
  exportRegister(): Promise<RegisterFile>;
  propose(caseId: string, outcome: CaseOutcome, reason: string): Promise<CaseDetail>;
  approve(proposalId: string): Promise<CaseDetail>;
  sendBack(proposalId: string, note: string): Promise<CaseDetail>;
  withdraw(proposalId: string): Promise<CaseDetail>;
}

export type ComplianceErrorCode =
  | "invalid"
  | "proposal_waiting"
  | "case_closed"
  | "outcome_does_not_fit_case"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class ComplianceError extends Error {
  constructor(readonly code: ComplianceErrorCode) {
    super(code);
    this.name = "ComplianceError";
  }
}

const MESSAGES: Record<ComplianceErrorCode, string> = {
  invalid: "Finvest refused this. Write a reason of at most 1,000 characters and try again.",
  proposal_waiting: "Someone already proposed how this case closes. Here is the case as it now stands.",
  case_closed: "This case is already closed. Here is the case as it now stands.",
  outcome_does_not_fit_case: "That outcome does not fit this kind of case.",
  same_person: "Someone else has to decide this. You proposed it, so you can only withdraw it.",
  forbidden: "You are not allowed to do this. Ask an admin.",
  not_found: "Finvest could not find that case or proposal.",
  not_connected: "Compliance is not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function complianceErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ComplianceError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new ComplianceError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedComplianceApi: ComplianceApi = {
  summary: notConnected,
  listCases: notConnected,
  getCase: notConnected,
  waitingPayouts: notConnected,
  rules: notConnected,
  exportRegister: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
