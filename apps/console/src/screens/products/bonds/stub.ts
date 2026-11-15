/**
 * Sample bond issues for `pnpm dev` and previews, from Bank of Uganda's 28 October 2026 auction
 * (`@finvest/market`). The prices are samples, not quotes. Not connected to Finvest: commands
 * change only this browser tab's copy, and the checks here stand in for the backend's.
 */

import type { ProposalStatus } from "../api.ts";
import { BondsError, usualTaxBps, type Bond, type BondChange, type BondsApi } from "./api.ts";

export interface BondsStubOptions {
  me: string;
  /** "empty" starts with no bonds. "offline" acts as if Finvest could not be reached. */
  sample?: "default" | "empty" | "offline";
}

const COLLEAGUE = "-StaffSampleFinance01";
const ugx = (minor: number) => ({ minor, currency: "UGX" as const });

function sampleBonds(): Bond[] {
  const bond = (code: string, isin: string, name: string, couponBps: number, issueDate: string, maturityDate: string, years: number, price: number | null, status: Bond["status"]): Bond => ({
    code,
    isin,
    name,
    currency: "UGX",
    couponBps,
    taxBps: usualTaxBps(issueDate, maturityDate),
    usualTaxBps: usualTaxBps(issueDate, maturityDate),
    issueDate,
    maturityDate,
    years,
    minimumFace: ugx(100_000),
    faceStep: ugx(100_000),
    status,
    price,
    pricedAt: price === null ? null : "2026-10-29T07:00:00Z",
  });
  return [
    bond("BOND-2Y-2028", "UG0000001376", "2 Year Treasury Bond 2028", 1525, "2026-05-16", "2028-11-16", 2, 987_120, "live"),
    bond("BOND-5Y-2032", "UG12J2005328", "5 Year Treasury Bond 2032", 1500, "2027-05-20", "2032-05-20", 5, 954_350, "live"),
    bond("BOND-25Y-2050", "UG12M0707507", "25 Year Treasury Bond 2050", 1600, "2025-07-07", "2050-07-07", 25, 875_600, "paused"),
  ];
}

