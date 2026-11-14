import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpInvestmentsApi } from "./http.ts";

const INVESTMENT = {
  id: "-NinvestA00000000001",
  client_id: "-Nclient00000000000A",
  product_code: "TREA-91",
  product_name: "91 Day Treasury Bill",
  status: "active",
  amount_minor: 5_000_000,
  currency: "UGX",
  annual_rate_bps: 1250,
  tax_bps: 1500,
  term_days: 91,
  at_maturity: "roll_over",
  created_at: "2026-08-19T09:00:00Z",
  pay_by: "2026-08-20T09:00:00Z",
  start_date: "2026-08-20",
  maturity_date: "2026-11-19",
  accrued_through: "2026-11-13",
  interest_gross_minor: 145_521,
  tax_minor: 21_825,
  interest_net_minor: 123_696,
  rolled_into_id: null,
  rolled_from_id: null,
  payout_id: null,
  payout_refusal: null,
};

const RUN = {
  run_date: "2026-11-13",
  currency: "USD",
  investments: 4,
  gross_minor: 3_626,
  tax_minor: 181,
  net_minor: 3_445,
  behind: 0,
  started_at: "2026-11-12T21:30:00Z",
  finished_at: "2026-11-12T21:31:00Z",
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("investments over HTTP", () => {
  it("reads one status with the largest page, every amount in the investment's currency", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [INVESTMENT]));
    const [investment] = await createHttpInvestmentsApi(createHttp("https://api.test", fetch)).listInvestments("active");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/investments?status=active&limit=200");
    expect(investment).toMatchObject({
      id: INVESTMENT.id,
      clientId: INVESTMENT.client_id,
      productName: "91 Day Treasury Bill",
      amount: { minor: 5_000_000, currency: "UGX" },
      interestGross: { minor: 145_521, currency: "UGX" },
      tax: { minor: 21_825, currency: "UGX" },
      interestNet: { minor: 123_696, currency: "UGX" },
      maturityDate: "2026-11-19",
      accruedThrough: "2026-11-13",
      atMaturity: "roll_over",
      payoutRefusal: null,
    });
  });

  it("asks for every status in turn and puts the newest first", async () => {
    const older = { ...INVESTMENT, id: "-NinvestOlder0000001", status: "matured", created_at: "2026-05-01T09:00:00Z" };
    const newer = { ...INVESTMENT, id: "-NinvestNewer0000001", status: "awaiting_payment", created_at: "2026-11-13T09:00:00Z" };
    const fetch = vi.fn<typeof globalThis.fetch>((url) => {
      const status = new URL(String(url)).searchParams.get("status");
      return reply(200, status === "matured" ? [older] : status === "awaiting_payment" ? [newer] : status === "active" ? [INVESTMENT] : []);
    });
    const all = await createHttpInvestmentsApi(createHttp("https://api.test", fetch)).listInvestments();
    expect(fetch.mock.calls.map((call) => new URL(String(call[0])).searchParams.get("status")).sort()).toEqual([
      "active",
      "awaiting_payment",
      "cancelled",
      "matured",
      "unwound",
      "withdrawn",
    ]);
    expect(all.map((investment) => investment.id)).toEqual([newer.id, INVESTMENT.id, older.id]);
  });

  it("reads the interest runs, each in its own currency", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [RUN]));
    const [run] = await createHttpInvestmentsApi(createHttp("https://api.test", fetch)).interestRuns();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/interest-runs");
    expect(run).toEqual({
      runDate: "2026-11-13",
      currency: "USD",
      investments: 4,
      gross: { minor: 3_626, currency: "USD" },
      tax: { minor: 181, currency: "USD" },
      net: { minor: 3_445, currency: "USD" },
      behind: 0,
      startedAt: RUN.started_at,
      finishedAt: RUN.finished_at,
    });
  });

  it("says when the person may not see investments, and fails the whole list if one status fails", async () => {
    const forbidden = vi.fn<typeof globalThis.fetch>(() => reply(403, { request_id: "r", error: { code: "forbidden", message: "", fields: [] } }));
    expect(await createHttpInvestmentsApi(createHttp("https://api.test", forbidden)).interestRuns().catch((e: unknown) => e)).toMatchObject({
      code: "forbidden",
    });
    const oneDown = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).includes("status=matured") ? Promise.reject(new TypeError("offline")) : reply(200, []),
    );
    expect(await createHttpInvestmentsApi(createHttp("https://api.test", oneDown)).listInvestments().catch((e: unknown) => e)).toMatchObject({
      code: "unavailable",
    });
  });
});
