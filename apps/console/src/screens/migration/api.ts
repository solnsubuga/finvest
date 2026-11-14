/**
 * The console's view of the Finvest 1.0 migration.
 *
 * A stand-in for the generated client in packages/api-client, like AuthApi.
 * The shapes follow backend/app/modules/migration/domain (see docs/migration.md).
 * When the backend publishes its routes, an implementation that calls them
 * replaces the stub in index.tsx and the screen does not change.
 *
 * Counts and amounts travel as whole-number strings in the smallest unit
 * (shillings, cents), so nothing here is ever a floating point number.
 */

export type Severity = "error" | "warning";
export type SheetName = "clients" | "investments";
export type Currency = "UGX" | "USD";

/** One thing wrong with a sheet. It never carries the cell's value. */
export interface Problem {
  sheet: SheetName;
  /** The spreadsheet row. The header is row 1. Null for the sheet as a whole. */
  row: number | null;
  column: string | null;
  code: string;
  severity: Severity;
}

/**
 * A difference accepted with a reason. One member of staff proposes it and a
 * different one approves or rejects it. Only an approved sign-off clears the
 * difference. A rejected one stays, with its reason, until it is proposed again.
 */
export interface SignOff {
  id: string;
  reason: string;
  state: "proposed" | "approved" | "rejected";
  proposedBy: string;
  /** Who approved or rejected it. */
  decidedBy: string | null;
  /** Why it was rejected. An approval may leave it out. */
  decisionReason: string | null;
  /** True when the signed-in person proposed it, so they cannot approve it. */
  mine: boolean;
}

export type Figure =
  | "clients"
  | "verified_identities"
  | "running_investments"
  | "pending_orders"
  | "invested"
  | "interest_before_tax";

export interface TotalLine {
  key: string;
  figure: Figure;
  /** Null for a count. */
  currency: Currency | null;
  /** What Finvest 1.0 reports. Null until finance has entered it. */
  old: string | null;
  /** What the latest trial import holds. */
  new: string;
  signedOff: SignOff | null;
}

/** A running investment whose interest or tax rate is not what the new system works out. */
export interface Difference {
  key: string;
  kind: "interest" | "tax_rate";
  /** The investments sheet's row. Null when the backend does not say. */
  row: number | null;
  legacyInvestmentRef: string;
  /** Null when the backend does not say: the interest is then shown as a whole number in the smallest unit. */
  currency: Currency | null;
  /** Interest in the smallest unit, or a tax rate in basis points. */
  old: string;
  new: string;
  signedOff: SignOff | null;
}

/** One whole-number string per currency, in the smallest unit. A currency left out is none. */
export type Amounts = Partial<Record<Currency, string>>;

/** What finance says Finvest 1.0 holds on the "as of" day. */
export interface TotalsInput {
  asOf: string;
  clients: string;
  verifiedIdentities: string;
  runningInvestments: string;
  pendingOrders: string;
  invested: Amounts;
  interestBeforeTax: Amounts;
}

/**
 * The latest entry of the Finvest 1.0 totals. It is compared with the trial
 * only once a second member of staff has confirmed it.
 */
export interface EnteredTotals extends TotalsInput {
  id: string;
  enteredBy: string;
  enteredAt: string;
  confirmedBy: string | null;
  /** True when the signed-in person entered it, so they cannot confirm it. */
  mine: boolean;
}

export interface Trial {
  /** The day the old system's figures were taken, as YYYY-MM-DD. */
  asOf: string;
  ranAt: string;
  ranBy: string;
  errorCount: number;
  warningCount: number;
  /** The first problems, errors first. `errorCount + warningCount` can be larger. */
  problems: readonly Problem[];
}

/** Null on the screen until the switch has run and invitations have gone out. */
export interface ClaimProgress {
  invited: number;
  claimedAndConfirmed: number;
  saidSomethingIsWrong: number;
  notYetClaimed: number;
  cannotBeInvited: number;
}

export type Blocker =
  | "no_trial"
  | "import_has_errors"
  | "totals_not_entered"
  | "totals_not_confirmed"
  | "difference_not_signed_off";

/**
 * What the signed-in person may do, as the backend's overview says. `enter_totals` covers
 * entering and confirming the Finvest 1.0 totals: both routes require `migration.sign_off`,
 * the same permission that gives `sign_off` (backend/app/modules/migration/api.py).
 */
export type Allowed =
  | "run_trial"
  | "enter_totals"
  | "sign_off"
  | "propose_switch"
  | "decide_switch"
  | "propose_resolution"
  | "decide_resolution";

/**
 * A client the worker could not carry over at the switch, and where resolving them stands.
 * `retrying` is an approved retry the worker has not finished. A client carried over or closed
 * leaves the list.
 */
export type NotCarriedState = "noted" | "retry_proposed" | "close_proposed" | "retrying";
export type ResolutionAction = "retry" | "close";

