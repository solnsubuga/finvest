/**
 * Sample product types for `pnpm dev` and previews: the three Finvest started with, a type a
 * colleague defined, and a new one waiting for approval. Not connected to Finvest: commands change
 * only this browser tab's copy, and the checks here stand in for the backend's.
 */

import type { ProposalStatus } from "../api.ts";
import { ProductTypesError, type ProductType, type ProductTypeChange, type ProductTypesApi, type ReturnModelInfo } from "./api.ts";

export interface ProductTypesStubOptions {
  me: string;
  /** "empty" starts with only the built-in types. "offline" acts as if Finvest could not be reached. */
  sample?: "default" | "empty" | "offline";
}

const COLLEAGUE = "-StaffSampleFinance01";
const CODE = /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$/;

export const SAMPLE_MODELS: readonly ReturnModelInfo[] = [
  { model: "treasury_bill", description: "Earns a rate before tax over a term in days, paid at maturity. Bought at the auction at a discount." },
  { model: "fixed_income", description: "Earns a rate before tax over a term, or an open term, paid at maturity or monthly." },
  {
    model: "treasury_bond",
    description: "An issue with its own ISIN, coupon and dates, bought at a price. Pays a coupon every six months and the face value at maturity.",
  },
];

export function builtInTypes(): ProductType[] {
  return [
    {
      code: "TREASURY-BILL",
      name: "Treasury bill",
      model: "treasury_bill",
      description: "A term in days and a rate before tax, paid at maturity. Bought at the Bank of Uganda auction.",
      defaultTaxBps: 2000,
      status: "active",
      builtIn: true,
      inUse: 3,
    },
    {
      code: "FIXED-INCOME",
      name: "Fixed income",
      model: "fixed_income",
      description: "A term and a rate before tax, paid at maturity or monthly.",
      defaultTaxBps: 1500,
      status: "active",
      builtIn: true,
      inUse: 2,
    },
    {
      code: "TREASURY-BOND",
      name: "Treasury bond",
      model: "treasury_bond",
      description: "A Bank of Uganda issue: ISIN, coupon, dates and today's price. Coupons every six months.",
      defaultTaxBps: 2000,
      status: "active",
      builtIn: true,
      inUse: 3,
    },
  ];
}

function sampleTypes(): ProductType[] {
  return [
    ...builtInTypes(),
    {
      code: "SAVINGS-NOTE",
      name: "Savings note",
      model: "fixed_income",
      description: "Money set aside for a goal, earning monthly interest with no fixed end date.",
      defaultTaxBps: 1500,
      status: "active",
      builtIn: false,
      inUse: 0,
    },
    {
      code: "HOLIDAY-BILL",
      name: "Holiday bill",
      model: "treasury_bill",
      description: "A 91 day bill sold over the December holidays.",
      defaultTaxBps: 1500,
      status: "retired",
      builtIn: false,
      inUse: 1,
    },
  ];
}

