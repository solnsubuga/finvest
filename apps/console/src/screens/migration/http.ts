/**
 * The migration checks over the real API: `/staff/migration` in
 * backend/app/modules/migration/api.py (see docs/migration.md).
 *
 * The routes give staff IDs, never names, and say nothing about who is signed in. Names come
 * from the shared directory, and `mine` is worked out here against the signed-in person's ID,
 * by the same rule the backend enforces: nobody confirms or decides their own entry.
 *
 * The backend keys every difference: `total:<figure>[:<currency>]` for a total line,
 * `investment:<ref>` for interest and `tax_rate:<ref>` for a tax rate. The figures of a
 * difference are exactly what a sign-off accepts; this file reads them back into the screen's
 * shape and never sends them.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import {
  MAX_SHEET_BYTES,
  MigrationError,
  type Allowed,
  type Amounts,
  type Blocker,
  type Currency,
  type Difference,
  type EnteredTotals,
  type Figure,
  type MigrationApi,
  type NotCarried,
  type Overview,
  type Problem,
  type SignOff,
  type Switch,
  type SwitchInput,
  type TotalLine,
  type TotalsInput,
  type TrialInput,
} from "./api.ts";

interface ProblemJson {
  sheet: Problem["sheet"];
  row: number | null;
  column: string | null;
  code: string;
  severity: Problem["severity"];
}

interface TrialJson {
  id: string;
  as_of: string;
  ran_by: string;
  ran_at: string;
  error_count: number;
  warning_count: number;
  problems: ProblemJson[];
}

interface SignOffJson {
  id: string;
  key: string;
  figures: number[];
  reason: string;
  proposed_by: string;
  proposed_at: string;
  state: SignOff["state"];
  decided_by: string | null;
  decision_reason: string | null;
}

interface AmountJson {
  currency: Currency;
  minor: number;
}

interface TotalsJson {
  id: string;
  as_of: string;
  entered_by: string;
  entered_at: string;
  confirmed_by: string | null;
  clients: number;
  verified_identities: number;
  running_investments: number;
  pending_orders: number;
  invested: AmountJson[];
  interest_before_tax: AmountJson[];
}

interface LineJson {
  key: string;
  figure: Figure;
  currency: Currency | null;
  old: number;
  new: number;
  agrees: boolean;
}

interface DifferenceJson {
  key: string;
  figures: number[];
  sign_off: SignOffJson | null;
  /** Not sent yet. When the backend sends them, the screen shows them. */
  row?: number | null;
  currency?: Currency | null;
}

interface ClaimsJson {
  invited: number;
  claimed_and_confirmed: number;
  said_something_is_wrong: number;
  not_yet_claimed: number;
  cannot_be_invited: number;
}

interface NotCarriedJson {
  legacy_client_ref: string;
  code: string;
  attempt: number;
  noted_at: string;
  state: NotCarried["state"];
  resolution_id: string | null;
  proposed_by: string | null;
  reason: string | null;
}

interface SwitchJson {
  id: string;
  trial_id: string;
  as_of: string;
  proposed_by: string;
  proposed_at: string;
  state: Switch["state"];
  decided_by: string | null;
  decision_reason: string | null;
  clients_carried: number;
  not_carried: NotCarriedJson[];
}

export interface OverviewJson {
  trials_run: number;
  trial: TrialJson | null;
  totals: TotalsJson | null;
  lines: LineJson[];
  differences: DifferenceJson[];
  blockers: Blocker[];
  claims: ClaimsJson | null;
  /** Older backends leave it out. */
  switch?: SwitchJson | null;
  allowed: Allowed[];
}

/** Who someone is, for the screen. A name that cannot be found is "A colleague". */
type Names = (staffId: string) => string;

const COLLEAGUE = "A colleague";

function staffIdsIn(json: OverviewJson): string[] {
  const ids = new Set<string>();
  if (json.trial) ids.add(json.trial.ran_by);
  if (json.totals) {
    ids.add(json.totals.entered_by);
    if (json.totals.confirmed_by) ids.add(json.totals.confirmed_by);
  }
  for (const item of json.differences) {
    if (item.sign_off) {
      ids.add(item.sign_off.proposed_by);
      if (item.sign_off.decided_by) ids.add(item.sign_off.decided_by);
    }
  }
  if (json.switch) {
    ids.add(json.switch.proposed_by);
    if (json.switch.decided_by) ids.add(json.switch.decided_by);
    for (const client of json.switch.not_carried) if (client.proposed_by) ids.add(client.proposed_by);
  }
  return [...ids];
}

function toSignOff(json: SignOffJson, me: string, name: Names): SignOff {
  return {
    id: json.id,
    reason: json.reason,
    state: json.state,
    proposedBy: name(json.proposed_by),
    decidedBy: json.decided_by === null ? null : name(json.decided_by),
    decisionReason: json.decision_reason,
    mine: json.proposed_by === me,
  };
}

function amounts(list: readonly AmountJson[]): Amounts {
  const found: Amounts = {};
  for (const amount of list) found[amount.currency] = String(amount.minor);
  return found;
}

