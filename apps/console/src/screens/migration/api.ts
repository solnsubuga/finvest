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

export interface SignOff {
  reason: string;
  signedBy: string;
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
  row: number;
  legacyInvestmentRef: string;
  currency: Currency;
  /** Interest in the smallest unit, or a tax rate in basis points. */
  old: string;
  new: string;
  signedOff: SignOff | null;
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

export type Blocker = "no_trial" | "import_has_errors" | "totals_not_entered" | "difference_not_signed_off";

export interface Overview {
  trialsRun: number;
  trial: Trial | null;
  lines: readonly TotalLine[];
  differences: readonly Difference[];
  claims: ClaimProgress | null;
  /** Why the switch cannot be scheduled yet. Empty when it can. */
  blockers: readonly Blocker[];
  /** What the signed-in person may do. The backend decides. */
  allowed: readonly "run_trial"[];
}

export interface TrialInput {
  clients: File;
  investments: File;
  asOf: string;
}

export interface MigrationApi {
  getOverview(): Promise<Overview>;
  /** Checks both sheets and saves nothing but the result. */
  runTrial(input: TrialInput): Promise<Overview>;
}

export type MigrationErrorCode = "forbidden" | "file_too_large" | "not_connected" | "unavailable";

export class MigrationError extends Error {
  readonly code: MigrationErrorCode;

  constructor(code: MigrationErrorCode) {
    super(code);
    this.name = "MigrationError";
    this.code = code;
  }
}

const MESSAGES: Record<MigrationErrorCode, string> = {
  forbidden: "You are not allowed to run a trial import.",
  file_too_large: "One of the files is too large. A sheet holds at most 100,000 rows.",
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
};