export interface NotCarried {
  legacyClientRef: string;
  /** Why the worker refused them. Never anything from the sheets. */
  code: string;
  /** Which time the worker tried them. A retry refused again is the next attempt. */
  attempt: number;
  notedAt: string;
  state: NotCarriedState;
  /** The proposal awaiting a decision, or the approved retry. */
  resolutionId: string | null;
  proposedBy: string | null;
  /** Why it was proposed. */
  reason: string | null;
  /** True when the signed-in person proposed it, so they cannot decide it. */
  mine: boolean;
}

/**
 * The newest proposal to switch. One member of staff proposes it with the sheets of the trial
 * that cleared every check, and a different one approves it, which creates every account.
 */
export interface Switch {
  id: string;
  asOf: string;
  proposedBy: string;
  proposedAt: string;
  state: "proposed" | "approved" | "rejected";
  decidedBy: string | null;
  decisionReason: string | null;
  /** Accounts the approval created. */
  clientsCarried: number;
  notCarried: readonly NotCarried[];
  /** True when the signed-in person proposed it. */
  mine: boolean;
}

export interface Overview {
  trialsRun: number;
  trial: Trial | null;
  /** The latest entry for the trial's "as of" day, confirmed or not. */
  totals: EnteredTotals | null;
  lines: readonly TotalLine[];
  differences: readonly Difference[];
  claims: ClaimProgress | null;
  /** Null until someone proposes the switch. */
  switch: Switch | null;
  /** Why the switch cannot be scheduled yet. Empty when it can. */
  blockers: readonly Blocker[];
  /** What the signed-in person may do. The backend decides. */
  allowed: readonly Allowed[];
}

export interface TrialInput {
  clients: File;
  investments: File;
  asOf: string;
}

/** The two sheets of the latest trial, exactly as that trial read them. */
export interface SwitchInput {
  clients: File;
  investments: File;
}

export interface MigrationApi {
  getOverview(): Promise<Overview>;
  /** Checks both sheets and saves nothing but the result. */
  runTrial(input: TrialInput): Promise<Overview>;
  /** A new entry replaces the one before it, and waits for a second person. */
  enterTotals(input: TotalsInput): Promise<Overview>;
  confirmTotals(totalsId: string): Promise<Overview>;
  /** The figures are the ones that stand now. The backend reads them itself. */
  proposeSignOff(key: string, reason: string): Promise<Overview>;
  /** Approve, or reject with a reason. Never the proposer. */
  decideSignOff(signOffId: string, approve: boolean, reason?: string): Promise<Overview>;
  /** Asks a second person to approve the switch. Nothing is created yet. */
  proposeSwitch(input: SwitchInput): Promise<Overview>;
  /** Approve, which creates every account, or reject with a reason. Never the proposer. */
  decideSwitch(switchId: string, approve: boolean, reason?: string): Promise<Overview>;
  /** Asks a second person to approve retrying or closing a client the switch could not carry over. */
  proposeResolution(switchId: string, legacyClientRef: string, action: ResolutionAction, reason: string): Promise<Overview>;
  decideResolution(resolutionId: string, approve: boolean, reason?: string): Promise<Overview>;
}

/** The largest sheet the backend accepts, in bytes. */
export const MAX_SHEET_BYTES = 40_000_000;

export type MigrationErrorCode =
  | "forbidden"
  | "conflict"
  | "invalid"
  | "file_too_large"
  | "not_the_trial_sheet"
  | "phone_missing"
  | "phone_taken"
  | "not_confirmed"
  | "stale"
  | "not_connected"
  | "unavailable";

export class MigrationError extends Error {
  readonly code: MigrationErrorCode;

  constructor(code: MigrationErrorCode) {
    super(code);
    this.name = "MigrationError";
    this.code = code;
  }
}

const MESSAGES: Record<MigrationErrorCode, string> = {
  forbidden: "You are not allowed to do this. A sign-off or an entry of totals is checked by someone else.",
  conflict: "Someone changed this while you were looking. The screen now shows the latest figures.",
  invalid: "Finvest could not accept that. Check the figures and the reason, and try again.",
  not_confirmed: "Nothing was changed, because you did not confirm it is you.",
  stale: "That was saved, but the screen could not load the latest figures. It has been reloaded.",
  file_too_large: "One of the files is too large. A sheet holds at most 100,000 rows.",
  not_the_trial_sheet: "These are not the sheets the latest trial checked. Choose the same two files, unchanged.",
  phone_missing: "A client in the sheets has no phone number. Every client claims their account by phone, so add one and run a trial again.",
  phone_taken: "A phone number in the sheets already has a Finvest 2.0 account, so no account was created. Look into it, then approve again.",
  not_connected: "Migration is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function migrationErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof MigrationError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new MigrationError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedMigrationApi: MigrationApi = {
  getOverview: notConnected,
  runTrial: notConnected,
  enterTotals: notConnected,
  confirmTotals: notConnected,
  proposeSignOff: notConnected,
  decideSignOff: notConnected,
  proposeSwitch: notConnected,
  decideSwitch: notConnected,
  proposeResolution: notConnected,
  decideResolution: notConnected,
};
