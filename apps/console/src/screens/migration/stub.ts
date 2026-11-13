import {
  MigrationError,
  type Allowed,
  type Amounts,
  type Blocker,
  type Difference,
  type EnteredTotals,
  type MigrationApi,
  type Overview,
  type Problem,
  type SignOff,
  type TotalLine,
  type TotalsInput,
  type TrialInput,
} from "./api.ts";

/**
 * Sample migration figures for `pnpm dev` and previews. Not real clients and
 * not connected to Finvest. A trial here reads nothing from the chosen files:
 * the first one reports sample problems and the next one comes back clean.
 *
 * The signed-in person is "you". Grace Finance and Daniel Admin stand in for
 * colleagues, so a sample proposal can be approved and your own cannot.
 */

const YOU = "You";

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

const ROUNDING = "Finvest 1.0 rounded each day's dollar interest up.";

const approvedBy = (id: string, reason: string): SignOff => ({
  id,
  reason,
  state: "approved",
  proposedBy: "Grace Finance",
  approvedBy: "Daniel Admin",
  mine: false,
});

/** What each trial imports, by line key. */
function newTotals(clean: boolean): Record<string, string> {
  return {
    "total:clients": clean ? "1912" : "1909",
    "total:verified_identities": clean ? "1804" : "1802",
    "total:running_investments": clean ? "3204" : "3201",
    "total:pending_orders": "14",
    "total:invested:UGX": clean ? "18240000000" : "18212500000",
    "total:interest_before_tax:UGX": "420100000",
    "total:invested:USD": "25450000",
    "total:interest_before_tax:USD": "61270",
  };
}

const LINES: readonly Pick<TotalLine, "key" | "figure" | "currency">[] = [
  { key: "total:clients", figure: "clients", currency: null },
  { key: "total:verified_identities", figure: "verified_identities", currency: null },
  { key: "total:running_investments", figure: "running_investments", currency: null },
  { key: "total:pending_orders", figure: "pending_orders", currency: null },
  { key: "total:invested:UGX", figure: "invested", currency: "UGX" },
  { key: "total:interest_before_tax:UGX", figure: "interest_before_tax", currency: "UGX" },
  { key: "total:invested:USD", figure: "invested", currency: "USD" },
  { key: "total:interest_before_tax:USD", figure: "interest_before_tax", currency: "USD" },
];

function oldFigure(totals: TotalsInput, line: Pick<TotalLine, "figure" | "currency">): string {
  const amounts = (values: Amounts) => (line.currency ? (values[line.currency] ?? "0") : "0");
  switch (line.figure) {
    case "clients":
      return totals.clients;
    case "verified_identities":
      return totals.verifiedIdentities;
    case "running_investments":
      return totals.runningInvestments;
    case "pending_orders":
      return totals.pendingOrders;
    case "invested":
      return amounts(totals.invested);
    case "interest_before_tax":
      return amounts(totals.interestBeforeTax);
  }
}

const SAMPLE_TOTALS: EnteredTotals = {
  id: "totals-sample",
  asOf: "2026-11-12",
  clients: "1912",
  verifiedIdentities: "1804",
  runningInvestments: "3207",
  pendingOrders: "14",
  invested: { UGX: "18240000000", USD: "25450000" },
  interestBeforeTax: { UGX: "420101240", USD: "61275" },
  enteredBy: "Grace Finance",
  enteredAt: "2026-11-13T14:05:00Z",
  confirmedBy: "Daniel Admin",
  mine: false,
};

const DIFFERENCES: readonly Difference[] = [
  { key: "investment:INV-20418", kind: "interest", row: 418, legacyInvestmentRef: "INV-20418", currency: "UGX", old: "184620", new: "184000", signedOff: null },
  {
    key: "investment:INV-20977",
    kind: "interest",
    row: 977,
    legacyInvestmentRef: "INV-20977",
    currency: "UGX",
    old: "96120",
    new: "95500",
    signedOff: { id: "sign-off-20977", reason: "Finvest 1.0 counted the start date as a day earned.", state: "proposed", proposedBy: "Grace Finance", approvedBy: null, mine: false },
  },
  { key: "tax_rate:INV-19034", kind: "tax_rate", row: 34, legacyInvestmentRef: "INV-19034", currency: "UGX", old: "1000", new: "1500", signedOff: null },
  { key: "investment:USD-0091", kind: "interest", row: 3190, legacyInvestmentRef: "USD-0091", currency: "USD", old: "4105", new: "4100", signedOff: approvedBy("sign-off-0091", ROUNDING) },
];

interface State {
  trialsRun: number;
  asOf: string;
  clean: boolean;
  totals: EnteredTotals | null;
  differences: Difference[];
  // Sign-offs on total lines, by key.
  lineSignOffs: Record<string, SignOff>;
  allowed: readonly Allowed[];
}

