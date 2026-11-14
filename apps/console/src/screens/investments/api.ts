/**
 * The console's view of client investments and the daily interest run.
 *
 * `http.ts` calls `/staff/investments` and `/staff/interest-runs` in
 * backend/app/modules/investments/api.py; `stub.ts` stands in for them under
 * `pnpm dev` and previews. Staff see interest before tax and
 * the tax withheld. Every figure comes from the backend: the screen adds
 * nothing up.
 */

import type { Amount, Currency } from "../products/api.ts";

export type { Amount, Currency } from "../products/api.ts";

/** `unwound`: undone by two people after a mistake, see "Undoing a mistake" in docs/investing.md. */
export type InvestmentStatus = "awaiting_payment" | "active" | "matured" | "withdrawn" | "unwound" | "cancelled";

/** Every status, so a list of all investments can ask for each in turn. */
export const INVESTMENT_STATUSES: readonly InvestmentStatus[] = ["awaiting_payment", "active", "matured", "withdrawn", "unwound", "cancelled"];
export type AtMaturity = "roll_over" | "pay_out";

export interface Investment {
  id: string;
  clientId: string;
  productCode: string;
  productName: string;
  status: InvestmentStatus;
  amount: Amount;
  annualRateBps: number;
  taxBps: number;
  termDays: number | null;
  atMaturity: AtMaturity | null;
  createdAt: string;
  payBy: string;
  startDate: string | null;
  maturityDate: string | null;
  /** The last day interest has been posted for. */
  accruedThrough: string | null;
  interestGross: Amount;
  tax: Amount;
  interestNet: Amount;
  rolledIntoId: string | null;
  rolledFromId: string | null;
  payoutId: string | null;
  /** Why matured money is waiting in payouts instead of on its way. */
  payoutRefusal: string | null;
}

/** One day's run for one currency. */
export interface InterestRun {
  runDate: string;
  currency: Currency;
  investments: number;
  gross: Amount;
  tax: Amount;
  net: Amount;
  /** Investments the run could not bring up to date. They are retried by the next run. */
  behind: number;
  startedAt: string;
  finishedAt: string | null;
}

export interface InvestmentsApi {
  /** Newest first, at most 200 of each status. */
  listInvestments(status?: InvestmentStatus): Promise<readonly Investment[]>;
  /** Newest run first. */
  interestRuns(): Promise<readonly InterestRun[]>;
}

export type InvestmentsErrorCode = "forbidden" | "not_connected" | "unavailable";

export class InvestmentsError extends Error {
  readonly code: InvestmentsErrorCode;

  constructor(code: InvestmentsErrorCode) {
    super(code);
    this.name = "InvestmentsError";
    this.code = code;
  }
}

const MESSAGES: Record<InvestmentsErrorCode, string> = {
  forbidden: "You are not allowed to see client investments.",
  not_connected: "Investments are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function investmentsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof InvestmentsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new InvestmentsError("not_connected"));

export const notConnectedInvestmentsApi: InvestmentsApi = {
  listInvestments: notConnected,
  interestRuns: notConnected,
};
