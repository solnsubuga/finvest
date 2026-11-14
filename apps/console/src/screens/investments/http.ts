/**
 * Client investments and the daily interest run over the real API
 * (`/staff/investments`, `/staff/interest-runs` in backend/app/modules/investments/api.py).
 *
 * The list route answers one status at a time, newest first, up to 200. The screen counts each
 * status on its own tab, so the whole list is asked for status by status: a busy status never
 * pushes a quieter one out of a single shared page.
 */

import type { InterestRunOut, StaffInvestmentOut } from "@finvest/api-client/staff";
import { ApiError, type Http } from "@/api/http.ts";
import {
  INVESTMENT_STATUSES,
  InvestmentsError,
  type InterestRun,
  type Investment,
  type InvestmentsApi,
  type InvestmentStatus,
} from "./api.ts";

/** The most the list route gives at once. */
export const PAGE = 200;

export function toInvestment(json: StaffInvestmentOut): Investment {
  const amount = (minor: number) => ({ minor, currency: json.currency });
  return {
    id: json.id,
    clientId: json.client_id,
    productCode: json.product_code,
    productName: json.product_name,
    status: json.status,
    amount: amount(json.amount_minor),
    annualRateBps: json.annual_rate_bps,
    taxBps: json.tax_bps,
    termDays: json.term_days,
    atMaturity: json.at_maturity,
    createdAt: json.created_at,
    payBy: json.pay_by,
    startDate: json.start_date,
    maturityDate: json.maturity_date,
    accruedThrough: json.accrued_through,
    interestGross: amount(json.interest_gross_minor),
    tax: amount(json.tax_minor),
    interestNet: amount(json.interest_net_minor),
    rolledIntoId: json.rolled_into_id,
    rolledFromId: json.rolled_from_id,
    payoutId: json.payout_id,
    payoutRefusal: json.payout_refusal,
  };
}

export function toInterestRun(json: InterestRunOut): InterestRun {
  const amount = (minor: number) => ({ minor, currency: json.currency });
  return {
    runDate: json.run_date,
    currency: json.currency,
    investments: json.investments,
    gross: amount(json.gross_minor),
    tax: amount(json.tax_minor),
    net: amount(json.net_minor),
    behind: json.behind,
    startedAt: json.started_at,
    finishedAt: json.finished_at,
  };
}

export function toInvestmentsError(error: unknown): InvestmentsError {
  if (error instanceof InvestmentsError) return error;
  if (error instanceof ApiError && error.code === "forbidden") return new InvestmentsError("forbidden");
  return new InvestmentsError("unavailable");
}

export function createHttpInvestmentsApi(http: Http): InvestmentsApi {
  const page = (status: InvestmentStatus) =>
    http
      .request<StaffInvestmentOut[]>("GET", `/staff/investments?${new URLSearchParams({ status, limit: String(PAGE) }).toString()}`)
      .then((items) => items.map(toInvestment));

  return {
    async listInvestments(status) {
      try {
        if (status) return await page(status);
        const pages = await Promise.all(INVESTMENT_STATUSES.map(page));
        // Newest first across every status, as one page of one status already is.
        return pages.flat().sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      } catch (error) {
        throw toInvestmentsError(error);
      }
    },
    async interestRuns() {
      try {
        return (await http.request<InterestRunOut[]>("GET", "/staff/interest-runs")).map(toInterestRun);
      } catch (error) {
        throw toInvestmentsError(error);
      }
    },
  };
}

