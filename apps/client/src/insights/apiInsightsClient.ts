import type { InsightsView } from "@finvest/api-client/client";

import { api } from "@/api/http";
import { Money } from "@/payments/types";

import { Insights, InsightsClient, InsightsError } from "./types";

/** The Insights screen's contract, answered by `GET /client/insights`. Every amount is after tax. */

function currencyOf(view: InsightsView): Money["currency"] {
  // The API answers in shillings unless asked otherwise, and the chart's labels are
  // thousands of shillings. Any other currency is an answer this screen cannot show.
  if (view.currency === "UGX") return view.currency;
  throw new InsightsError();
}

export function toInsights(view: InsightsView): Insights {
  const currency = currencyOf(view);
  const money = (minor: number): Money => ({ minor, currency });
  return {
    months: view.months.map((month) => ({
      month: month.month,
      invested: money(month.invested_minor),
      earned: money(month.earned_minor),
      paidOut: money(month.paid_out_minor),
    })),
    investedNow: money(view.invested_now_minor),
    earnedSoFar: money(view.earned_so_far_minor),
    upcoming: view.upcoming.map((item) => ({
      on: item.on,
      investmentId: item.investment_id,
      productName: item.product_name,
      atMaturity: item.at_maturity,
      amount: money(item.amount_minor),
    })),
  };
}

export function createApiInsightsClient(): InsightsClient {
  return {
    async getInsights(months) {
      try {
        return toInsights(await api("GET /client/insights", { query: { months } }));
      } catch {
        // The screen offers "Try again" for every failure: nothing here changes anything.
        throw new InsightsError();
      }
    },
  };
}