export function createStubBondsApi(options: BondsStubOptions): BondsApi {
  if (options.sample === "offline") {
    const offline = () => Promise.reject(new BondsError("unavailable"));
    return { listBonds: offline, listChanges: offline, proposeBond: offline, proposePrice: offline, proposeStatus: offline, approve: offline, sendBack: offline, withdraw: offline };
  }
  const { me } = options;
  const bonds = options.sample === "empty" ? [] : sampleBonds();
  const changes: BondChange[] = [];
  let counter = 0;
  const clone = <T>(value: T): T => structuredClone(value);

  function add(proposedBy: string, change: Pick<BondChange, "action" | "code" | "terms" | "price" | "reason">, at: string): BondChange {
    counter += 1;
    const made: BondChange = { ...change, id: `-BondChangeSample${counter}`, status: "proposed", proposedBy, proposedAt: at, decidedBy: null, decidedAt: null, decisionNote: null };
    changes.unshift(made);
    return clone(made);
  }

  if (options.sample !== "empty") {
    add(
      COLLEAGUE,
      { action: "price", code: "BOND-2Y-2028", terms: null, price: 989_450, reason: "Primary dealer quote this morning: yields fell 10 basis points." },
      "2026-11-15T07:20:00Z",
    );
  }

  function bondFor(code: string): Bond {
    const found = bonds.find((b) => b.code === code);
    if (!found) throw new BondsError("not_found");
    return found;
  }

  function waiting(id: string): BondChange {
    const found = changes.find((c) => c.id === id);
    if (!found) throw new BondsError("not_found");
    if (found.status !== "proposed") throw new BondsError("conflict");
    return found;
  }

  function oneWaitingFor(code: string): void {
    if (changes.some((c) => c.status === "proposed" && c.code === code)) throw new BondsError("conflict");
  }

  function decide(change: BondChange, status: ProposalStatus, note: string | null): BondChange {
    change.status = status;
    change.decidedBy = me;
    change.decidedAt = new Date().toISOString();
    change.decisionNote = note;
    return clone(change);
  }

  function attempt<T>(work: () => T): Promise<T> {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new BondsError("unavailable"));
    }
  }

  const now = () => new Date().toISOString();

  return {
    listBonds: () => Promise.resolve(clone(bonds)),
    listChanges: (status) => Promise.resolve(clone(changes.filter((c) => !status || c.status === status))),
    proposeBond: (draft) =>
      attempt(() => {
        if (!draft.reason.trim()) throw new BondsError("reason_required");
        if (bonds.some((b) => b.code === draft.code) || changes.some((c) => c.status === "proposed" && c.code === draft.code)) throw new BondsError("code_taken");
        if (bonds.some((b) => b.isin === draft.isin) || changes.some((c) => c.status === "proposed" && c.terms?.isin === draft.isin)) throw new BondsError("isin_taken");
        if (draft.maturityDate <= draft.issueDate) throw new BondsError("invalid");
        return add(
          me,
          {
            action: "create",
            code: draft.code,
            price: draft.price,
            reason: draft.reason,
            terms: {
              isin: draft.isin,
              name: draft.name,
              currency: "UGX",
              couponBps: draft.couponBps,
              taxBps: draft.taxBps,
              issueDate: draft.issueDate,
              maturityDate: draft.maturityDate,
              minimumFace: ugx(draft.minimumFaceMinor),
              faceStep: ugx(draft.faceStepMinor),
            },
          },
          now(),
        );
      }),
    proposePrice: (code, price, reason) =>
      attempt(() => {
        if (!reason.trim()) throw new BondsError("reason_required");
        const bond = bondFor(code);
        oneWaitingFor(code);
        if (bond.price === price) throw new BondsError("same_price");
        return add(me, { action: "price", code, terms: null, price, reason }, now());
      }),
    proposeStatus: (code, to, reason) =>
      attempt(() => {
        if (!reason.trim()) throw new BondsError("reason_required");
        const bond = bondFor(code);
        oneWaitingFor(code);
        if (to === "paused" && bond.status !== "live") throw new BondsError("not_live");
        if (to === "live" && bond.status === "live") throw new BondsError("already_live");
        if (to === "live" && bond.price === null) throw new BondsError("no_price");
        return add(me, { action: to === "live" ? "open" : "pause", code, terms: null, price: null, reason }, now());
      }),
    approve: (id) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy === me) throw new BondsError("forbidden");
        if (change.action === "create" && change.terms && change.price !== null) {
          const t = change.terms;
          bonds.push({
            code: change.code,
            isin: t.isin,
            name: t.name,
            currency: t.currency,
            couponBps: t.couponBps,
            taxBps: t.taxBps,
            usualTaxBps: usualTaxBps(t.issueDate, t.maturityDate),
            issueDate: t.issueDate,
            maturityDate: t.maturityDate,
            years: Number(t.maturityDate.slice(0, 4)) - Number(t.issueDate.slice(0, 4)),
            minimumFace: t.minimumFace,
            faceStep: t.faceStep,
            status: "draft",
            price: change.price,
            pricedAt: now(),
          });
        } else {
          const bond = bondFor(change.code);
          if (change.action === "price" && change.price !== null) {
            bond.price = change.price;
            bond.pricedAt = now();
          }
          if (change.action === "open") bond.status = "live";
          if (change.action === "pause") bond.status = "paused";
        }
        return decide(change, "approved", null);
      }),
    sendBack: (id, note) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy === me) throw new BondsError("forbidden");
        return decide(change, "sent_back", note);
      }),
    withdraw: (id) =>
      attempt(() => {
        const change = waiting(id);
        if (change.proposedBy !== me) throw new BondsError("forbidden");
        return decide(change, "withdrawn", null);
      }),
  };
}
