import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { returnsErrorMessage } from "./api.ts";
import { createHttpReturnsApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const RETURN = {
  id: "-NreturnA00000000001",
  payout_id: "-NpayoutA00000000001",
  client_id: "-Nclient00000000000A",
  statement_line_id: "-NlineA0000000000001",
  reason: "Account closed",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T08:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
  ledger_entry_id: null,
  payout: {
    kind: "bank",
    amount: { minor: 1_200_000, currency: "UGX" },
    bank_name: "Stanbic Bank",
    network: null,
    masked_account: "•••• 7731",
    holder_name: "Joan Akello",
  },
  line: {
    id: "-NlineA0000000000001",
    value_date: "2026-11-13",
    amount: { minor: 1_200_000, currency: "UGX" },
    reference: "RTN 0412",
    narrative: "Returned: account closed",
  },
};

const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];
const api = (fetch: typeof globalThis.fetch) => createHttpReturnsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

describe("payout returns over HTTP", () => {
  it("reads returns waiting for a second person", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [RETURN, { ...RETURN, id: "-NreturnA00000000002", line: null }]));
    const [item, gone] = await api(fetch).listWaiting();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payout-returns");
    expect(item).toEqual({
      id: RETURN.id,
      payoutId: RETURN.payout_id,
      clientId: RETURN.client_id,
      statementLineId: RETURN.statement_line_id,
      reason: "Account closed",
      status: "proposed",
      proposedBy: RETURN.proposed_by,
      proposedAt: RETURN.proposed_at,
      payout: { kind: "bank", amount: RETURN.payout.amount, bankName: "Stanbic Bank", network: null, maskedAccount: "•••• 7731", holderName: "Joan Akello" },
      line: { id: RETURN.line.id, valueDate: "2026-11-13", amount: RETURN.line.amount, reference: "RTN 0412", narrative: "Returned: account closed" },
    });
    expect(gone?.line).toBeNull();
  });

  it("proposes and approves with a key, and declines or withdraws without one", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, RETURN));
    const returns = api(fetch);
    await returns.propose("key-1", RETURN.payout_id, RETURN.statement_line_id, "Account closed");
    await returns.approve("key-2", RETURN.id, "Opened the line");
    await returns.decline(RETURN.id, "Not this payout");
    await returns.withdraw(RETURN.id, "Wrong line");
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      `https://api.test/staff/payouts/${RETURN.payout_id}/returns`,
      `https://api.test/staff/payout-returns/${RETURN.id}/approve`,
      `https://api.test/staff/payout-returns/${RETURN.id}/decline`,
      `https://api.test/staff/payout-returns/${RETURN.id}/withdraw`,
    ]);
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ statement_line_id: RETURN.statement_line_id, reason: "Account closed" });
    expect(fetch.mock.calls.map((call) => header(call[1], "Idempotency-Key"))).toEqual(["key-1", "key-2", undefined, undefined]);
  });

  it("words the backend's named refusals", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(409, { request_id: "req", error: { code: "conflict", message: "x", fields: [{ field_path: ["body"], code: "return_already_recorded" }] } }),
    );
    const error = await api(fetch).propose("key", RETURN.payout_id, RETURN.statement_line_id, "x").catch((caught: unknown) => caught);
    expect(returnsErrorMessage(error)).toContain("A payout comes back once.");
  });

  it("reads the paid payouts by amount and the lines that could be one coming back", async () => {
    const payout = {
      id: "-NpayoutA00000000001",
      client_id: "-Nclient00000000000A",
      kind: "bank",
      amount: { minor: 2_750_000, currency: "UGX" },
      paid_at: "2026-11-12T09:00:00Z",
      bank_name: "Centenary Bank",
      network: null,
      masked_account: "•••• 4417",
      holder_name: "Amara Nakato",
    };
    const line = { id: "-NlineA0000000000001", value_date: "2026-11-13", amount: payout.amount, reference: "RTN 1", narrative: "Returned" };
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, [payout]))
      .mockImplementationOnce(() => reply(200, [line]));
    const api = createHttpReturnsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
    const [found] = await api.listReturnable({ minor: 2_750_000, currency: "UGX" });
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payout-returns/returnable?amount_minor=2750000&currency=UGX");
    expect(found).toMatchObject({ id: payout.id, kind: "bank", bankName: "Centenary Bank", maskedAccount: "•••• 4417", paidAt: payout.paid_at });
    const lines = await api.listReturnLines(payout.id);
    expect(fetch.mock.calls[1]?.[0]).toBe(`https://api.test/staff/payouts/${payout.id}/return-lines`);
    expect(lines).toEqual([{ id: line.id, valueDate: "2026-11-13", amount: payout.amount, reference: "RTN 1", narrative: "Returned" }]);
  });
});