function toTotals(json: TotalsJson, me: string, name: Names): EnteredTotals {
  return {
    id: json.id,
    asOf: json.as_of,
    clients: String(json.clients),
    verifiedIdentities: String(json.verified_identities),
    runningInvestments: String(json.running_investments),
    pendingOrders: String(json.pending_orders),
    invested: amounts(json.invested),
    interestBeforeTax: amounts(json.interest_before_tax),
    enteredBy: name(json.entered_by),
    enteredAt: json.entered_at,
    confirmedBy: json.confirmed_by === null ? null : name(json.confirmed_by),
    mine: json.entered_by === me,
  };
}

function toSwitch(json: SwitchJson, me: string, name: Names): Switch {
  return {
    id: json.id,
    asOf: json.as_of,
    proposedBy: name(json.proposed_by),
    proposedAt: json.proposed_at,
    state: json.state,
    decidedBy: json.decided_by === null ? null : name(json.decided_by),
    decisionReason: json.decision_reason,
    clientsCarried: json.clients_carried,
    notCarried: json.not_carried.map((client) => ({
      legacyClientRef: client.legacy_client_ref,
      code: client.code,
      attempt: client.attempt,
      notedAt: client.noted_at,
      state: client.state,
      resolutionId: client.resolution_id,
      proposedBy: client.proposed_by === null ? null : name(client.proposed_by),
      reason: client.reason,
      mine: client.proposed_by === me,
    })),
    mine: json.proposed_by === me,
  };
}

/** A difference for one investment, or null for a total line's key or one this console does not know. */
export function toDifference(json: DifferenceJson, signedOff: SignOff | null): Difference | null {
  const match = /^(investment|tax_rate):(.+)$/.exec(json.key);
  if (!match || json.figures.length < 2) return null;
  return {
    key: json.key,
    kind: match[1] === "investment" ? "interest" : "tax_rate",
    row: json.row ?? null,
    legacyInvestmentRef: match[2]!,
    currency: json.currency ?? null,
    // Interest: old and new before tax. A tax rate: stated and the product's, in basis points.
    old: String(json.figures[0]),
    new: String(json.figures[1]),
    signedOff,
  };
}

export function toOverview(json: OverviewJson, me: string, name: Names): Overview {
  const signOffs = new Map(
    json.differences.map((item) => [item.key, item.sign_off ? toSignOff(item.sign_off, me, name) : null]),
  );
  // The backend gives the totals' lines only once a second person has confirmed them. Before
  // then the trial's side is not shown on its own: there is nothing to compare it with.
  const lines: TotalLine[] = json.lines.map((line) => ({
    key: line.key,
    figure: line.figure,
    currency: line.currency,
    old: String(line.old),
    new: String(line.new),
    signedOff: signOffs.get(line.key) ?? null,
  }));
  const differences = json.differences.flatMap((item) => toDifference(item, signOffs.get(item.key) ?? null) ?? []);
  return {
    trialsRun: json.trials_run,
    trial: json.trial && {
      asOf: json.trial.as_of,
      ranAt: json.trial.ran_at,
      ranBy: name(json.trial.ran_by),
      errorCount: json.trial.error_count,
      warningCount: json.trial.warning_count,
      problems: json.trial.problems,
    },
    totals: json.totals && toTotals(json.totals, me, name),
    lines,
    differences,
    claims: json.claims && {
      invited: json.claims.invited,
      claimedAndConfirmed: json.claims.claimed_and_confirmed,
      saidSomethingIsWrong: json.claims.said_something_is_wrong,
      notYetClaimed: json.claims.not_yet_claimed,
      cannotBeInvited: json.claims.cannot_be_invited,
    },
    switch: json.switch ? toSwitch(json.switch, me, name) : null,
    blockers: json.blockers,
    allowed: json.allowed,
  };
}

function toAmounts(values: Amounts): AmountJson[] {
  return (Object.entries(values) as [Currency, string | undefined][]).flatMap(([currency, minor]) =>
    minor === undefined ? [] : [{ currency, minor: Number(minor) }],
  );
}

/** Whole numbers above this cannot travel exactly as JSON numbers. The backend refuses them too. */
const MAX_FIGURE = BigInt(Number.MAX_SAFE_INTEGER);

function whole(value: string): number {
  if (!/^\d+$/.test(value) || BigInt(value) > MAX_FIGURE) throw new MigrationError("invalid");
  return Number(value);
}

export function totalsBody(input: TotalsInput) {
  const amountsOf = (values: Amounts) => {
    for (const minor of Object.values(values)) if (minor !== undefined) whole(minor);
    return toAmounts(values);
  };
  return {
    as_of: input.asOf,
    clients: whole(input.clients),
    verified_identities: whole(input.verifiedIdentities),
    running_investments: whole(input.runningInvestments),
    pending_orders: whole(input.pendingOrders),
    invested: amountsOf(input.invested),
    interest_before_tax: amountsOf(input.interestBeforeTax),
  };
}

