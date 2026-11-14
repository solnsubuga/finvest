import {
  MigrationError,
  type Allowed,
  type Amounts,
  type Blocker,
  type Difference,
  type EnteredTotals,
  type MigrationApi,
  type NotCarried,
  type Overview,
  type Problem,
  type ResolutionAction,
  type SignOff,
  type Switch,
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
  decidedBy: "Daniel Admin",
  decisionReason: null,
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
    signedOff: { id: "sign-off-20977", reason: "Finvest 1.0 counted the start date as a day earned.", state: "proposed", proposedBy: "Grace Finance", decidedBy: null, decisionReason: null, mine: false },
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
  switch: Switch | null;
  allowed: readonly Allowed[];
}

const NOT_CARRIED: readonly NotCarried[] = [
  { legacyClientRef: "C-10482", code: "document_taken", attempt: 1, notedAt: "2026-11-14T06:02:00Z", state: "noted", resolutionId: null, proposedBy: null, reason: null, mine: false },
  {
    legacyClientRef: "C-11930",
    code: "investment_interest_differs",
    attempt: 1,
    notedAt: "2026-11-14T06:03:00Z",
    state: "retry_proposed",
    resolutionId: "resolution-11930",
    proposedBy: "Grace Finance",
    reason: "The 12% product's rate for July was corrected to match Finvest 1.0.",
    mine: false,
  },
  {
    legacyClientRef: "C-12007",
    code: "document_taken",
    attempt: 2,
    notedAt: "2026-11-14T07:15:00Z",
    state: "close_proposed",
    resolutionId: "resolution-12007",
    proposedBy: YOU,
    reason: "Same person as an account opened in October. Operations will merge them by hand.",
    mine: true,
  },
];

const PROPOSED_SWITCH: Switch = {
  id: "switch-sample",
  asOf: "2026-11-12",
  proposedBy: "Sarah Operations",
  proposedAt: "2026-11-14T05:10:00Z",
  state: "proposed",
  decidedBy: null,
  decisionReason: null,
  clientsCarried: 0,
  notCarried: [],
  mine: false,
};

