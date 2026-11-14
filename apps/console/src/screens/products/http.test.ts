import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { CatalogueError, catalogueErrorMessage } from "./api.ts";
import { createHttpCatalogueApi } from "./http.ts";

const RATE = { effective_from: "2026-11-01", annual_rate_bps: 1250, tax_bps: 1500, client_rate_percent: "10.63" };

const PRODUCT = {
  id: "-NproductTREA91000001",
  code: "TREA-91",
  name: "91 Day Treasury Bill",
  kind: "treasury_bill",
  currency: "UGX",
  term_days: 91,
  minimum_minor: 100_000,
  interest_paid: "at_maturity",
  status: "live",
  rate: RATE,
  next_rate: null,
};

const PROPOSAL = {
  id: "-NproposalA000000001",
  product_code: "TREA-91",
  rate: { ...RATE, effective_from: "2026-12-01", annual_rate_bps: 1300 },
  reason: "Auction on 20 November cleared higher",
  status: "proposed",
  proposed_by: "-NstaffA000000000001",
  proposed_at: "2026-11-13T08:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, fields: { field_path: string[]; code: string }[] = []) {
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

describe("products and rates over HTTP", () => {
  it("reads products with their rates and minimum in the smallest unit", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [PRODUCT]));
    const [product] = await createHttpCatalogueApi(createHttp("https://api.test", fetch)).listProducts();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/products");
    expect(product).toEqual({
      id: PRODUCT.id,
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 91,
      minimum: { minor: 100_000, currency: "UGX" },
      interestPaid: "at_maturity",
      status: "live",
      rate: { effectiveFrom: "2026-11-01", annualRateBps: 1250, taxBps: 1500, clientRatePercent: "10.63" },
      nextRate: null,
    });
  });

  it("reads a product's rate history by its code", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [RATE]));
    const rates = await createHttpCatalogueApi(createHttp("https://api.test", fetch)).rateHistory("USD BOND");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/products/USD%20BOND/rates");
    expect(rates).toEqual([{ effectiveFrom: "2026-11-01", annualRateBps: 1250, taxBps: 1500, clientRatePercent: "10.63" }]);
  });

  it("asks for proposals in one status, or every status", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [PROPOSAL]));
    const api = createHttpCatalogueApi(createHttp("https://api.test", fetch));
    const [proposal] = await api.listProposals("proposed");
    await api.listProposals();
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/rate-proposals?status=proposed",
      "https://api.test/staff/rate-proposals",
    ]);
    expect(proposal).toMatchObject({ id: PROPOSAL.id, productCode: "TREA-91", proposedBy: PROPOSAL.proposed_by, decidedBy: null });
    expect(proposal?.rate.annualRateBps).toBe(1300);
  });

  it("proposes a rate with the backend's field names", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, PROPOSAL));
    await createHttpCatalogueApi(createHttp("https://api.test", fetch)).propose({
      productCode: "TREA-91",
      annualRateBps: 1300,
      taxBps: 1500,
      effectiveFrom: "2026-12-01",
      reason: "Auction on 20 November cleared higher",
    });
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe("https://api.test/staff/rate-proposals");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      product_code: "TREA-91",
      annual_rate_bps: 1300,
      tax_bps: 1500,
      effective_from: "2026-12-01",
      reason: "Auction on 20 November cleared higher",
    });
  });

  it("approves, sends back with a note, and withdraws at the proposal's own path", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, PROPOSAL));
    const api = createHttpCatalogueApi(createHttp("https://api.test", fetch));
    await api.approve(PROPOSAL.id);
    await api.sendBack(PROPOSAL.id, "Wait for the auction result");
    await api.withdraw(PROPOSAL.id);
    expect(fetch.mock.calls.map((call) => [call[0], call[1]?.method])).toEqual([
      [`https://api.test/staff/rate-proposals/${PROPOSAL.id}/approve`, "POST"],
      [`https://api.test/staff/rate-proposals/${PROPOSAL.id}/send-back`, "POST"],
      [`https://api.test/staff/rate-proposals/${PROPOSAL.id}/withdraw`, "POST"],
    ]);
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ note: "Wait for the auction result" });
  });

  it("approves after the person confirms it is them, sending the approval once more", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => refusal(403, "confirm_it_is_you"))
      .mockImplementationOnce(() => reply(200, { ...PROPOSAL, status: "approved" }));
    const confirm = vi.fn(() => Promise.resolve(true));
    const approved = await createHttpCatalogueApi(createHttp("https://api.test", fetch, confirm)).approve(PROPOSAL.id);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(approved.status).toBe("approved");
  });

  it("says nothing was approved when the person closes the confirm dialog", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(403, "confirm_it_is_you"));
    const api = createHttpCatalogueApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
    const error = await api.approve(PROPOSAL.id).catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code: "not_confirmed" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["reason_required", "reason"],
    ["effective_date_not_in_future", "effective_from"],
    ["date_already_taken", "effective_from"],
    ["nothing_changes", "annual_rate_bps"],
    ["out_of_range", "annual_rate_bps"],
  ])("names the refused field %s", async (code, field) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(422, "validation_failed", [{ field_path: [field], code }]));
    const error = await createHttpCatalogueApi(createHttp("https://api.test", fetch))
      .propose({ productCode: "TREA-91", annualRateBps: 1250, taxBps: 1500, effectiveFrom: "2026-11-01", reason: "" })
      .catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code });
  });

  it.each([
    [422, "validation_failed", "invalid"],
    [409, "conflict", "conflict"],
    [404, "not_found", "not_found"],
    [403, "forbidden", "forbidden"],
    [500, "internal_error", "unavailable"],
  ])("turns a %i %s into %s", async (status, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code));
    const error = await createHttpCatalogueApi(createHttp("https://api.test", fetch))
      .withdraw(PROPOSAL.id)
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(CatalogueError);
    expect(error).toMatchObject({ code: expected });
    expect(catalogueErrorMessage(error)).not.toBe("");
  });

  it("is unavailable when Finvest cannot be reached", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    const error = await createHttpCatalogueApi(createHttp("https://api.test", fetch))
      .listProducts()
      .catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code: "unavailable" });
  });
});
