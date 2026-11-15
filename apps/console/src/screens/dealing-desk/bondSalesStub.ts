/**
 * Sample bond sales for `pnpm dev` and tests. Follows the backend's rules closely enough to try
 * the screen: a sale is priced by one person and decided by another, and the figures of a sold
 * one are the backend's worked example in docs/bonds.md.
 */

import { BondSaleError, type BondSale, type BondSalesApi, type SaleStep } from "./bondSales.ts";

export const SALES_COLLEAGUE = { id: "-StaffColleague00001", name: "Grace Atim" };

const UGX = (minor: number) => ({ minor, currency: "UGX" as const });

function base(id: string, clientName: string, requestedAt: string): BondSale {
  return {
    id,
    holdingId: `${id}-holding`,
    clientId: `${id}-client`,
    clientName,
    code: "BOND-10Y-2037",
    name: "10 Year Treasury Bond 2037",
    currency: "UGX",
    face: UGX(1_000_000),
    cost: UGX(985_883),
    requestedAt,
    status: "requested",
    price: null,
    soldOn: null,
    reason: null,
    pricedBy: null,
    pricedAt: null,
    decidedBy: null,
    decidedAt: null,
    note: null,
    clean: null,
    accrued: null,
    tax: null,
    amount: null,
    soldAt: null,
    payoutStatus: null,
    heldBecause: null,
    allowed: [],
  };
}

function samples(me: { id: string; name: string }): BondSale[] {
  return [
    base("-SaleRequested000001", "Sarah Nakato", "2027-02-14T07:12:00Z"),
    {
      ...base("-SalePricedColleague", "Brian Okello", "2027-02-13T10:40:00Z"),
      status: "priced",
      price: 972_500,
      soldOn: "2027-02-14",
      reason: "Desk trade 4471 with Stanbic",
      pricedBy: SALES_COLLEAGUE.name,
      pricedAt: "2027-02-14T09:05:00Z",
    },
    {
      ...base("-SalePricedByMe00001", "Joan Achieng", "2027-02-12T15:20:00Z"),
      face: UGX(500_000),
      cost: UGX(512_000),
      status: "priced",
      price: 1_001_250,
      soldOn: "2027-02-14",
      reason: "Desk trade 4472 with Absa",
      pricedBy: me.name,
      pricedAt: "2027-02-14T09:30:00Z",
    },
    {
      ...base("-SaleSold00000000001", "Peter Mugisha", "2027-02-10T08:00:00Z"),
      status: "sold",
      price: 972_500,
      soldOn: "2027-02-14",
      reason: "Desk trade 4460 with Stanbic",
      pricedBy: SALES_COLLEAGUE.name,
      pricedAt: "2027-02-14T08:15:00Z",
      decidedBy: me.name,
      decidedAt: "2027-02-14T08:40:00Z",
      clean: UGX(972_500),
      accrued: UGX(40_662),
      tax: UGX(4_066),
      amount: UGX(1_009_096),
      soldAt: "2027-02-14T08:41:00Z",
      payoutStatus: "paid_out",
    },
  ];
}

function steps(sale: BondSale, me: string): readonly SaleStep[] {
  if (sale.status === "requested") return ["price"];
  if (sale.status === "priced") return sale.pricedBy === me ? ["withdraw"] : ["approve", "send_back"];
  return [];
}

export function createStubBondSalesApi(me: { id: string; name: string }, sample: "sample" | "empty" = "sample"): BondSalesApi {
  let sales = sample === "empty" ? [] : samples(me);
  const shown = (sale: BondSale): BondSale => ({ ...sale, allowed: steps(sale, me.name) });
  function update(id: string, change: (sale: BondSale) => BondSale): Promise<BondSale> {
    const found = sales.find((s) => s.id === id);
    if (!found) return Promise.reject(new BondSaleError("not_found"));
    let next: BondSale;
    try {
      next = change(found);
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new BondSaleError("unavailable"));
    }
    sales = sales.map((s) => (s.id === id ? next : s));
    return Promise.resolve(shown(next));
  }
  const now = () => new Date().toISOString();
  return {
    list: () => Promise.resolve(sales.map(shown)),
    price: (id, { price, soldOn, reason }) =>
      update(id, (sale) => {
        if (sale.status !== "requested") throw new BondSaleError("conflict");
        return { ...sale, status: "priced", price, soldOn, reason, pricedBy: me.name, pricedAt: now() };
      }),
    approve: (id) =>
      update(id, (sale) => {
        if (sale.status !== "priced") throw new BondSaleError("conflict");
        if (sale.pricedBy === me.name) throw new BondSaleError("forbidden");
        return { ...sale, status: "approved", decidedBy: me.name, decidedAt: now() };
      }),
    sendBack: (id, note) =>
      update(id, (sale) => {
        if (sale.status !== "priced") throw new BondSaleError("conflict");
        if (sale.pricedBy === me.name) throw new BondSaleError("forbidden");
        return { ...sale, status: "requested", price: null, soldOn: null, reason: null, pricedBy: null, pricedAt: null, note };
      }),
    withdraw: (id) =>
      update(id, (sale) => {
        if (sale.status !== "priced") throw new BondSaleError("conflict");
        if (sale.pricedBy !== me.name) throw new BondSaleError("forbidden");
        return { ...sale, status: "requested", price: null, soldOn: null, reason: null, pricedBy: null, pricedAt: null };
      }),
  };
}