function build(state: State): Overview {
  if (state.trialsRun === 0) {
    return { trialsRun: 0, trial: null, totals: null, lines: [], differences: [], claims: null, switch: null, blockers: ["no_trial"], allowed: state.allowed };
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
    claims:
      state.switch?.state === "approved"
        ? { invited: 1909, claimedAndConfirmed: 0, saidSomethingIsWrong: 0, notYetClaimed: 1909, cannotBeInvited: 0 }
        : null,
    switch: state.switch,
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
const EVERYTHING: readonly Allowed[] = [
  "run_trial",
  "propose_switch",
  "propose_resolution",
  "enter_totals",
  "sign_off",
  "decide_switch",
  "decide_resolution",
];

/** A second trial that came back clean, with every total and difference signed off. */
function cleared(allowed: readonly Allowed[], proposal: Switch | null): State {
  const signedOff = (item: Difference): Difference => ({ ...item, signedOff: approvedBy(`sign-off-${item.key}`, ROUNDING) });
  const lineSignOffs: Record<string, SignOff> = {};
  const imported = newTotals(true);
  for (const line of LINES) {
    if (BigInt(oldFigure(SAMPLE_TOTALS, line)) !== BigInt(imported[line.key]!)) lineSignOffs[line.key] = approvedBy(`sign-off-${line.key}`, ROUNDING);
  }
  return { trialsRun: 2, asOf: "2026-11-12", clean: true, totals: SAMPLE_TOTALS, differences: DIFFERENCES.map(signedOff), lineSignOffs, switch: proposal, allowed };
}

/**
 * `start` picks what the screen opens with: nothing imported yet, a first trial
 * with problems, that trial with Finvest 1.0 totals a colleague entered and
 * nobody has confirmed, every check cleared with a switch a colleague proposed,
 * the same where approving finds a phone number that already has an account,
 * or an approved switch with clients it could not carry over. `allowed` is
 * everything by default, like an admin.
 */
export function createStubMigrationApi(
  delayMs = 150,
  start: "empty" | "imported" | "unconfirmed" | "cleared" | "taken" | "switched" = "imported",
  allowed: readonly Allowed[] = EVERYTHING,
): MigrationApi {
  const state: State =
    start === "empty"
      ? { trialsRun: 0, asOf: "2026-11-12", clean: false, totals: null, differences: [], lineSignOffs: {}, switch: null, allowed }
      : start === "cleared" || start === "taken"
        ? cleared(allowed, PROPOSED_SWITCH)
        : start === "switched"
          ? cleared(allowed, {
              ...PROPOSED_SWITCH,
              state: "approved",
              decidedBy: "Grace Finance",
              clientsCarried: 1912,
              notCarried: NOT_CARRIED.map((client) => ({ ...client })),
            })
          : {
              trialsRun: 1,
              asOf: "2026-11-12",
              clean: false,
              totals: start === "unconfirmed" ? { ...SAMPLE_TOTALS, confirmedBy: null } : SAMPLE_TOTALS,
              differences: DIFFERENCES.map((item) => ({ ...item })),
              lineSignOffs: { "total:interest_before_tax:USD": approvedBy("sign-off-usd", ROUNDING) },
              switch: null,
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
        // A new entry replaces the last and waits for a second person. As in the backend, a sign-off
        // on a total line counts again only if that line's Finvest 1.0 figure comes out the same.
        const before = state.totals;
        const entered: EnteredTotals = { ...input, id: nextId("totals"), enteredBy: YOU, enteredAt: new Date().toISOString(), confirmedBy: null, mine: true };
        for (const line of LINES) {
          if (before === null || BigInt(oldFigure(before, line)) !== BigInt(oldFigure(entered, line))) delete state.lineSignOffs[line.key];
        }
        state.totals = entered;
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
        // A rejected sign-off may be proposed again, and the new one replaces it.
        const current = signOffFor(key);
        if (current && current.state !== "rejected") throw new MigrationError("conflict");
        setSignOff(key, { id: nextId("sign-off"), reason, state: "proposed", proposedBy: YOU, decidedBy: null, decisionReason: null, mine: true });
      }),
    decideSignOff: (signOffId: string, approve: boolean, reason?: string) =>
      wait(() => {
        may("sign_off");
        const key = keyOf(signOffId);
        const signOff = signOffFor(key)!;
        if (signOff.mine) throw new MigrationError("forbidden");
        if (signOff.state !== "proposed") throw new MigrationError("conflict");
        setSignOff(key, { ...signOff, state: approve ? "approved" : "rejected", decidedBy: YOU, decisionReason: reason?.trim() || null });
      }),
    proposeSwitch: () =>
      wait(() => {
        may("propose_switch");
        if (build(state).blockers.length > 0 || (state.switch && state.switch.state !== "rejected")) throw new MigrationError("conflict");
        state.switch = { ...PROPOSED_SWITCH, id: nextId("switch"), proposedBy: YOU, proposedAt: new Date().toISOString(), mine: true };
      }),
    decideSwitch: (switchId: string, approve: boolean, reason?: string) =>
      wait(() => {
        may("decide_switch");
        const current = state.switch;
        if (!current || current.id !== switchId || current.state !== "proposed") throw new MigrationError("conflict");
        if (current.mine) throw new MigrationError("forbidden");
        // `taken`: a phone number in the sheets already has an account, so nothing is created.
        if (approve && start === "taken") throw new MigrationError("phone_taken");
        state.switch = approve
          ? { ...current, state: "approved", decidedBy: YOU, clientsCarried: 1912, notCarried: NOT_CARRIED.slice(0, 1).map((client) => ({ ...client })) }
          : { ...current, state: "rejected", decidedBy: YOU, decisionReason: reason?.trim() || null };
      }),
    proposeResolution: (switchId: string, ref: string, action: ResolutionAction, reason: string) =>
      wait(() => {
        may("propose_resolution");
        const client = state.switch?.id === switchId ? state.switch.notCarried.find((item) => item.legacyClientRef === ref) : undefined;
        if (!client || client.state !== "noted") throw new MigrationError("conflict");
        updateClient(ref, { state: action === "retry" ? "retry_proposed" : "close_proposed", resolutionId: nextId("resolution"), proposedBy: YOU, reason, mine: true });
      }),
    decideResolution: (resolutionId: string, approve: boolean) =>
      wait(() => {
        may("decide_resolution");
        const client = state.switch?.notCarried.find((item) => item.resolutionId === resolutionId);
        if (!client || (client.state !== "retry_proposed" && client.state !== "close_proposed")) throw new MigrationError("conflict");
        if (client.mine) throw new MigrationError("forbidden");
        if (!approve) updateClient(client.legacyClientRef, { state: "noted", resolutionId: null, proposedBy: null, reason: null, mine: false });
        else if (client.state === "close_proposed") removeClient(client.legacyClientRef);
        else updateClient(client.legacyClientRef, { state: "retrying" });
      }),
  };

  function updateClient(ref: string, change: Partial<NotCarried>) {
    const current = state.switch!;
    state.switch = { ...current, notCarried: current.notCarried.map((item) => (item.legacyClientRef === ref ? { ...item, ...change } : item)) };
  }

  function removeClient(ref: string) {
    const current = state.switch!;
    state.switch = { ...current, notCarried: current.notCarried.filter((item) => item.legacyClientRef !== ref) };
  }
}
