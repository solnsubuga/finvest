/**
 * Sample accounting system codes for `pnpm dev` and previews. Not Finvest's
 * chart. QuickBooks is in use, a change to Xero waits for a second person,
 * and Sage and Zoho Books are not set up. Changes stay in this browser tab.
 */

import {
  AccountCodesError,
  checkAccount,
  type AccountCodesApi,
  type AccountingSystem,
  type CodeMaps,
  type LedgerCode,
  type MapVersion,
  type SystemMap,
} from "./accountCodes.ts";

interface Reader {
  id: string;
  name: string;
}

export const SAMPLE_CODES: readonly LedgerCode[] = [
  { code: "bank.trust", name: "Trust bank account", kind: "asset" },
  { code: "client.awaiting_start", name: "Paid in, awaiting start", kind: "liability" },
  { code: "dealing.bill_income", name: "Income on bills held to maturity", kind: "income" },
  { code: "dealing.bills", name: "Bills and bonds held, at cost", kind: "asset" },
  { code: "fees.provider.iotec", name: "ioTec fees", kind: "expense" },
  { code: "investments.holdings", name: "Invested", kind: "liability" },
  { code: "investments.interest_expense", name: "Interest paid to clients", kind: "expense" },
  { code: "iotec.wallet", name: "ioTec wallet", kind: "asset" },
  { code: "tax.withholding_payable", name: "Tax withheld, to pay URA", kind: "liability" },
];

const QUICKBOOKS: Record<string, string> = {
  "bank.trust": "Stanbic trust account",
  "client.awaiting_start": "Client funds:Awaiting start",
  "dealing.bill_income": "Treasury bill income",
  "dealing.bills": "Treasury bills held",
  "fees.provider.iotec": "Bank and payment charges",
  "investments.holdings": "Client funds:Invested",
  "investments.interest_expense": "Interest to clients",
  "iotec.wallet": "ioTec wallet",
  "tax.withholding_payable": "Withholding tax payable",
};

const XERO_NOW: Record<string, string> = {
  "bank.trust": "090",
  "client.awaiting_start": "820",
  "dealing.bill_income": "200",
  "dealing.bills": "610",
  "investments.holdings": "821",
  "investments.interest_expense": "470",
  "iotec.wallet": "091",
  "tax.withholding_payable": "825",
};

export function createStubAccountCodesApi(reader: Reader, now: () => number = Date.now, sample: "sample" | "empty" = "sample"): AccountCodesApi {
  const ago = (hours: number) => new Date(now() - hours * 3600 * 1000).toISOString();
  const version = (id: string, accounts: Record<string, string>, extra: Partial<MapVersion>): MapVersion => ({
    id,
    accounts,
    taxRate: null,
    reason: "",
    status: "approved",
    proposedBy: "stub-grace",
    proposedByName: "Grace Atim",
    proposedAt: ago(72),
    decidedBy: "stub-peter",
    decidedByName: "Peter Okello",
    decidedAt: ago(70),
    decisionReason: "Checked against the chart",
    yours: false,
    ...extra,
  });
  const base = (system: AccountingSystem, name: string, accountLabel: string): SystemMap => ({
    system,
    name,
    accountLabel,
    needsTaxRate: system === "xero",
    defaultTaxRate: system === "xero" ? "Tax Exempt" : null,
    active: null,
    proposal: null,
    history: [],
  });
  const systems: SystemMap[] = [
    base("quickbooks", "QuickBooks Online", "Account name"),
    base("xero", "Xero", "Account code"),
    base("sage", "Sage Accounting", "Nominal code"),
    base("zoho", "Zoho Books", "Account name"),
  ];
  if (sample === "sample") {
    const qb = version("stub-map-qb-1", QUICKBOOKS, { reason: "Chart of accounts from the accountant" });
    systems[0] = { ...systems[0]!, active: qb, history: [qb] };
    const xero = version("stub-map-xero-1", XERO_NOW, { reason: "First Xero codes", taxRate: "Tax Exempt" });
    systems[1] = {
      ...systems[1]!,
      active: xero,
      history: [xero],
      proposal: version(
        "stub-map-xero-2",
        { ...XERO_NOW, "fees.provider.iotec": "404", "dealing.bills": "611" },
        {
          status: "proposed",
          reason: "ioTec fees get their own account, and bills move to the new investments account",
          proposedAt: ago(3),
          decidedBy: null,
          decidedByName: null,
          decidedAt: null,
          decisionReason: null,
          taxRate: "Tax Exempt",
        },
      ),
    };
  }
  let count = 0;
  const snapshot = (): CodeMaps => ({ codes: SAMPLE_CODES, systems: systems.map((s) => ({ ...s })) });

  return {
    list: () => Promise.resolve(snapshot()),
    propose(system, map) {
      const index = systems.findIndex((s) => s.system === system);
      const item = systems[index]!;
      if (item.proposal) return Promise.reject(new AccountCodesError("conflict"));
      const problems = Object.entries(map.accounts).flatMap(([on, account]) => {
        const code = checkAccount(system, account);
        return code ? [{ on, code }] : [];
      });
      if (problems.length > 0) return Promise.reject(new AccountCodesError("invalid", problems));
      const accounts = Object.fromEntries(Object.entries(map.accounts).flatMap(([code, account]) => (account.trim() ? [[code, account.trim()]] : [])));
      count += 1;
      systems[index] = {
        ...item,
        proposal: version(`stub-map-new-${count}`, accounts, {
          status: "proposed",
          reason: map.reason.trim(),
          taxRate: map.taxRate?.trim() || null,
          proposedBy: reader.id,
          proposedByName: reader.name,
          proposedAt: new Date(now()).toISOString(),
          decidedBy: null,
          decidedByName: null,
          decidedAt: null,
          decisionReason: null,
          yours: true,
        }),
      };
      return Promise.resolve(snapshot());
    },
    decide(mapId, decision, reason) {
      const index = systems.findIndex((s) => s.proposal?.id === mapId);
      if (index < 0) return Promise.reject(new AccountCodesError("not_found"));
      const item = systems[index]!;
      const proposal = item.proposal!;
      if (proposal.yours !== (decision === "withdrawn")) return Promise.reject(new AccountCodesError("forbidden"));
      const decided: MapVersion = {
        ...proposal,
        status: decision,
        decidedBy: reader.id,
        decidedByName: reader.name,
        decidedAt: new Date(now()).toISOString(),
        decisionReason: reason.trim(),
      };
      systems[index] = {
        ...item,
        active: decision === "approved" ? decided : item.active,
        proposal: null,
        history: [{ ...decided, accounts: {} }, ...item.history].slice(0, 5),
      };
      return Promise.resolve(snapshot());
    },
  };
}
