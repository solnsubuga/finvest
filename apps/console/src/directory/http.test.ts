import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpDirectory } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const CARD = {
  id: "-Nclient00000000000A",
  verified_name: "NAKATO Amara",
  account_number: "1001004827",
  status: "active",
  created_at: "2026-10-09T22:30:00Z",
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

  it("builds a client card, with the Kampala day they joined", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { cards: [CARD] }));
    const card = await createHttpDirectory(createHttp("https://api.test", fetch)).client(CARD.id);
    expect(fetch.mock.calls[0]?.[0]).toBe(`https://api.test/staff/clients/cards?ids=${CARD.id}`);
    expect(card).toEqual({
      clientId: CARD.id,
      name: "NAKATO Amara",
      accountNumber: "1001004827",
      verified: true,
      clientSince: "2026-10-10",
    });
  });

  it("asks for every client wanted at the same moment in one request, and remembers them", async () => {
    const other = { ...CARD, id: "-Nclient00000000000B", verified_name: null, account_number: null };
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { cards: [CARD, other] }));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    const cards = await Promise.all([directory.client(CARD.id), directory.client(other.id), directory.client("-Nclient00000000000Z")]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(fetch.mock.calls[0]?.[0])).toBe(
      `https://api.test/staff/clients/cards?ids=${CARD.id}&ids=${other.id}&ids=-Nclient00000000000Z`,
    );
    expect(cards.map((card) => card?.verified ?? null)).toEqual([true, false, null]);
    expect(cards[1]?.name).toBe("Name not yet verified");
    await directory.client(CARD.id);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("answers null when cards cannot be read, and asks again next time", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => Promise.reject(new TypeError("offline")))
      .mockImplementationOnce(() => reply(200, { cards: [CARD] }));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.client(CARD.id)).toBeNull();
    expect((await directory.client(CARD.id))?.name).toBe("NAKATO Amara");
  });

  it("finds a client by search, then reads their card", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { clients: [{ id: CARD.id }] }))
      .mockImplementationOnce(() => reply(200, { cards: [CARD] }));
    const directory = createHttpDirectory(createHttp("https://api.test", fetch));
    expect(await directory.findClients("1")).toEqual([]);
    const found = await directory.findClients("1001004827");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/clients/search?q=1001004827");
    expect(found.map((card) => card.name)).toEqual(["NAKATO Amara"]);
  });
});
