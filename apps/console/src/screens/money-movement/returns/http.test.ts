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
};

const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];
const api = (fetch: typeof globalThis.fetch) => createHttpReturnsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

describe("payout returns over HTTP", () => {
  it("reads returns waiting for a second person", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [RETURN]));
    const [item] = await api(fetch).listWaiting();
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
    });
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
});
