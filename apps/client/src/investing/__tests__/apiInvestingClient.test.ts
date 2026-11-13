import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiInvestingClient } from "../apiInvestingClient";
import { resolveInvestingClient } from "../resolveInvesting";
import { InvestingError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ID = "IIIIIIIIIIIIIIIIIIII";
const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

const product = {
  code: "TREA-91",
  name: "91 Day Treasury Bill",
  kind: "treasury_bill",
  currency: "UGX",
  term_days: 91,
  minimum_minor: 100000,
  interest_paid: "at_maturity",
  status: "live",
  client_rate_percent: "10.63",
};

const quote = {
  product: "TREA-91",
  amount_minor: 1000000,
  currency: "UGX",
  starts_on: "2026-11-13",
  matures_on: "2027-02-12",
  interest_minor: 26490,
  total_minor: 1026490,
};

const investment = {
  id: ID,
  product_code: "TREA-91",
  product_name: "91 Day Treasury Bill",
  status: "active",
  currency: "UGX",
  amount_minor: 1000000,
  client_rate_percent: "10.63",
  term_days: 91,
  at_maturity: "roll_over",
  can_change_maturity: true,
  pay_by: "2026-11-16T09:00:00Z",
  start_date: "2026-11-13",
  maturity_date: "2027-02-12",
  days_earned: 1,
  interest_so_far_minor: 291,
  interest_today_minor: 291,
  value_minor: 1000291,
  interest_at_maturity_minor: 26490,
  total_at_maturity_minor: 1026490,
  rolled_into_id: null,
  payout_id: null,
  payout_destination_id: null,
  waiting_for_destination: false,
};

type Seen = { method: string; path: string; key: string | undefined; body: unknown };

function serve(status: number, body: unknown): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    seen.push({
      method: String(init?.method),
      path: String(url).replace(API_URL, ""),
      key: headers["Idempotency-Key"],
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields } };
}

afterEach(() => setFetch(null));

