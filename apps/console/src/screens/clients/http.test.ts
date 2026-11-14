import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { ClientsError } from "./api.ts";
import { createHttpClientsApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const ACCOUNT = {
  id: "-Nclient00000000000A",
  phone: "+256772123456",
  email: null,
  status: "active",
  created_at: "2026-08-03T07:12:00Z",
  restricted_until: null,
  has_passkey: true,
  has_authenticator: false,
  last_signed_in_at: null,
};

const LOCK = {
  id: "-Nlock0000000000000A",
  kind: "lock",
  client_id: ACCOUNT.id,
  reason: "Lost phone",
  proposed_by: "-Nstaff000000000000A",
  proposed_by_name: "Grace Auma",
  proposed_at: "2026-11-13T07:40:00Z",
  status: "proposed",
  decided_by: null,
  decided_by_name: null,
  decided_at: null,
  decision_reason: null,
  yours: false,
};

describe("clients over HTTP", () => {
  it("searches with the query in the URL and reads a client's page", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((input) =>
      String(input).includes("search")
        ? reply(200, { clients: [ACCOUNT] })
        : reply(200, {
            account: ACCOUNT,
            verified_name: "Sarah Nakato",
            identity_check: null,
            owed: [
              { account_code: "client.holding", account_reference: "TB-1", amount_minor: 2500000, currency: "UGX" },
              { account_code: "client.holding", account_reference: "X", amount_minor: 1, currency: "KES" },
            ],
            destinations: [],
            locks: [LOCK],
            freezes: [{ id: "-NFreeze000000000001", frozen_at: "2026-11-14T06:00:00Z", reason: null }],
            conversation: null,
            complaints: null,
            can_lock: true,
          }),
    );
    const api = createHttpClientsApi(createHttp("https://api.test", fetch));
    expect(await api.search(" 0772 123456 ")).toHaveLength(1);
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/clients/search?q=0772+123456", expect.objectContaining({ method: "GET" }));
    const page = await api.client(ACCOUNT.id);
    expect(page.owed).toEqual([{ accountCode: "client.holding", accountReference: "TB-1", minor: 2500000, currency: "UGX" }]);
    expect(page.locks[0]).toMatchObject({ proposedByName: "Grace Auma", yours: false });
    expect(page.freezes).toEqual([{ id: "-NFreeze000000000001", frozenAt: "2026-11-14T06:00:00Z", reason: null }]);
    expect(page.complaints).toBeNull();
  });

  it("sends a proposal and a decision in the backend's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, LOCK));
    const api = createHttpClientsApi(createHttp("https://api.test", fetch));
    await api.propose(ACCOUNT.id, "lock", "Lost phone");
    await api.decide(LOCK.id, "approved", "Checked");
    const [first, second] = fetch.mock.calls;
    expect(first?.[0]).toBe(`https://api.test/staff/clients/${ACCOUNT.id}/locks`);
    expect(JSON.parse(String(first?.[1]?.body))).toEqual({ kind: "lock", reason: "Lost phone" });
    expect(second?.[0]).toBe(`https://api.test/staff/clients/locks/${LOCK.id}/decision`);
    expect(JSON.parse(String(second?.[1]?.body))).toEqual({ decision: "approved", reason: "Checked" });
  });

  it("turns the API's refusals into the screen's errors", async () => {
    const answers = [
      reply(409, { error: { code: "conflict", fields: [{ field_path: ["body", "kind"], code: "already_locked" }] } }),
      reply(409, { error: { code: "conflict", fields: [] } }),
      reply(403, { error: { code: "forbidden", fields: [] } }),
      reply(404, { error: { code: "not_found", fields: [] } }),
    ];
    const fetch = vi.fn<typeof globalThis.fetch>(() => answers.shift() ?? Promise.reject(new TypeError("offline")));
    const api = createHttpClientsApi(createHttp("https://api.test", fetch));
    await expect(api.propose(ACCOUNT.id, "lock", "x")).rejects.toEqual(new ClientsError("already_locked"));
    await expect(api.propose(ACCOUNT.id, "lock", "x")).rejects.toMatchObject({ code: "waiting" });
    await expect(api.decide(LOCK.id, "approved", "x")).rejects.toMatchObject({ code: "same_person" });
    await expect(api.client("-Nnobody000000000000")).rejects.toMatchObject({ code: "not_found" });
    await expect(api.search("x")).rejects.toMatchObject({ code: "unavailable" });
  });
});
