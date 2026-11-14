import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { createHttpAutoinvestApi } from "./http.ts";

const RUN = {
  run_id: "-NrunA0000000000002",
  plan_id: "-NplanA000000000001",
  client_id: "-Nclient00000000001",
  due_on: "2026-11-13",
  status: "failed",
  attempt: 1,
  retry_on: "2026-11-14",
  code: "payment_failed",
  investment_id: null,
  product_code: "TREA-91",
  product_name: "91 Day Treasury Bill",
  currency: "UGX",
  amount_minor: 200_000,
  frequency: "monthly",
  day: 13,
  network: "mtn",
};

const AMARA: ClientCard = {
  clientId: "-Nclient00000000001",
  name: "Amara Nakato",
  accountNumber: "1001004827",
  verified: true,
  clientSince: "2026-10-10",
  status: "active",
};

const directory: Directory = {
  client: (id) => Promise.resolve(id === AMARA.clientId ? AMARA : null),
  findClients: () => Promise.resolve([]),
  staffName: () => Promise.resolve(null),
  team: () => Promise.resolve([]),
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("automatic investing over HTTP", () => {
  it("reads the plan counts and the week's runs, naming each client", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).includes("/plans/counts")
        ? reply(200, { active: 12, paused: 3 })
        : reply(200, { runs: [RUN, { ...RUN, run_id: "-NrunA0000000000001", client_id: "-Nunknown0000000001", status: "paid", code: null, retry_on: null }], next_before_id: null }),
    );
    const overview = await createHttpAutoinvestApi(createHttp("https://api.test", fetch), directory).getOverview("2026-11-14");
    const urls = fetch.mock.calls.map(([url]) => String(url));
    expect(urls).toContain("https://api.test/staff/autoinvest/plans/counts");
    expect(urls).toContain("https://api.test/staff/autoinvest/runs?date_from=2026-11-08&date_to=2026-11-14&limit=200");
    expect(overview).toMatchObject({ plans: { active: 12, paused: 3 }, from: "2026-11-08", to: "2026-11-14" });
    expect(overview.runs[0]).toEqual({
      id: "-NrunA0000000000002",
      planId: "-NplanA000000000001",
      dueOn: "2026-11-13",
      status: "failed",
      attempt: 1,
      retryOn: "2026-11-14",
      code: "payment_failed",
      clientName: "Amara Nakato",
      accountNumber: "1001004827",
      productName: "91 Day Treasury Bill",
      amount: "200000",
      frequency: "monthly",
      day: 13,
      network: "mtn",
    });
    expect(overview.runs[1]).toMatchObject({ clientName: "A client", accountNumber: "No account number yet" });
  });

  it("reads a busy week page by page", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) => {
      const query = new URL(String(url)).searchParams;
      if (String(url).includes("/plans/counts")) return reply(200, { active: 1, paused: 0 });
      return query.get("before_id")
        ? reply(200, { runs: [{ ...RUN, run_id: "-NrunA0000000000001" }], next_before_id: null })
        : reply(200, { runs: [RUN], next_before_id: RUN.run_id });
    });
    const overview = await createHttpAutoinvestApi(createHttp("https://api.test", fetch), directory).getOverview("2026-11-14");
    expect(overview.runs.map((run) => run.id)).toEqual(["-NrunA0000000000002", "-NrunA0000000000001"]);
    expect(fetch.mock.calls.some(([url]) => String(url).includes(`before_id=${RUN.run_id}`))).toBe(true);
    expect(overview.cut).toBe(false);
  });

  it("says so when a week holds more runs than it reads", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).includes("/plans/counts") ? reply(200, { active: 1, paused: 0 }) : reply(200, { runs: [RUN], next_before_id: RUN.run_id }),
    );
    const overview = await createHttpAutoinvestApi(createHttp("https://api.test", fetch), directory).getOverview("2026-11-14");
    expect(overview.cut).toBe(true);
    // The counts, then fifty pages of runs and no more.
    expect(fetch).toHaveBeenCalledTimes(51);
  });

  it.each([
    [403, "forbidden", "forbidden"],
    [503, "unavailable", "unavailable"],
  ])("turns a %i into %s", async (status, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(status, { request_id: "r", error: { code, message: code, fields: [] } }));
    await expect(createHttpAutoinvestApi(createHttp("https://api.test", fetch), directory).getOverview("2026-11-14")).rejects.toMatchObject({ code: expected });
  });
});
