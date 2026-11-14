import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { potTransfersErrorMessage } from "./api.ts";
import { createHttpPotTransfersApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const TRANSFER = {
  id: "-NtransferA000000001",
  from_pot: { source: "iotec", account_reference: "mtn", name: "ioTec wallet, MTN" },
  to_pot: { source: "bank", account_reference: "trust", name: "Trust bank account" },
  sent: { minor: 48_000_000, currency: "UGX" },
  received: { minor: 47_985_000, currency: "UGX" },
  fee: { minor: 15_000, currency: "UGX" },
  value_date: "2026-11-13",
  provider_reference: "IOT-1",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T08:00:00Z",
  decided_by: null,
  ledger_entry_id: null,
};

const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];
const api = (fetch: typeof globalThis.fetch) => createHttpPotTransfersApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

describe("transfers between pots over HTTP", () => {
  it("reads the pots and the transfers waiting", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).endsWith("/pots")
        ? reply(200, [{ source: "bank", account_reference: "trust", name: "Trust bank account", currency: "UGX", last_statement_date: null, last_closing: null }])
        : reply(200, [TRANSFER]),
    );
    const transfers = api(fetch);
    expect(await transfers.listPots()).toEqual([{ source: "bank", accountReference: "trust", name: "Trust bank account", currency: "UGX" }]);
    const [item] = await transfers.listWaiting();
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/reconciliation/pots",
      "https://api.test/staff/reconciliation/pot-transfers",
    ]);
    expect(item).toMatchObject({ fromPot: { accountReference: "mtn" }, fee: { minor: 15_000 }, valueDate: "2026-11-13", providerReference: "IOT-1" });
  });

  it("records, approves and declines with an idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, TRANSFER));
    const transfers = api(fetch);
    await transfers.record("key-1", {
      from: { source: "iotec", accountReference: "mtn" },
      to: { source: "bank", accountReference: "trust" },
      sent: { minor: 48_000_000, currency: "UGX" },
      received: { minor: 47_985_000, currency: "UGX" },
      valueDate: "2026-11-13",
      providerReference: "IOT-1",
      reason: "Sweep",
    });
    await transfers.approve("key-2", TRANSFER.id);
    await transfers.decline("key-3", TRANSFER.id, "Not on the statement");
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({
      from_pot: { source: "iotec", account_reference: "mtn" },
      to_pot: { source: "bank", account_reference: "trust" },
      sent: { minor: 48_000_000, currency: "UGX" },
      received: { minor: 47_985_000, currency: "UGX" },
      value_date: "2026-11-13",
      provider_reference: "IOT-1",
      reason: "Sweep",
    });
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/reconciliation/pot-transfers",
      `https://api.test/staff/reconciliation/pot-transfers/${TRANSFER.id}/approve`,
      `https://api.test/staff/reconciliation/pot-transfers/${TRANSFER.id}/decline`,
    ]);
    expect(fetch.mock.calls.map((call) => header(call[1], "Idempotency-Key"))).toEqual(["key-1", "key-2", "key-3"]);
    expect(JSON.parse(String(fetch.mock.calls[2]?.[1]?.body))).toEqual({ reason: "Not on the statement" });
  });

  it("words the backend's named refusals", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(409, { request_id: "r", error: { code: "conflict", message: "x", fields: [{ field_path: ["body"], code: "transfer_more_than_the_pot_holds" }] } }),
    );
    const error = await api(fetch).approve("key", TRANSFER.id).catch((caught: unknown) => caught);
    expect(potTransfersErrorMessage(error)).toContain("never held that much");
  });
});