export function createStubProductTypesApi(options: ProductTypesStubOptions): ProductTypesApi {
  if (options.sample === "offline") {
    const offline = () => Promise.reject(new ProductTypesError("unavailable"));
    return { listTypes: offline, listChanges: offline, proposeType: offline, proposeEdit: offline, proposeStatus: offline, approve: offline, sendBack: offline, withdraw: offline };
  }
  const { me } = options;
  const types = options.sample === "empty" ? builtInTypes() : sampleTypes();
  const changes: ProductTypeChange[] = [];
  let counter = 0;
  const clone = <T>(value: T): T => structuredClone(value);
  const now = () => new Date().toISOString();

  function add(proposedBy: string, change: Pick<ProductTypeChange, "action" | "code" | "model" | "terms" | "reason">, at: string): ProductTypeChange {
    counter += 1;
    const made: ProductTypeChange = { ...change, id: `-TypeChangeSample0${counter}`, status: "proposed", proposedBy, proposedAt: at, decidedBy: null, decidedAt: null, decisionNote: null };
    changes.unshift(made);
    return clone(made);
  }

  if (options.sample !== "empty") {
    add(
      COLLEAGUE,
      {
        action: "create",
        code: "COMPANY-NOTE",
        model: "fixed_income",
        terms: { name: "Company note", description: "A fixed term note for companies, paying interest monthly.", defaultTaxBps: 1500 },
        reason: "Companies have asked for a monthly-paying product. Approved at the product meeting on 14 November.",
      },
      "2026-11-15T08:10:00Z",
    );
  }

  function typeFor(code: string): ProductType {
    const found = types.find((t) => t.code === code);
    if (!found) throw new ProductTypesError("not_found");
    return found;
  }

  function waiting(id: string): ProductTypeChange {
    const found = changes.find((c) => c.id === id);
    if (!found) throw new ProductTypesError("not_found");
    if (found.status !== "proposed") throw new ProductTypesError("conflict");
    return found;
  }

  function oneWaitingFor(code: string): void {
    if (changes.some((c) => c.status === "proposed" && c.code === code)) throw new ProductTypesError("conflict");
  }

  function nameTaken(name: string, except?: string): boolean {
    const same = (other: string) => other.trim().toLowerCase() === name.trim().toLowerCase();
    return types.some((t) => t.code !== except && same(t.name)) || changes.some((c) => c.status === "proposed" && c.code !== except && c.terms && same(c.terms.name));
  }

  function decide(change: ProductTypeChange, status: ProposalStatus, note: string | null): ProductTypeChange {
    change.status = status;
    change.decidedBy = me;
    change.decidedAt = now();
    change.decisionNote = note;
    return clone(change);
  }

  function attempt<T>(work: () => T): Promise<T> {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new ProductTypesError("unavailable"));
    }
  }

  return {
    listTypes: () => Promise.resolve({ types: clone(types), models: clone(SAMPLE_MODELS) }),
    listChanges: (status) => Promise.resolve(clone(changes.filter((c) => !status || c.status === status))),
    proposeType: (draft) =>
      attempt(() => {
        if (!draft.reason.trim()) throw new ProductTypesError("reason_required");
        if (!CODE.test(draft.code) || draft.code.length > 24) throw new ProductTypesError("invalid_code");
        if (types.some((t) => t.code === draft.code) || changes.some((c) => c.status === "proposed" && c.code === draft.code)) throw new ProductTypesError("code_taken");
        if (nameTaken(draft.name)) throw new ProductTypesError("name_taken");
        const { name, description, defaultTaxBps } = draft;
        return add(me, { action: "create", code: draft.code, model: draft.model, terms: { name, description, defaultTaxBps }, reason: draft.reason }, now());
      }),
    proposeEdit: (code, terms, reason) =>
      attempt(() => {
        if (!reason.trim()) throw new ProductTypesError("reason_required");
        const type = typeFor(code);
        oneWaitingFor(code);
        if (type.name === terms.name && type.description === terms.description && type.defaultTaxBps === terms.defaultTaxBps) throw new ProductTypesError("nothing_changes");
        if (nameTaken(terms.name, code)) throw new ProductTypesError("name_taken");
        return add(me, { action: "edit", code, model: type.model, terms, reason }, now());
      }),
    proposeStatus: (code, to, reason) =>
      attempt(() => {
        if (!reason.trim()) throw new ProductTypesError("reason_required");
        const type = typeFor(code);
        oneWaitingFor(code);
        if (to === "retired" && type.status === "retired") throw new ProductTypesError("already_retired");
        if (to === "active" && type.status === "active") throw new ProductTypesError("not_retired");
        return add(me, { action: to === "retired" ? "retire" : "restore", code, model: type.model, terms: null, reason }, now());
      }),
    approve: (id) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy === me) throw new ProductTypesError("forbidden");
        if (change.action === "create" && change.terms) {
          types.push({ code: change.code, model: change.model, ...change.terms, status: "active", builtIn: false, inUse: 0 });
        } else {
          const type = typeFor(change.code);
          if (change.action === "edit" && change.terms) Object.assign(type, change.terms);
          if (change.action === "retire") type.status = "retired";
          if (change.action === "restore") type.status = "active";
        }
        return decide(change, "approved", null);
      }),
    sendBack: (id, note) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy === me) throw new ProductTypesError("forbidden");
        if (!note.trim()) throw new ProductTypesError("note_required");
        return decide(change, "sent_back", note);
      }),
    withdraw: (id) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy !== me) throw new ProductTypesError("forbidden");
        return decide(change, "withdrawn", null);
      }),
  };
}