export function toMigrationError(error: unknown): MigrationError {
  if (error instanceof MigrationError) return error;
  if (!(error instanceof ApiError)) return new MigrationError("unavailable");
  const fields = error.fields.map((field) => field.code);
  switch (error.code) {
    case "validation_failed":
      if (fields.includes("not_the_trial_sheet")) return new MigrationError("not_the_trial_sheet");
      if (error.fields.some((field) => field.code === "missing" && field.field_path.at(-1) === "phone")) {
        return new MigrationError("phone_missing");
      }
      return new MigrationError("invalid");
    case "phone_taken":
      return new MigrationError("phone_taken");
    case "payload_too_large":
      return new MigrationError("file_too_large");
    // A sign-off for figures that no longer stand, totals already confirmed, or one's own entry.
    case "conflict":
    case "not_found":
      return new MigrationError("conflict");
    case "confirm_it_is_you":
      return new MigrationError("not_confirmed");
    case "forbidden":
      return new MigrationError("forbidden");
    default:
      return error.status === 413 ? new MigrationError("file_too_large") : new MigrationError("unavailable");
  }
}

export function createHttpMigrationApi(http: Http, directory: Directory, me: string): MigrationApi {
  async function named(json: OverviewJson): Promise<Overview> {
    const ids = staffIdsIn(json);
    const found = await Promise.all(ids.map((id) => directory.staffName(id).catch(() => null)));
    const names = new Map(ids.map((id, index) => [id, found[index] ?? null]));
    return toOverview(json, me, (id) => (id === me ? "You" : (names.get(id) ?? COLLEAGUE)));
  }

  async function read(): Promise<Overview> {
    try {
      return await named(await http.request<OverviewJson>("GET", "/staff/migration"));
    } catch (error) {
      throw toMigrationError(error);
    }
  }

  /** A change that answers with the overview. */
  async function change(run: () => Promise<OverviewJson>): Promise<Overview> {
    let json: OverviewJson;
    try {
      json = await run();
    } catch (error) {
      throw toMigrationError(error);
    }
    return named(json);
  }

  /** A change that answers with something else: the overview is read again after it. */
  async function thenRead(run: () => Promise<unknown>): Promise<Overview> {
    try {
      await run();
    } catch (error) {
      throw toMigrationError(error);
    }
    try {
      return await read();
    } catch {
      throw new MigrationError("stale");
    }
  }

  async function sheets(input: SwitchInput) {
    if (input.clients.size > MAX_SHEET_BYTES || input.investments.size > MAX_SHEET_BYTES) {
      throw new MigrationError("file_too_large");
    }
    const [clients, investments] = await Promise.all([input.clients.text(), input.investments.text()]);
    return { clients_csv: clients, investments_csv: investments };
  }

  const decision = (approve: boolean, reason?: string) => ({ approve, reason: reason ?? null });

  return {
    getOverview: read,
    async runTrial(input: TrialInput) {
      if (input.clients.size > MAX_SHEET_BYTES || input.investments.size > MAX_SHEET_BYTES) {
        throw new MigrationError("file_too_large");
      }
      const [clients, investments] = await Promise.all([input.clients.text(), input.investments.text()]);
      return change(() =>
        http.request<OverviewJson>("POST", "/staff/migration/trials", {
          as_of: input.asOf,
          clients_csv: clients,
          investments_csv: investments,
        }),
      );
    },
    enterTotals: (input) => change(() => http.request<OverviewJson>("PUT", "/staff/migration/control-totals", totalsBody(input))),
    confirmTotals: (totalsId) =>
      change(() =>
        http.request<OverviewJson>("POST", `/staff/migration/control-totals/${encodeURIComponent(totalsId)}/confirmation`),
      ),
    proposeSignOff: (key, reason) => thenRead(() => http.request("POST", "/staff/migration/sign-offs", { key, reason })),
    decideSignOff: (signOffId, approve, reason) =>
      thenRead(() =>
        http.request("POST", `/staff/migration/sign-offs/${encodeURIComponent(signOffId)}/decision`, {
          approve,
          reason: reason ?? null,
        }),
      ),
    async proposeSwitch(input) {
      const body = await sheets(input);
      return thenRead(() => http.request("POST", "/staff/migration/switch", body));
    },
    decideSwitch: (switchId, approve, reason) =>
      thenRead(() =>
        http.request("POST", `/staff/migration/switch/${encodeURIComponent(switchId)}/decision`, decision(approve, reason)),
      ),
    proposeResolution: (switchId, legacyClientRef, action, reason) =>
      thenRead(() =>
        http.request("POST", `/staff/migration/switch/${encodeURIComponent(switchId)}/resolutions`, {
          legacy_client_ref: legacyClientRef,
          action,
          reason,
        }),
      ),
    decideResolution: (resolutionId, approve, reason) =>
      thenRead(() =>
        http.request(
          "POST",
          `/staff/migration/switch/resolutions/${encodeURIComponent(resolutionId)}/decision`,
          decision(approve, reason),
        ),
      ),
  };
}