describe("the investing client for the real API", () => {
  it("lists products with the rate after tax, as sent", async () => {
    const seen = serve(200, [product, { ...product, code: "USD-BOND", currency: "USD", status: "paused", term_days: null }]);
    const products = await createApiInvestingClient().listProducts();
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/products" });
    expect(products[0]).toEqual({
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 91,
      minimum: ugx(100000),
      interestPaid: "at_maturity",
      status: "live",
      ratePercent: "10.63",
    });
    expect(products[1]).toMatchObject({ status: "paused", termDays: null, minimum: { currency: "USD", minor: 100000 } });
  });

  it("never treats a draft product as open", async () => {
    serve(200, [{ ...product, status: "draft" }]);
    expect((await createApiInvestingClient().listProducts())[0]!.status).toBe("paused");
  });

  it("quotes an amount by product and passes the net figures through", async () => {
    const seen = serve(200, quote);
    const result = await createApiInvestingClient().quote("TREA-91", ugx(1000000));
    expect(seen[0]!.path).toBe("/client/products/TREA-91/quote?amount_minor=1000000");
    expect(result).toEqual({
      productCode: "TREA-91",
      amount: ugx(1000000),
      startsOn: "2026-11-13",
      maturesOn: "2027-02-12",
      interest: ugx(26490),
      total: ugx(1026490),
    });
  });

  it("quotes an open term with no maturity figures", async () => {
    serve(200, { ...quote, matures_on: null, interest_minor: null, total_minor: null });
    expect(await createApiInvestingClient().quote("TREA-91", ugx(1000000))).toMatchObject({
      maturesOn: null,
      interest: null,
      total: null,
    });
  });

  it("refuses a quote that came back in another currency rather than mislabel it", async () => {
    serve(200, { ...quote, currency: "USD" });
    await expect(createApiInvestingClient().quote("TREA-91", ugx(1000000))).rejects.toEqual(
      new InvestingError("unavailable"),
    );
  });

  it("saves an investment under the screen's idempotency key", async () => {
    const seen = serve(201, { ...investment, status: "awaiting_payment", start_date: null });
    const saved = await createApiInvestingClient().requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1000000),
      atMaturity: "pay_out",
      idempotencyKey: "key-1",
    });
    expect(seen[0]).toEqual({
      method: "POST",
      path: "/client/investments",
      key: "key-1",
      body: { product_code: "TREA-91", amount_minor: 1000000, currency: "UGX", at_maturity: "pay_out" },
    });
    expect(saved).toMatchObject({ id: ID, status: "awaiting_payment", startDate: null });
  });

  it("sends no maturity choice for an open term", async () => {
    const seen = serve(201, investment);
    await createApiInvestingClient().requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1000000),
      atMaturity: null,
      idempotencyKey: "key-2",
    });
    expect(seen[0]!.body).toMatchObject({ at_maturity: null });
  });

  it("reads the portfolio and one investment with every figure as sent", async () => {
    const seen = serve(200, [investment]);
    const [first] = await createApiInvestingClient().listInvestments();
    expect(seen[0]!.path).toBe("/client/investments");
    expect(first).toEqual({
      id: ID,
      productCode: "TREA-91",
      productName: "91 Day Treasury Bill",
      status: "active",
      amount: ugx(1000000),
      ratePercent: "10.63",
      termDays: 91,
      atMaturity: "roll_over",
      canChangeMaturity: true,
      payBy: "2026-11-16T09:00:00Z",
      startDate: "2026-11-13",
      maturityDate: "2027-02-12",
      daysEarned: 1,
      interestSoFar: ugx(291),
      interestToday: ugx(291),
      value: ugx(1000291),
      interestAtMaturity: ugx(26490),
      totalAtMaturity: ugx(1026490),
      rolledIntoId: null,
      payoutId: null,
      waitingForDestination: false,
    });

    const one = serve(200, { ...investment, interest_at_maturity_minor: null, total_at_maturity_minor: null });
    expect(await createApiInvestingClient().getInvestment(ID)).toMatchObject({ interestAtMaturity: null, totalAtMaturity: null });
    expect(one[0]!.path).toBe(`/client/investments/${ID}`);
  });

  it("never passes on a tax rate", async () => {
    serve(200, [product]);
    expect(JSON.stringify(await createApiInvestingClient().listProducts())).not.toMatch(/tax|Bps|before/i);
  });

  it("turns refusals into the reasons the screens show", async () => {
    const client = createApiInvestingClient();
    const request = { productCode: "TREA-91", amount: ugx(1000000), atMaturity: null, idempotencyKey: "k" };

    serve(422, refusal("validation_failed", [{ field_path: ["amount"], code: "below_minimum" }]));
    await expect(client.quote("TREA-91", ugx(1))).rejects.toEqual(new InvestingError("below_minimum"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("below_minimum"));

    serve(422, refusal("validation_failed", [{ field_path: ["amount"], code: "product_not_open" }]));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("product_not_open"));

    serve(403, refusal("forbidden"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("not_verified"));

    serve(409, refusal("conflict"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("too_many_unpaid"));

    serve(422, refusal("idempotency_key_reused"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("key_reused"));

    serve(404, refusal("not_found"));
    await expect(client.getInvestment(ID)).rejects.toEqual(new InvestingError("not_found"));
  });

  it("treats an answer it cannot know as unavailable, so the review screen keeps its key", async () => {
    const request = { productCode: "TREA-91", amount: ugx(1000000), atMaturity: null, idempotencyKey: "k" };
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(createApiInvestingClient().requestInvestment(request)).rejects.toEqual(new InvestingError("unavailable"));

    serve(500, refusal("internal_error"));
    await expect(createApiInvestingClient().requestInvestment(request)).rejects.toEqual(new InvestingError("unavailable"));
  });
});

describe("choosing the investing client", () => {
  it("talks to the API when the demo is not switched on", async () => {
    const seen = serve(200, [product]);
    await resolveInvestingClient(null).listProducts();
    expect(seen[0]!.path).toBe("/client/products");
  });
});