function build(state: State): Overview {
  if (state.trialsRun === 0) {
    return { trialsRun: 0, trial: null, totals: null, lines: [], differences: [], claims: null, blockers: ["no_trial"], allowed: state.allowed };
  }
  const problems = state.clean ? PROBLEMS.filter((problem) => problem.severity === "warning") : PROBLEMS;
  const errorCount = problems.filter((problem) => problem.severity === "error").length;
  const imported = newTotals(state.clean);
  const confirmed = state.totals?.confirmedBy ? state.totals : null;
  const lines: TotalLine[] = LINES.map((line) => {
    const old = confirmed ? oldFigure(confirmed, line) : null;
    const differs = old !== null && BigInt(old) !== BigInt(imported[line.key]!);
    return { ...line, old, new: imported[line.key]!, signedOff: differs ? (state.lineSignOffs[line.key] ?? null) : null };
  });
  const open = [...lines.filter((line) => line.old !== null && BigInt(line.old) !== BigInt(line.new)), ...state.differences].some(
    (item) => item.signedOff?.state !== "approved",
  );
  const blockers: Blocker[] = [];
  if (errorCount > 0) blockers.push("import_has_errors");
  if (!state.totals) blockers.push("totals_not_entered");
  else if (!confirmed) blockers.push("totals_not_confirmed");
  if (open) blockers.push("difference_not_signed_off");
  return {
    trialsRun: state.trialsRun,
    trial: { asOf: state.asOf, ranAt: "2026-11-13T15:40:00Z", ranBy: "Sarah Operations", errorCount, warningCount: state.clean ? 108 : 112, problems },
    totals: state.totals,
    lines,
    differences: state.differences,
    claims: null,
    blockers,
    allowed: state.allowed,
  };
}

let counter = 0;
const nextId = (prefix: string) => `${prefix}-${++counter}`;

/**
 * `start` picks what the screen opens with: nothing imported yet, a first trial
 * with problems, or that trial with Finvest 1.0 totals a colleague entered and
 * nobody has confirmed. `allowed` is everything by default, like an admin.
 */
export function createStubMigrationApi(delayMs = 150, start: "empty" | "imported" | "unconfirmed" = "imported", allowed: readonly Allowed[] = ["run_trial", "enter_totals", "sign_off"]): MigrationApi {
  const state: State =
    start === "empty"
      ? { trialsRun: 0, asOf: "2026-11-12", clean: false, totals: null, differences: [], lineSignOffs: {}, allowed }
      : {
          trialsRun: 1,
          asOf: "2026-11-12",
          clean: false,
          totals: start === "unconfirmed" ? { ...SAMPLE_TOTALS, confirmedBy: null } : SAMPLE_TOTALS,
          differences: DIFFERENCES.map((item) => ({ ...item })),
          lineSignOffs: { "total:interest_before_tax:USD": approvedBy("sign-off-usd", ROUNDING) },
          allowed,
        };

  const wait = (change: () => void): Promise<Overview> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          change();
          resolve(build(state));
        } catch (error) {
          reject(error instanceof Error ? error : new MigrationError("unavailable"));
        }
      }, delayMs),
    );

  function may(action: Allowed) {
    if (!state.allowed.includes(action)) throw new MigrationError("forbidden");
  }

  function signOffFor(key: string): SignOff | null {
    return state.lineSignOffs[key] ?? state.differences.find((item) => item.key === key)?.signedOff ?? null;
  }

  function setSignOff(key: string, signOff: SignOff | null) {
    const item = state.differences.find((difference) => difference.key === key);
    if (item) item.signedOff = signOff;
    else if (signOff) state.lineSignOffs[key] = signOff;
    else delete state.lineSignOffs[key];
  }

  function keyOf(signOffId: string): string {
    const all = [...Object.entries(state.lineSignOffs), ...state.differences.map((item) => [item.key, item.signedOff] as const)];
    const found = all.find(([, signOff]) => signOff?.id === signOffId);
    if (!found) throw new MigrationError("conflict");
    return found[0];
  }

  return {
    getOverview: () => wait(() => undefined),
    runTrial: (input: TrialInput) =>
      wait(() => {
        may("run_trial");
        state.clean = state.trialsRun > 0;
        state.trialsRun += 1;
        state.asOf = input.asOf;
        if (state.totals && state.totals.asOf !== input.asOf) state.totals = null;
        if (state.trialsRun === 1) state.differences = DIFFERENCES.map((item) => ({ ...item }));
      }),
    enterTotals: (input: TotalsInput) =>
      wait(() => {
        may("enter_totals");
        // A new entry replaces the last and waits for a second person, so sign-offs on the lines start again.
        state.totals = { ...input, id: nextId("totals"), enteredBy: YOU, enteredAt: new Date().toISOString(), confirmedBy: null, mine: true };
        state.lineSignOffs = {};
      }),
    confirmTotals: (totalsId: string) =>
      wait(() => {
        may("enter_totals");
        if (!state.totals || state.totals.id !== totalsId || state.totals.confirmedBy) throw new MigrationError("conflict");
        if (state.totals.mine) throw new MigrationError("forbidden");
        state.totals = { ...state.totals, confirmedBy: YOU };
      }),
    proposeSignOff: (key: string, reason: string) =>
      wait(() => {
        may("sign_off");
        if (signOffFor(key)) throw new MigrationError("conflict");
        setSignOff(key, { id: nextId("sign-off"), reason, state: "proposed", proposedBy: YOU, approvedBy: null, mine: true });
      }),
    decideSignOff: (signOffId: string, approve: boolean) =>
      wait(() => {
        may("sign_off");
        const key = keyOf(signOffId);
        const signOff = signOffFor(key)!;
        if (signOff.mine) throw new MigrationError("forbidden");
        if (signOff.state !== "proposed") throw new MigrationError("conflict");
        setSignOff(key, approve ? { ...signOff, state: "approved", approvedBy: YOU } : null);
      }),
  };
}
