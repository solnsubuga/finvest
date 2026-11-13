import { MigrationError, type MigrationApi, type Overview, type Problem, type TotalLine, type TrialInput } from "./api.ts";

/**
 * Sample migration figures for `pnpm dev` and previews. Not real clients and
 * not connected to Finvest. A trial here reads nothing from the chosen files:
 * the first one reports sample problems and the next one comes back clean.
 */

const PROBLEMS: readonly Problem[] = [
  { sheet: "clients", row: 214, column: "phone", code: "duplicate_phone", severity: "error" },
  { sheet: "clients", row: 1377, column: "phone", code: "duplicate_phone", severity: "error" },
  { sheet: "clients", row: 902, column: "date_of_birth", code: "date_invalid", severity: "error" },
  { sheet: "investments", row: 58, column: "legacy_client_ref", code: "client_unknown", severity: "error" },
  { sheet: "investments", row: 1420, column: "maturity_date", code: "maturity_differs", severity: "error" },
  { sheet: "investments", row: 2981, column: "principal", code: "amount_invalid", severity: "error" },
  { sheet: "clients", row: 77, column: "phone", code: "phone_missing", severity: "warning" },
  { sheet: "clients", row: 340, column: "id_expires_on", code: "id_expired", severity: "warning" },
  { sheet: "clients", row: 1105, column: "id_verified", code: "id_not_verified", severity: "warning" },
  { sheet: "investments", row: 611, column: "maturity_date", code: "past_maturity", severity: "warning" },
];

const line = (key: string, figure: TotalLine["figure"], currency: TotalLine["currency"], old: string, next: string): TotalLine => ({
  key,
  figure,
  currency,
  old,
  new: next,
  signedOff: null,
});

function overview(trialsRun: number, asOf: string, clean: boolean): Overview {
  const problems = clean ? PROBLEMS.filter((problem) => problem.severity === "warning") : PROBLEMS;
  const errors = problems.filter((problem) => problem.severity === "error").length;
  return {
    trialsRun,
    trial: {
      asOf,
      ranAt: "2026-11-13T15:40:00Z",
      ranBy: "Sarah Operations",
      errorCount: errors,
      warningCount: clean ? 108 : 112,
      problems,
    },
    lines: [
      line("total:clients", "clients", null, "1912", clean ? "1912" : "1909"),
      line("total:verified_identities", "verified_identities", null, "1804", clean ? "1804" : "1802"),
      line("total:running_investments", "running_investments", null, "3207", clean ? "3204" : "3201"),
      line("total:pending_orders", "pending_orders", null, "14", "14"),
      line("total:invested:UGX", "invested", "UGX", "18240000000", clean ? "18240000000" : "18212500000"),
      line("total:interest_before_tax:UGX", "interest_before_tax", "UGX", "420101240", "420100000"),
      {
        ...line("total:invested:USD", "invested", "USD", "25450000", "25450000"),
      },
      {
        ...line("total:interest_before_tax:USD", "interest_before_tax", "USD", "61275", "61270"),
        signedOff: { reason: "Finvest 1.0 rounded each day's dollar interest up.", signedBy: "Grace Finance" },
      },
    ],
    differences: [
      { key: "investment:INV-20418", kind: "interest", row: 418, legacyInvestmentRef: "INV-20418", currency: "UGX", old: "184620", new: "184000", signedOff: null },
      { key: "investment:INV-20977", kind: "interest", row: 977, legacyInvestmentRef: "INV-20977", currency: "UGX", old: "96120", new: "95500", signedOff: null },
      { key: "tax_rate:INV-19034", kind: "tax_rate", row: 34, legacyInvestmentRef: "INV-19034", currency: "UGX", old: "1000", new: "1500", signedOff: null },
      {
        key: "investment:USD-0091",
        kind: "interest",
        row: 3190,
        legacyInvestmentRef: "USD-0091",
        currency: "USD",
        old: "4105",
        new: "4100",
        signedOff: { reason: "Finvest 1.0 rounded each day's dollar interest up.", signedBy: "Grace Finance" },
      },
    ],
    claims: null,
    blockers: clean ? ["difference_not_signed_off"] : ["import_has_errors", "difference_not_signed_off"],
    allowed: ["run_trial"],
  };
}

const EMPTY: Overview = {
  trialsRun: 0,
  trial: null,
  lines: [],
  differences: [],
  claims: null,
  blockers: ["no_trial"],
  allowed: ["run_trial"],
};

/** `start` picks what the screen opens with: nothing imported yet, or a first trial with problems. */
export function createStubMigrationApi(delayMs = 150, start: "empty" | "imported" = "imported"): MigrationApi {
  let current = start === "empty" ? EMPTY : overview(1, "2026-11-12", false);
  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new MigrationError("unavailable"));
        }
      }, delayMs),
    );
  return {
    getOverview: () => wait(() => current),
    runTrial: (input: TrialInput) =>
      wait(() => {
        current = overview(current.trialsRun + 1, input.asOf, current.trialsRun > 0);
        return current;
      }),
  };
}
