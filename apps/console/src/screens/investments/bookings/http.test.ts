import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import type { CatalogueApi } from "../../products/api.ts";
import { createHttpBookingsApi } from "./http.ts";

const BOOKING = {
  id: "-Nbook0000000000001",
  client_id: "-Nclient00000000001",
  product_code: "TREA-91",
  product_name: "91-day Treasury bill",
  amount_minor: 1_000_000,
  currency: "UGX",
  at_maturity: "roll_over",
  reason: "Called the office",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T07:40:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
  investment_id: null,
  failure_code: null,
  finished_at: null,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

const catalogue = { listProducts: () => Promise.resolve([]) } as unknown as CatalogueApi;
const refused = (code: string, fields: unknown[] = []) => ({ request_id: "r", error: { code, message: code, fields } });

describe("investments: bookings over the API", () => {
  it("lists them in the console's shape", async () => {
    const fetchImpl = respond(200, [BOOKING]);
    const api = createHttpBookingsApi(createHttp("https://api.example", fetchImpl), catalogue);
    const [first] = await api.list();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/investment-bookings?limit=50");
    expect(first).toMatchObject({ clientId: "-Nclient00000000001", amount: { minor: 1_000_000, currency: "UGX" }, atMaturity: "roll_over" });
  });

  it("books with the key, and decides on each booking's own route", async () => {
    const fetchImpl = respond(201, BOOKING);
    const api = createHttpBookingsApi(createHttp("https://api.example", fetchImpl), catalogue);
    await api.propose("key-1", {
      clientId: "-Nclient00000000001",
      productCode: "TREA-91",
      amount: { minor: 1_000_000, currency: "UGX" },
      atMaturity: "roll_over",
      reason: "Called",
    });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/investment-bookings");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("key-1");
    expect(JSON.parse(String(init?.body))).toEqual({
      client_id: "-Nclient00000000001",
      product_code: "TREA-91",
      amount_minor: 1_000_000,
      currency: "UGX",
      at_maturity: "roll_over",
      reason: "Called",
    });
    await api.sendBack("key-2", BOOKING.id, "Wrong product");
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(`https://api.example/staff/investment-bookings/${BOOKING.id}/send-back`);
  });

  it("names the field Finvest refused, and a decision by the booker", async () => {
    const api = createHttpBookingsApi(
      createHttp("https://api.example", respond(422, refused("validation_failed", [{ field_path: ["amount"], code: "below_minimum" }]))),
      catalogue,
    );
    await expect(
      api.propose("k", { clientId: "c", productCode: "TREA-91", amount: { minor: 1, currency: "UGX" }, atMaturity: null, reason: "r" }),
    ).rejects.toMatchObject({ code: "invalid", problems: [{ field: "amount", code: "below_minimum" }] });
    const own = createHttpBookingsApi(createHttp("https://api.example", respond(403, refused("forbidden"))), catalogue);
    await expect(own.approve("k", BOOKING.id)).rejects.toMatchObject({ code: "same_person" });
  });
});
