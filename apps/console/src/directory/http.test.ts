import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpDirectory } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const PAGE = {
  account: { id: "-Nclient00000000000A", created_at: "2026-10-09T22:30:00Z" },
  verified_name: "Amara Nakato",
  identity_check: { status: "approved", account_number: "1001004827" },
};

describe("the directory over HTTP", () => {
  it("asks for each staff name once and answers null for someone unknown", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      reply(200, { people: String(url).includes("-Nstaff000000000000A") ? [{ id: "-Nstaff000000000000A", name: "Grace Auma" }] : [] }),
    );
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.staffName("-Nstaff000000000000A")).toBe("Grace Auma");
    expect(await directory.staffName("-Nstaff000000000000A")).toBe("Grace Auma");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/people?ids=-Nstaff000000000000A");
    expect(await directory.staffName("-Nstaff000000000000Z")).toBeNull();
  });

  it("answers null when a name cannot be read, and asks again next time", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => Promise.reject(new TypeError("offline")))
      .mockImplementationOnce(() => reply(200, { people: [{ id: "-Nstaff000000000000A", name: "Grace Auma" }] }));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.staffName("-Nstaff000000000000A")).toBeNull();
    expect(await directory.staffName("-Nstaff000000000000A")).toBe("Grace Auma");
  });

  it("lists the active staff and remembers their names", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { people: [{ id: "-Nstaff000000000000A", name: "Grace Auma" }] }));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.team()).toEqual([{ staffId: "-Nstaff000000000000A", name: "Grace Auma" }]);
    expect(await directory.staffName("-Nstaff000000000000A")).toBe("Grace Auma");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("builds a client card from the client page, with the Kampala day they joined", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, PAGE));
    const card = await createHttpDirectory(createHttp("https://api.test", fetch)).client("-Nclient00000000000A");
    expect(card).toEqual({
      clientId: "-Nclient00000000000A",
      name: "Amara Nakato",
      accountNumber: "1001004827",
      verified: true,
      clientSince: "2026-10-10",
    });
  });

  it("finds a client by search, then reads their card", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { clients: [PAGE.account] }))
      .mockImplementationOnce(() => reply(200, PAGE));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.findClients("1")).toEqual([]);
    const found = await directory.findClients("1001004827");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/clients/search?q=1001004827");
    expect(found.map((card) => card.name)).toEqual(["Amara Nakato"]);
  });
});
