import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiInsightsClient } from "../apiInsightsClient";
import { InsightsError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

const view = {
  currency: "UGX",
  months: [{ month: "2026-11-01", invested_minor: 500000, earned_minor: 4200, paid_out_minor: 0 }],
  invested_now_minor: 500000,
  earned_so_far_minor: 4200,
  upcoming: [
    { on: "2027-02-12", investment_id: "I".repeat(20), product_name: "91 Day Treasury Bill", at_maturity: "roll_over", amount_minor: 513000 },
  ],
};

describe("the insights client for the real API", () => {
  it("asks for the months and passes the after-tax figures through", async () => {
    const urls: string[] = [];
    setFetch(async (url) => {
      urls.push(String(url));
      return new Response(JSON.stringify(view), { status: 200 });
    });
    const insights = await createApiInsightsClient().getInsights(6);
    expect(urls).toEqual([`${API_URL}/client/insights?months=6`]);
    expect(insights.months[0]).toEqual({
      month: "2026-11-01",
      invested: { minor: 500000, currency: "UGX" },
      earned: { minor: 4200, currency: "UGX" },
      paidOut: { minor: 0, currency: "UGX" },
    });
    expect(insights.upcoming[0]).toMatchObject({ on: "2027-02-12", atMaturity: "roll_over", amount: { minor: 513000 } });
  });

  it("fails as one error the screen can retry, offline or not", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(createApiInsightsClient().getInsights(6)).rejects.toBeInstanceOf(InsightsError);
    setFetch(async () => new Response(JSON.stringify({ ...view, currency: "KES" }), { status: 200 }));
    await expect(createApiInsightsClient().getInsights(6)).rejects.toBeInstanceOf(InsightsError);
  });

  it("refuses figures in a currency the chart cannot label", async () => {
    setFetch(async () => new Response(JSON.stringify({ ...view, currency: "USD" }), { status: 200 }));
    await expect(createApiInsightsClient().getInsights(6)).rejects.toBeInstanceOf(InsightsError);
  });
});
