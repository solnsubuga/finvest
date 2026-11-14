import { Currency, Insights, InsightsClient, InsightsError } from "./types";

/**
 * An in-memory stand-in for `GET /client/insights`. Nothing here is real.
 * `resolveInsights.ts` keeps it out of production builds.
 *
 * The two demo accounts have a few months of history, with figures from the
 * design canvas. An account made by signing up has none yet, which shows the
 * empty state. A number ending in 99 cannot reach the service. The demo holds no
 * dollars, so its dollar figures are all zero. Its coming up list names the demo goal and
 * automatic plan the goals and automatic investing stand-ins hold.
 */

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;

// Oldest first, ending with this month. Whole shillings.
const EARNED = [0, 15_400, 29_800, 28_800, 61_800, 31_408];
const INVESTED = [0, 1_000_000, 3_000_000, 0, 4_250_000, 250_000];
const PAID_OUT = [0, 0, 0, 1_015_400, 0, 0];

function inCurrency(currency: Currency) {
  return (minor: number) => ({ currency, minor });
}

function monthsEnding(now: Date, count: number): string[] {
  const kampala = new Date(now.getTime() + KAMPALA_OFFSET_MS);
  const months: string[] = [];
  for (let back = count - 1; back >= 0; back -= 1) {
    const start = new Date(Date.UTC(kampala.getUTCFullYear(), kampala.getUTCMonth() - back, 1));
    months.push(start.toISOString().slice(0, 10));
  }
  return months;
}

function addDays(now: Date, days: number): string {
  return new Date(now.getTime() + KAMPALA_OFFSET_MS + days * 86_400_000).toISOString().slice(0, 10);
}

export function createStubInsightsClient(
  phone: string | null,
  now: () => Date = () => new Date(),
): InsightsClient {
  return {
    async heldCurrencies() {
      if (!phone || phone.endsWith("99")) {
        throw new InsightsError();
      }
      return ["UGX"];
    },

    async getInsights(count, currency) {
      await new Promise((resolve) => setTimeout(resolve, 150));
      if (!phone || phone.endsWith("99")) {
        throw new InsightsError();
      }
      const ugx = inCurrency(currency);
      const seeded = SEEDED.has(phone) && currency === "UGX";
      const months = monthsEnding(now(), count);
      const pick = (series: number[], index: number) =>
        ugx(seeded ? (series[series.length - months.length + index] ?? 0) : 0);
      const insights: Insights = {
        currency,
        months: months.map((month, index) => ({
          month,
          invested: pick(INVESTED, index),
          earned: pick(EARNED, index),
          paidOut: pick(PAID_OUT, index),
        })),
        investedNow: ugx(seeded ? 8_250_000 : 0),
        earnedSoFar: ugx(seeded ? 57_281 : 0),
        upcoming: seeded
          ? [
              {
                kind: "goal_saving",
                on: addDays(now(), 6),
                planId: "demo-plan-goal",
                productName: "91 Day Treasury Bill",
                goalId: "demo-goal-1",
                goalName: "School fees, Term 1",
                amount: ugx(150_000),
              },
              {
                kind: "autoinvest_run",
                on: addDays(now(), 13),
                planId: `demo-plan-${phone.slice(-3)}`,
                productName: "91 Day Treasury Bill",
                goalId: null,
                goalName: null,
                amount: ugx(250_000),
              },
              {
                kind: "goal_date",
                on: addDays(now(), 49),
                goalId: "demo-goal-1",
                goalName: "School fees, Term 1",
                amount: ugx(2_000_000),
              },
              {
                kind: "maturity",
                on: addDays(now(), 57),
                investmentId: "demo-investment-1",
                productName: "91 Day Treasury Bill",
                atMaturity: "roll_over",
                amount: ugx(5_132_449),
              },
              {
                kind: "maturity",
                on: addDays(now(), 62),
                investmentId: "demo-investment-2",
                productName: "182 Day Treasury Bill",
                atMaturity: "pay_out",
                amount: ugx(3_174_832),
              },
            ]
          : [],
      };
      return insights;
    },
  };
}
