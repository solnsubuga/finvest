/** The allocation report over the real API (backend/app/modules/dealing/allocations_api.py). */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount, Currency } from "./api.ts";
import { AllocationError, type AllocationReport, type AllocationsApi, type InvestmentBills } from "./allocations.ts";

interface LotJson {
  lot_id: string;
  settled_on: string;
  matures_on: string;
  custodian_reference: string;
  face: Amount;
  cost: Amount;
  allocated: Amount;
  unallocated: Amount;
  investments: number;
}

interface ProductJson {
  product_code: string;
  lots: LotJson[];
  cost_held: Amount;
  allocated: Amount;
  unallocated: Amount;
  investments: number;
  owed: Amount;
  covered: Amount;
  investments_waiting: number;
  waiting: Amount;
}

export interface AllocationReportJson {
  currency: Currency;
  on: string;
  products: ProductJson[];
}

export interface InvestmentBillsJson {
  investment_id: string;
  bills: { lot_id: string; product_code: string; matures_on: string; custodian_reference: string; cost: Amount }[];
  total: Amount | null;
}

export function toAllocationReport(json: AllocationReportJson): AllocationReport {
  return {
    currency: json.currency,
    on: json.on,
    products: json.products.map((p) => ({
      productCode: p.product_code,
      lots: p.lots.map((lot) => ({
        lotId: lot.lot_id,
        settledOn: lot.settled_on,
        maturesOn: lot.matures_on,
        custodianReference: lot.custodian_reference,
        face: lot.face,
        cost: lot.cost,
        allocated: lot.allocated,
        unallocated: lot.unallocated,
        investments: lot.investments,
      })),
      costHeld: p.cost_held,
      allocated: p.allocated,
      unallocated: p.unallocated,
      investments: p.investments,
      owed: p.owed,
      covered: p.covered,
      investmentsWaiting: p.investments_waiting,
      waiting: p.waiting,
    })),
  };
}

export function toInvestmentBills(json: InvestmentBillsJson): InvestmentBills {
  return {
    investmentId: json.investment_id,
    bills: json.bills.map((bill) => ({
      lotId: bill.lot_id,
      productCode: bill.product_code,
      maturesOn: bill.matures_on,
      custodianReference: bill.custodian_reference,
      cost: bill.cost,
    })),
    total: json.total,
  };
}

export function toAllocationError(error: unknown): AllocationError {
  if (error instanceof AllocationError) return error;
  if (!(error instanceof ApiError)) return new AllocationError("unavailable");
  if (error.code === "forbidden") return new AllocationError("forbidden");
  if (error.code === "validation_failed") return new AllocationError("invalid");
  return new AllocationError("unavailable");
}

export function createHttpAllocationsApi(http: Http): AllocationsApi {
  async function get<T>(path: string): Promise<T> {
    try {
      return await http.request<T>("GET", `/staff/dealing/allocations${path}`);
    } catch (error) {
      throw toAllocationError(error);
    }
  }
  return {
    getReport: async (currency) => toAllocationReport(await get<AllocationReportJson>(`?currency=${currency}`)),
    billsBehind: async (id) => toInvestmentBills(await get<InvestmentBillsJson>(`/investments/${encodeURIComponent(id)}`)),
  };
}
