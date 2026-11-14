/**
 * Which account in each accounting system every Finvest ledger code is posted
 * to, for the daily journal downloaded on the Finance screen.
 *
 * The shapes follow `/staff/finance/account-codes` in
 * backend/app/modules/finance/api.py. A change is a whole new map for one
 * system: one person proposes it and a different person approves it.
 */

import { ApiError, type Http } from "@/api/http.ts";

export type AccountingSystem = "quickbooks" | "xero" | "sage" | "zoho";
export type MapStatus = "proposed" | "approved" | "rejected" | "withdrawn";
export type MapDecision = "approved" | "rejected" | "withdrawn";

export interface LedgerCode {
  code: string;
  name: string;
  kind: string;
}

export interface MapVersion {
  id: string;
  /** Ledger code to the system's account. A code that is not here is not mapped. */
  accounts: Readonly<Record<string, string>>;
  taxRate: string | null;
  reason: string;
  status: MapStatus;
  proposedBy: string;
  proposedByName: string | null;
  proposedAt: string;
  decidedBy: string | null;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  /** The signed-in member of staff proposed it: they may withdraw it, never approve it. */
  yours: boolean;
}

export interface SystemMap {
  system: AccountingSystem;
  name: string;
  /** What the system calls the value: account name, account code or nominal code. */
  accountLabel: string;
  needsTaxRate: boolean;
  defaultTaxRate: string | null;
  active: MapVersion | null;
  proposal: MapVersion | null;
  /** The last decisions, newest first, without their accounts. */
  history: readonly MapVersion[];
}

export interface CodeMaps {
  codes: readonly LedgerCode[];
  systems: readonly SystemMap[];
}

export interface NewMap {
  accounts: Record<string, string>;
  taxRate: string | null;
  reason: string;
}

export interface AccountCodesApi {
  list(): Promise<CodeMaps>;
  propose(system: AccountingSystem, map: NewMap): Promise<CodeMaps>;
  decide(mapId: string, decision: MapDecision, reason: string): Promise<CodeMaps>;
}

/** A field the server refused, such as one code's account. */
export interface CodeProblem {
  /** The ledger code, or "tax_rate" or "reason". */
  on: string;
  code: string;
}

export type AccountCodesErrorCode = "invalid" | "conflict" | "forbidden" | "not_found" | "not_confirmed" | "not_connected" | "unavailable";

export class AccountCodesError extends Error {
  constructor(
    readonly code: AccountCodesErrorCode,
    readonly problems: readonly CodeProblem[] = [],
  ) {
    super(code);
    this.name = "AccountCodesError";
  }
}

const MESSAGES: Record<AccountCodesErrorCode, string> = {
  invalid: "Finvest refused some of these codes. Fix what is marked and send them again.",
  conflict: "A change to this system's codes is already waiting. It is decided or withdrawn first.",
  forbidden: "Your role can read these codes but not change them, and nobody decides their own change.",
  not_found: "This change has already been decided.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_connected: "Accounting system codes are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function accountCodesErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof AccountCodesError ? error.code : "unavailable"];
}

const PROBLEMS: Record<string, string> = {
  unknown_code: "Finvest has no account with this code.",
  bad_quickbooks_account: "Write the account's name as QuickBooks shows it, on one line. A sub-account is Parent:Child.",
  bad_xero_account: "A Xero account code is up to 10 letters or numbers.",
  bad_sage_account: "A Sage nominal code is a number of up to 8 digits.",
  bad_zoho_account: "Write the account's name as Zoho Books shows it, on one line.",
  required: "This is needed.",
  not_for_this_system: "Only Xero asks for a tax rate.",
};

export function codeProblemText(code: string): string {
  return PROBLEMS[code] ?? "Finvest refused this.";
}

/** The same checks the server makes, so a mistake shows before sending. */
export function checkAccount(system: AccountingSystem, account: string): string | null {
  const value = account.trim();
  if (!value) return null;
  const ok =
    system === "sage" ? /^[0-9]{1,8}$/.test(value) : system === "xero" ? /^[A-Za-z0-9._-]{1,10}$/.test(value) : value.length <= 100 && ![...value].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127);
  return ok ? null : `bad_${system}_account`;
}

export function toAccountCodesError(error: unknown): AccountCodesError {
  if (error instanceof AccountCodesError) return error;
  if (!(error instanceof ApiError)) return new AccountCodesError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new AccountCodesError(
        "invalid",
        error.fields.map((field) => ({
          on: field.field_path[0] === "accounts" ? String(field.field_path[1] ?? "") : String(field.field_path.at(-1) ?? ""),
          code: field.code,
        })),
      );
    case "conflict":
      return new AccountCodesError("conflict");
    case "forbidden":
      return new AccountCodesError("forbidden");
    case "not_found":
      return new AccountCodesError("not_found");
    case "confirm_it_is_you":
      return new AccountCodesError("not_confirmed");
    default:
      return new AccountCodesError("unavailable");
  }
}

// Over HTTP ------------------------------------------------------------------

interface VersionJson {
  id: string;
  accounts: Record<string, string>;
  tax_rate: string | null;
  reason: string;
  status: MapStatus;
  proposed_by: string;
  proposed_by_name: string | null;
  proposed_at: string;
  decided_by: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  yours: boolean;
}

interface MapsJson {
  codes: LedgerCode[];
  systems: {
    system: AccountingSystem;
    name: string;
    account_label: string;
    needs_tax_rate: boolean;
    default_tax_rate: string | null;
    active: VersionJson | null;
    proposal: VersionJson | null;
    history: VersionJson[];
  }[];
}

function toVersion(json: VersionJson): MapVersion {
  return {
    id: json.id,
    accounts: json.accounts,
    taxRate: json.tax_rate,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    yours: json.yours,
  };
}

export function toCodeMaps(json: MapsJson): CodeMaps {
  return {
    codes: json.codes.map((c) => ({ code: c.code, name: c.name, kind: c.kind })),
    systems: json.systems.map((s) => ({
      system: s.system,
      name: s.name,
      accountLabel: s.account_label,
      needsTaxRate: s.needs_tax_rate,
      defaultTaxRate: s.default_tax_rate,
      active: s.active ? toVersion(s.active) : null,
      proposal: s.proposal ? toVersion(s.proposal) : null,
      history: s.history.map(toVersion),
    })),
  };
}

const PATH = "/staff/finance/account-codes";

export function createHttpAccountCodesApi(http: Http): AccountCodesApi {
  async function call(work: () => Promise<MapsJson>): Promise<CodeMaps> {
    try {
      return toCodeMaps(await work());
    } catch (error) {
      throw toAccountCodesError(error);
    }
  }
  return {
    list: () => call(() => http.request<MapsJson>("GET", PATH)),
    propose: (system, map) =>
      call(() =>
        http.request<MapsJson>("POST", `${PATH}/${encodeURIComponent(system)}/proposals`, {
          accounts: map.accounts,
          tax_rate: map.taxRate,
          reason: map.reason,
        }),
      ),
    decide: (mapId, decision, reason) =>
      call(() => http.request<MapsJson>("POST", `${PATH}/proposals/${encodeURIComponent(mapId)}/decision`, { decision, reason })),
  };
}

const notConnected = () => Promise.reject(new AccountCodesError("not_connected"));

export const notConnectedAccountCodesApi: AccountCodesApi = {
  list: notConnected,
  propose: notConnected,
  decide: notConnected,
};
