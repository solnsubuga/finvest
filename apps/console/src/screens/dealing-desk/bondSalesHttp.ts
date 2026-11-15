/** Bond sales over the real API (`/staff/bond-sales` in backend/app/modules/bonds/api.py). */

import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import type { StaffBondSaleOut } from "@finvest/api-client/staff";
import type { Amount } from "./api.ts";
import { BondSaleError, type BondSale, type BondSaleErrorCode, type BondSalesApi } from "./bondSales.ts";

const PATH = "/staff/bond-sales";

export async function toBondSales(rows: readonly StaffBondSaleOut[], directory: Directory): Promise<BondSale[]> {
  const staff = new Set<string>();
  const clients = new Set<string>();
  for (const row of rows) {
    clients.add(row.client_id);
    for (const id of [row.priced_by, row.decided_by]) if (id) staff.add(id);
  }
  const names = new Map<string, string>();
  await Promise.all([
    ...[...staff].map(async (id) => {
      names.set(id, (await directory.staffName(id).catch(() => null)) ?? id);
    }),
    ...[...clients].map(async (id) => {
      names.set(id, (await directory.client(id).catch(() => null))?.name ?? id);
    }),
  ]);
  const named = (id: string | null) => (id ? (names.get(id) ?? id) : null);
  return rows.map((row) => {
    const amount = (minor: number | null): Amount | null => (minor === null ? null : { minor, currency: row.currency });
    return {
      id: row.id,
      holdingId: row.holding_id,
      clientId: row.client_id,
      clientName: named(row.client_id) ?? row.client_id,
      code: row.code,
      name: row.name,
      currency: row.currency,
      face: { minor: row.face_minor, currency: row.currency },
      cost: { minor: row.cost_minor, currency: row.currency },
      requestedAt: row.requested_at,
      status: row.status,
      price: row.price,
      soldOn: row.sold_on,
      reason: row.reason,
      pricedBy: named(row.priced_by),
      pricedAt: row.priced_at,
      decidedBy: named(row.decided_by),
      decidedAt: row.decided_at,
      note: row.note,
      clean: amount(row.clean_minor),
      accrued: amount(row.accrued_minor),
      tax: amount(row.tax_minor),
      amount: amount(row.amount_minor),
      soldAt: row.sold_at,
      payoutStatus: row.payout_status === "paid_out" || row.payout_status === "held" ? row.payout_status : null,
      heldBecause: row.held_because,
      allowed: row.allowed,
    };
  });
}

const FIELD_CODES = new Set<BondSaleErrorCode>([
  "sold_on_in_future",
  "sold_on_before_start",
  "sold_on_at_maturity",
  "coupon_since_sale",
  "price_out_of_range",
]);

export function toBondSaleError(error: unknown): BondSaleError {
  if (error instanceof BondSaleError) return error;
  if (!(error instanceof ApiError)) return new BondSaleError("unavailable");
  const field = error.fieldCode;
  if (field && FIELD_CODES.has(field as BondSaleErrorCode)) return new BondSaleError(field as BondSaleErrorCode);
  switch (error.code) {
    case "conflict":
    case "not_found":
    case "forbidden":
      return new BondSaleError(error.code);
    case "validation_failed":
      return new BondSaleError("invalid");
    // Includes the person closing the "confirm it is you" dialog: nothing was done, and they can press again.
    default:
      return new BondSaleError("unavailable");
  }
}

export function createHttpBondSalesApi(http: Http, directory: Directory): BondSalesApi {
  async function one(path: string, body?: unknown): Promise<BondSale> {
    let row: StaffBondSaleOut;
    try {
      row = await http.request<StaffBondSaleOut>("POST", path, body);
    } catch (error) {
      throw toBondSaleError(error);
    }
    const [sale] = await toBondSales([row], directory);
    if (!sale) throw new BondSaleError("unavailable");
    return sale;
  }
  const step = (id: string, name: string) => `${PATH}/${encodeURIComponent(id)}/${name}`;
  return {
    list: async () => {
      let rows: StaffBondSaleOut[];
      try {
        rows = await http.request<StaffBondSaleOut[]>("GET", PATH);
      } catch (error) {
        throw toBondSaleError(error);
      }
      return toBondSales(rows, directory);
    },
    price: (id, { price, soldOn, reason }) => one(step(id, "price"), { price, sold_on: soldOn, reason }),
    approve: (id) => one(step(id, "approve")),
    sendBack: (id, note) => one(step(id, "send-back"), { note }),
    withdraw: (id) => one(step(id, "withdraw")),
  };
}
