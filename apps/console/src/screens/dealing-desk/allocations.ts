/**
 * The console's view of the allocation report: which bills stand behind client investments.
 *
 * The shapes follow backend/app/modules/dealing/allocations_api.py and docs/dealing-desk.md.
 * The report only reads. Every figure comes from the backend, which brings allocations up to
 * date every 15 minutes. The console adds nothing up.
 */

import type { Amount, Currency } from "./api.ts";

export interface AllocatedLot {
  lotId: string;
  settledOn: string;
  maturesOn: string;
  custodianReference: string;
  /** What is still held of the purchase. */
  face: Amount;
  cost: Amount;
  allocated: Amount;
  unallocated: Amount;
  investments: number;
}

export interface ProductAllocation {
  productCode: string;
  /** Oldest purchase first. */
  lots: readonly AllocatedLot[];
  costHeld: Amount;
  allocated: Amount;
  unallocated: Amount;
  /** Running investments, what they are owed, and the part with no bill behind it yet. */
  investments: number;
  owed: Amount;
  covered: Amount;
  investmentsWaiting: number;
  waiting: Amount;
}

export interface AllocationReport {
  currency: Currency;
  /** The business day the report is for. */
  on: string;
  products: readonly ProductAllocation[];
}

export interface BillBehind {
  lotId: string;
  productCode: string;
  maturesOn: string;
  custodianReference: string;
  cost: Amount;
}

export interface InvestmentBills {
  investmentId: string;
  bills: readonly BillBehind[];
  /** Null when no bill stands behind it. */
  total: Amount | null;
}

export interface AllocationsApi {
  getReport(currency: Currency): Promise<AllocationReport>;
  billsBehind(investmentId: string): Promise<InvestmentBills>;
}

export type AllocationErrorCode = "forbidden" | "invalid" | "not_connected" | "unavailable";

export class AllocationError extends Error {
  readonly code: AllocationErrorCode;

  constructor(code: AllocationErrorCode) {
    super(code);
    this.name = "AllocationError";
    this.code = code;
  }
}

const MESSAGES: Record<AllocationErrorCode, string> = {
  forbidden: "Your role cannot see the dealing desk.",
  invalid: "Enter an investment ID as it appears on the investment.",
  not_connected: "The allocation report is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function allocationErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof AllocationError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new AllocationError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedAllocationsApi: AllocationsApi = { getReport: notConnected, billsBehind: notConnected };
