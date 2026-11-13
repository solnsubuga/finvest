import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiAutoinvestClient } from "../apiAutoinvestClient";
import { resolveAutoinvestClient } from "../resolveAutoinvest";
import { AutoinvestError, NewPlan } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

const PLAN = "A".repeat(20);

const plan = (overrides: Record<string, unknown> = {}) => ({
  plan_id: PLAN,
  product_code: "TB91",
  product_name: "91 Day Treasury Bill",
  amount_minor: 200_000,
  currency: "UGX",
  frequency: "monthly",
  day: 25,
  network: "mtn",
  payer_msisdn: "+256772123456",
  at_maturity: "roll_over",
  status: "active",
  next_run_on: "2026-11-25",
  paused_at: null,
  stopped_at: null,
  created_at: "2026-11-13T12:00:00Z",
  runs: [
    { run_id: "R".repeat(20), due_on: "2026-10-25", status: "failed", attempt: 2, code: "insufficient_funds", investment_id: null, retry_on: null },
  ],
  ...overrides,
});

const product = (overrides: Record<string, unknown> = {}) => ({
  code: "TB91",
  name: "91 Day Treasury Bill",
  currency: "UGX",
  kind: "treasury_bill",
  interest_paid: "at_maturity",
  minimum_minor: 100_000,
  client_rate_percent: "10.20",
  status: "live",
  term_days: 91,
  ...overrides,
});

type Seen = { method: string; url: string; headers: Record<string, string>; body: unknown };

function serve(routes: Record<string, () => Response>): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const method = init?.method ?? "GET";
    const path = String(url).slice(API_URL.length);
    seen.push({ method, url: path, headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body });
    const answer = routes[`${method} ${path}`];
    if (!answer) throw new Error(`unexpected ${method} ${path}`);
    return answer();
  });
  return seen;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });
const refused = (code: string, status: number, fields: unknown[] = []) =>
  json({ request_id: "r1", error: { code, message: code, fields } }, status);

const newPlan: NewPlan = {
  productCode: "TB91",
  amount: { minor: 200_000, currency: "UGX" },
  frequency: "monthly",
  day: 25,
  network: "mtn",
  payer: "+256772123456",
  atMaturity: "roll_over",
  idempotencyKey: "key-1",
};

describe("the automatic investing client for the real API", () => {
  it("offers live, fixed-term shilling products, with the client's own rate", async () => {
    serve({
      "GET /client/products": json([
        product(),
        product({ code: "USD-BOND", currency: "USD" }),
        product({ code: "OPEN", term_days: null, kind: "fixed_income" }),
        product({ code: "TB182", status: "paused", term_days: 182 }),
      ]),
    });
    await expect(createApiAutoinvestClient().listProducts()).resolves.toEqual([
      { code: "TB91", name: "91 Day Treasury Bill", minimum: { minor: 100_000, currency: "UGX" }, ratePercent: "10.20", termDays: 91 },
    ]);
  });

  it("lists plans and reads one, with its runs", async () => {
    serve({
      "GET /client/autoinvest/plans": json({ plans: [plan()] }),
      [`GET /client/autoinvest/plans/${PLAN}`]: json(plan()),
    });
    const client = createApiAutoinvestClient();
    const [listed] = await client.listPlans();
    expect(listed).toEqual({
      id: PLAN,
      product: { code: "TB91", name: "91 Day Treasury Bill" },
      amount: { minor: 200_000, currency: "UGX" },
      frequency: "monthly",
      day: 25,
      network: "mtn",
      payer: "+256772123456",
      atMaturity: "roll_over",
      status: "active",
      nextRunOn: "2026-11-25",
      runs: [{ id: "R".repeat(20), dueOn: "2026-10-25", status: "failed", attempt: 2, code: "insufficient_funds" }],
    });
    await expect(client.getPlan(PLAN)).resolves.toEqual(listed);
  });

  it("sets a plan up under the screen's key, without sending a number", async () => {
    const seen = serve({ "POST /client/autoinvest/plans": json(plan(), 201) });
    await createApiAutoinvestClient().createPlan(newPlan);
    expect(seen[0]!.headers["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(String(seen[0]!.body))).toEqual({
      product_code: "TB91",
      amount_minor: 200_000,
      currency: "UGX",
      frequency: "monthly",
      day: 25,
      network: "mtn",
      at_maturity: "roll_over",
    });
  });

  it("pauses, resumes and stops a plan", async () => {
    const seen = serve({
      [`POST /client/autoinvest/plans/${PLAN}/pause`]: json(plan({ status: "paused", next_run_on: null })),
      [`POST /client/autoinvest/plans/${PLAN}/resume`]: json(plan()),
      [`POST /client/autoinvest/plans/${PLAN}/stop`]: json(plan({ status: "stopped", next_run_on: null })),
    });
    const client = createApiAutoinvestClient();
    await expect(client.pausePlan(PLAN)).resolves.toMatchObject({ status: "paused", nextRunOn: null });
    await expect(client.resumePlan(PLAN)).resolves.toMatchObject({ status: "active" });
    await expect(client.stopPlan(PLAN)).resolves.toMatchObject({ status: "stopped" });
    expect(seen.map((call) => call.url)).toEqual([
      `/client/autoinvest/plans/${PLAN}/pause`,
      `/client/autoinvest/plans/${PLAN}/resume`,
      `/client/autoinvest/plans/${PLAN}/stop`,
    ]);
  });

  it.each([
    [refused("forbidden", 403), "not_verified"],
    [refused("conflict", 409), "too_many_plans"],
    [refused("idempotency_key_reused", 422), "key_reused"],
    [refused("validation_failed", 422, [{ field_path: ["amount"], code: "below_minimum" }]), "below_minimum"],
    [refused("validation_failed", 422, [{ field_path: ["amount"], code: "amount_too_large" }]), "amount_too_large"],
    [refused("validation_failed", 422, [{ field_path: ["day"], code: "invalid_day" }]), "invalid"],
  ])("refuses a new plan for the reason the API gave", async (answer, code) => {
    serve({ "POST /client/autoinvest/plans": answer });
    await expect(createApiAutoinvestClient().createPlan(newPlan)).rejects.toEqual(new AutoinvestError(code as never));
  });

  it("says a stopped plan cannot be changed, and a missing one cannot be found", async () => {
    serve({
      [`POST /client/autoinvest/plans/${PLAN}/resume`]: refused("conflict", 409),
      [`GET /client/autoinvest/plans/${PLAN}`]: refused("not_found", 404),
    });
    const client = createApiAutoinvestClient();
    await expect(client.resumePlan(PLAN)).rejects.toEqual(new AutoinvestError("stopped"));
    await expect(client.getPlan(PLAN)).rejects.toEqual(new AutoinvestError("not_found"));
  });

  it("fails as unavailable when offline or when the answer does not match the contract", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    const client = createApiAutoinvestClient();
    await expect(client.listPlans()).rejects.toEqual(new AutoinvestError("unavailable"));
    serve({ "GET /client/autoinvest/plans": json({ plans: [{ plan_id: PLAN }] }) });
    await expect(client.listPlans()).rejects.toEqual(new AutoinvestError("unavailable"));
  });

  it("talks to the API when the demo is not loaded", async () => {
    const seen = serve({ "GET /client/autoinvest/plans": json({ plans: [] }) });
    await expect(resolveAutoinvestClient("+256772123456", null).listPlans()).resolves.toEqual([]);
    expect(seen).toHaveLength(1);
  });
});
