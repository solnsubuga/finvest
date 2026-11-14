import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import type { ClientPage, ClientsApi } from "../clients/api.ts";
import { notConnectedCorrectionsApi } from "../investments/corrections/api.ts";
import { createHttpHeldActionsApi } from "./actions.ts";

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

const destination = (id: string, status: string, kind = "mobile_money", network: string | null = "airtel") => ({
  id,
  kind,
  network,
  bankCode: kind === "bank" ? "stanbic" : null,
  maskedAccount: "•••• 4471",
  holderName: "NAMUTEBI Joan",
  status,
  createdAt: "2026-11-01T08:00:00Z",
});

const clients = (destinations: ReturnType<typeof destination>[]): ClientsApi => ({
  search: () => Promise.resolve([]),
  client: () => Promise.resolve({ destinations } as unknown as ClientPage),
  propose: () => Promise.reject(new Error("unused")),
  decide: () => Promise.reject(new Error("unused")),
});

describe("maturities: held money actions over the API", () => {
  it("offers only the client's approved payout accounts", async () => {
    const api = createHttpHeldActionsApi(
      createHttp("https://api.example", respond(200, {})),
      clients([destination("-D1", "approved"), destination("-D2", "in_review"), destination("-D3", "approved", "bank", null)]),
      notConnectedCorrectionsApi,
    );
    expect(await api.payoutAccounts("-Nclient")).toEqual([
      { id: "-D1", label: "Airtel Money", maskedAccount: "•••• 4471", holderName: "NAMUTEBI Joan" },
      { id: "-D3", label: "Bank (STANBIC)", maskedAccount: "•••• 4471", holderName: "NAMUTEBI Joan" },
    ]);
  });

  it("writes to the client's chat with the key in the body, about a withdrawal", async () => {
    const fetchImpl = respond(201, {});
    const api = createHttpHeldActionsApi(createHttp("https://api.example", fetchImpl), clients([]), notConnectedCorrectionsApi);
    await api.message("key-1", "-Nclient/1", "Your money is ready.");
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/support/clients/-Nclient%2F1/messages");
    expect(JSON.parse(String(init?.body))).toEqual({ body: "Your money is ready.", key: "key-1", topic: "withdrawal" });
  });

  it("maps refusals of a message", async () => {
    const cases: [number, string, string][] = [
      [404, "not_found", "not_found"],
      [422, "validation_failed", "invalid"],
      [403, "forbidden", "forbidden"],
      [500, "internal", "unavailable"],
    ];
    for (const [status, backend, code] of cases) {
      const body = { request_id: "r", error: { code: backend, message: backend, fields: [] } };
      const api = createHttpHeldActionsApi(createHttp("https://api.example", respond(status, body)), clients([]), notConnectedCorrectionsApi);
      await expect(api.message("k", "-Nclient", "Hi")).rejects.toMatchObject({ code });
    }
  });
});
