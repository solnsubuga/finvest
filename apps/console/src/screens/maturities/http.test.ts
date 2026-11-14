import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpMaturitiesApi } from "./http.ts";

const HELD = {
  id: "-NinvHeld0000000001",
  client_id: "-Nclient00000000001",
  product_code: "TREA-91",
  product_name: "91 Day Treasury Bill",
  status: "matured",
  amount_minor: 1_000_000,
  currency: "UGX",
  annual_rate_bps: 1250,
  tax_bps: 1500,
  term_days: 91,
  at_maturity: "pay_out",
  created_at: "2026-08-12T08:00:00Z",
  pay_by: "2026-08-12T08:00:00Z",
  start_date: "2026-08-12",
  maturity_date: "2026-11-11",
  accrued_through: "2026-11-11",
  interest_gross_minor: 31_164,
  tax_minor: 4_674,
  interest_net_minor: 26_490,
  rolled_into_id: null,
  rolled_from_id: null,
  payout_id: null,
  payout_refusal: "no_destination",
};

const BODY = {
  today: "2026-11-14",
  days: [
    {
      day: "2026-11-11",
      currency: "USD",
      investments: 2,
      principal_minor: 1_250_000,
      rolled_over: 0,
      paid_out: 2,
      held: 0,
      open_roll_over: 0,
      open_pay_out: 0,
    },
  ],
  not_closed: [],
  held: [HELD],
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("maturities over HTTP", () => {
  it("reads the day counts and the held investments into the screen's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, BODY));
    const found = await createHttpMaturitiesApi(createHttp("https://api.test", fetch)).getMaturities();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/maturities");
    expect(found.today).toBe("2026-11-14");
    expect(found.days).toEqual([
      {
        day: "2026-11-11",
        currency: "USD",
        investments: 2,
        principal: { minor: 1_250_000, currency: "USD" },
        rolledOver: 0,
        paidOut: 2,
        held: 0,
        openRollOver: 0,
        openPayOut: 0,
      },
    ]);
    expect(found.held[0]).toMatchObject({ clientId: "-Nclient00000000001", payoutRefusal: "no_destination", interestNet: { minor: 26_490, currency: "UGX" } });
  });

  it.each([
    [403, "forbidden", "forbidden"],
    [503, "unavailable", "unavailable"],
  ])("turns a %i into %s", async (status, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(status, { request_id: "r", error: { code, message: code, fields: [] } }));
    await expect(createHttpMaturitiesApi(createHttp("https://api.test", fetch)).getMaturities()).rejects.toMatchObject({ code: expected });
  });
});
