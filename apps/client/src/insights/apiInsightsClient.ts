import type { InsightsView } from "@finvest/api-client/client";

import { api } from "@/api/http";
import { Money } from "@/payments/types";

import { Currency, Insights, InsightsClient, InsightsError, Upcoming } from "./types";

/** The Insights screen's contract, answered by `GET /client/insights`. Every amount is after tax. */

/** The figures, which must be in the currency asked for. Anything else is not understood. */
export function toInsights(view: InsightsView, asked: Currency): Insights {
  if (view.currency !== asked) throw new InsightsError();
  const currency = asked;
  const money = (minor: number): Money => ({ minor, currency });
  return {
    currency,
    months: view.months.map((month) => ({
      month: month.month,
      invested: money(month.invested_minor),
      earned: money(month.earned_minor),
      paidOut: money(month.paid_out_minor),
    })),
    investedNow: money(view.invested_now_minor),
    earnedSoFar: money(view.earned_so_far_minor),
    upcoming: view.upcoming.flatMap((item): Upcoming[] => {
      const amount = money(item.amount_minor);
      switch (item.kind) {
        case "maturity":
          return item.investment_id && item.product_name
            ? [
                {
                  kind: "maturity",
                  on: item.on,
                  investmentId: item.investment_id,
                  productName: item.product_name,
                  atMaturity: item.at_maturity,
                  amount,
                },
              ]
            : [];
        case "autoinvest_run":
        case "goal_saving":
          return item.plan_id && item.product_name
            ? [
                {
                  kind: item.kind,
                  on: item.on,
                  planId: item.plan_id,
                  productName: item.product_name,
                  goalId: item.goal_id,
                  goalName: item.goal_name,
                  amount,
                },
              ]
            : [];
        case "goal_date":
          return item.goal_id && item.goal_name
            ? [{ kind: "goal_date", on: item.on, goalId: item.goal_id, goalName: item.goal_name, amount }]
            : [];
      }
    }),
  };
}

export function createApiInsightsClient(): InsightsClient {
  return {
    async getInsights(months, currency) {
      try {
        return toInsights(await api("GET /client/insights", { query: { months, currency } }), currency);
      } catch {
        // The screen offers "Try again" for every failure: nothing here changes anything.
        throw new InsightsError();
      }
    },

    async heldCurrencies() {
      try {
        const investments = await api("GET /client/investments", {});
        const held = new Set(investments.filter((item) => item.status !== "cancelled").map((item) => item.currency));
        return held.has("USD") ? ["UGX", "USD"] : ["UGX"];
      } catch {
        throw new InsightsError();
      }
    },
  };
}
