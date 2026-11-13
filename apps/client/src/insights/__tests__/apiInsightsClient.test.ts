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
    const insights = await createApiInsightsClient().getInsights(6, "UGX");
    expect(urls).toEqual([`${API_URL}/client/insights?months=6&currency=UGX`]);
    expect(insights.currency).toBe("UGX");
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
    await expect(createApiInsightsClient().getInsights(6, "UGX")).rejects.toBeInstanceOf(InsightsError);
    setFetch(async () => new Response(JSON.stringify({ ...view, currency: "KES" }), { status: 200 }));
    await expect(createApiInsightsClient().getInsights(6, "UGX")).rejects.toBeInstanceOf(InsightsError);
  });

  it("asks for dollars on their own and keeps them in cents", async () => {
    const urls: string[] = [];
    setFetch(async (url) => {
      urls.push(String(url));
      return new Response(JSON.stringify({ ...view, currency: "USD", invested_now_minor: 50000 }), { status: 200 });
    });
    const insights = await createApiInsightsClient().getInsights(6, "USD");
    expect(urls).toEqual([`${API_URL}/client/insights?months=6&currency=USD`]);
    expect(insights.currency).toBe("USD");
    expect(insights.investedNow).toEqual({ minor: 50000, currency: "USD" });
  });

  it("refuses figures in a currency it did not ask for", async () => {
    setFetch(async () => new Response(JSON.stringify({ ...view, currency: "USD" }), { status: 200 }));
    await expect(createApiInsightsClient().getInsights(6, "UGX")).rejects.toBeInstanceOf(InsightsError);
  });

  it("lists dollars only when the client holds a dollar investment", async () => {
    const holding = (currency: string, status: string) => ({
      amount_minor: 1, at_maturity: null, can_change_maturity: false, client_rate_percent: "10.00", currency,
      days_earned: 0, id: "I".repeat(20), interest_at_maturity_minor: null, interest_so_far_minor: 0,
      interest_today_minor: 0, maturity_date: null, pay_by: "2026-11-14T12:00:00Z", payout_destination_id: null,
      payout_id: null, product_code: "P", product_name: "P", rolled_into_id: null, start_date: null, status,
      term_days: null, total_at_maturity_minor: null, value_minor: 0, waiting_for_destination: false,
      can_withdraw: false, withdrawal_requested_at: null,
    });
    const answer = (items: unknown[]) => setFetch(async () => new Response(JSON.stringify(items), { status: 200 }));
    const client = createApiInsightsClient();
    answer([]);
    await expect(client.heldCurrencies()).resolves.toEqual(["UGX"]);
    answer([holding("UGX", "active"), holding("USD", "cancelled")]);
    await expect(client.heldCurrencies()).resolves.toEqual(["UGX"]);
    answer([holding("USD", "awaiting_payment")]);
    await expect(client.heldCurrencies()).resolves.toEqual(["UGX", "USD"]);
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(client.heldCurrencies()).rejects.toBeInstanceOf(InsightsError);
  });
});
