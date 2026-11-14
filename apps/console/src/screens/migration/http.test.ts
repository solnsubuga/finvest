import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import { migrationErrorMessage } from "./api.ts";
import { createHttpMigrationApi, type OverviewJson } from "./http.ts";

const ME = "-Nstaff0000000000ME";
const GRACE = "-Nstaff000000000GRA";
const DANIEL = "-Nstaff000000000DAN";

const OVERVIEW: OverviewJson = {
  trials_run: 2,
  trial: {
    id: "-Ntrial00000000001",
    as_of: "2026-11-12",
    ran_by: ME,
    ran_at: "2026-11-13T07:00:00Z",
    error_count: 0,
    warning_count: 1,
    problems: [{ sheet: "clients", row: 77, column: "phone", code: "phone_missing", severity: "warning" }],
  },
  totals: {
    id: "-Ntotals0000000001",
    as_of: "2026-11-12",
    entered_by: GRACE,
    entered_at: "2026-11-13T06:00:00Z",
    confirmed_by: DANIEL,
    clients: 1912,
    verified_identities: 1804,
    running_investments: 3204,
    pending_orders: 14,
    invested: [{ currency: "UGX", minor: 18240000000 }],
    interest_before_tax: [{ currency: "USD", minor: 61270 }],
  },
  lines: [
    { key: "total:clients", figure: "clients", currency: null, old: 1912, new: 1909, agrees: false },
    { key: "total:invested:UGX", figure: "invested", currency: "UGX", old: 18240000000, new: 18240000000, agrees: true },
  ],
  differences: [
    {
      key: "total:clients",
      figures: [1912, 1909],
      sign_off: {
        id: "-Nsignoff000000001",
        key: "total:clients",
        figures: [1912, 1909],
        reason: "Three closed accounts",
        proposed_by: GRACE,
        proposed_at: "2026-11-13T06:30:00Z",
        state: "proposed",
        decided_by: null,
        decision_reason: null,
      },
    },
    { key: "investment:FV-1042", figures: [120000, 118000, 18000, 17700], sign_off: null },
    {
      key: "tax_rate:FV-2001",
      figures: [1000, 1500],
      sign_off: {
        id: "-Nsignoff000000002",
        key: "tax_rate:FV-2001",
        figures: [1000, 1500],
        reason: "Old rate",
        proposed_by: ME,
        proposed_at: "2026-11-13T06:40:00Z",
        state: "rejected",
        decided_by: DANIEL,
        decision_reason: "Ask URA first",
      },
    },
  ],
  blockers: ["difference_not_signed_off"],
  claims: null,
  allowed: ["run_trial", "enter_totals", "sign_off"],
};

const NAMES: Record<string, string> = { [GRACE]: "Grace Finance" };

const directory: Directory = {
  client: () => Promise.resolve(null),
  findClients: () => Promise.resolve([]),
  staffName: (id) => Promise.resolve(NAMES[id] ?? null),
  team: () => Promise.resolve([]),
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string) {
  return reply(status, { request_id: "req", error: { code, message: code, fields: [] } });
}

function apiWith(fetch: typeof globalThis.fetch) {
  return createHttpMigrationApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)), directory, ME);
}

describe("migration over HTTP", () => {
  it("reads the overview into the screen's shape, with names and whose entry is whose", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, OVERVIEW));
    const overview = await apiWith(fetch).getOverview();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/migration");
    expect(overview.trial?.ranBy).toBe("You");
    expect(overview.totals).toMatchObject({
      enteredBy: "Grace Finance",
      // A name the directory cannot find is never shown as an ID.
      confirmedBy: "A colleague",
      mine: false,
      clients: "1912",
      invested: { UGX: "18240000000" },
      interestBeforeTax: { USD: "61270" },
    });
    expect(overview.lines[0]).toMatchObject({ key: "total:clients", old: "1912", new: "1909" });
    expect(overview.lines[0]?.signedOff).toMatchObject({ proposedBy: "Grace Finance", mine: false, state: "proposed" });
    expect(overview.lines[1]?.signedOff).toBeNull();
    // Only investments are differences on the screen. Total lines carry their own sign-off.
    expect(overview.differences).toEqual([
      {
        key: "investment:FV-1042",
        kind: "interest",
        row: null,
        legacyInvestmentRef: "FV-1042",
        currency: null,
        old: "120000",
        new: "118000",
        signedOff: null,
      },
      expect.objectContaining({
        key: "tax_rate:FV-2001",
        kind: "tax_rate",
        old: "1000",
        new: "1500",
        signedOff: expect.objectContaining({ mine: true, decidedBy: "A colleague", decisionReason: "Ask URA first" }),
      }),
    ]);
  });

  it("sends a trial's sheets as text, and refuses one too large before sending", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, OVERVIEW));
    const api = apiWith(fetch);
    // jsdom's File has no text(), so a stand-in with the two things the API reads.
    const sheet = (text: string) => ({ size: text.length, text: () => Promise.resolve(text) }) as unknown as File;
    await api.runTrial({ clients: sheet("a,b\n"), investments: sheet("c,d\n"), asOf: "2026-11-12" });
    const init = fetch.mock.calls[0]?.[1];
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/migration/trials");
    expect(JSON.parse(String(init?.body))).toEqual({ as_of: "2026-11-12", clients_csv: "a,b\n", investments_csv: "c,d\n" });

    const big = { size: 40_000_001, text: () => Promise.resolve("") } as unknown as File;
    await expect(api.runTrial({ clients: big, investments: big, asOf: "2026-11-12" })).rejects.toMatchObject({ code: "file_too_large" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("enters totals as whole numbers, leaving out a currency with none", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, OVERVIEW));
    await apiWith(fetch).enterTotals({
      asOf: "2026-11-12",
      clients: "1912",
      verifiedIdentities: "1804",
      runningInvestments: "3204",
      pendingOrders: "14",
      invested: { UGX: "18240000000" },
      interestBeforeTax: {},
    });
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("https://api.test/staff/migration/control-totals");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({
      as_of: "2026-11-12",
      clients: 1912,
      verified_identities: 1804,
      running_investments: 3204,
      pending_orders: 14,
      invested: [{ currency: "UGX", minor: 18240000000 }],
      interest_before_tax: [],
    });
  });

  it("refuses a figure too large to send exactly", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, OVERVIEW));
    const totals = {
      asOf: "2026-11-12",
      clients: "9007199254740993",
      verifiedIdentities: "0",
      runningInvestments: "0",
      pendingOrders: "0",
      invested: {},
      interestBeforeTax: {},
    };
    await expect(apiWith(fetch).enterTotals(totals)).rejects.toMatchObject({ code: "invalid" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reads the overview again after a sign-off is proposed or decided", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).endsWith("/staff/migration") ? reply(200, OVERVIEW) : reply(201, OVERVIEW.differences[0]!.sign_off),
    );
    const api = apiWith(fetch);
    await api.proposeSignOff("investment:FV-1042", "Rounding");
    await api.decideSignOff("-Nsignoff000000001", false, "Check again");
    const calls = fetch.mock.calls.map(([url, init]) => [String(url), init?.method, init?.body ? JSON.parse(String(init.body)) : null]);
    expect(calls).toEqual([
      ["https://api.test/staff/migration/sign-offs", "POST", { key: "investment:FV-1042", reason: "Rounding" }],
      ["https://api.test/staff/migration", "GET", null],
      ["https://api.test/staff/migration/sign-offs/-Nsignoff000000001/decision", "POST", { approve: false, reason: "Check again" }],
      ["https://api.test/staff/migration", "GET", null],
    ]);
  });

  it("says the change was saved when only the read after it fails", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).endsWith("/staff/migration") ? Promise.reject(new TypeError("offline")) : reply(200, {}),
    );
    await expect(apiWith(fetch).decideSignOff("-Nsignoff000000001", true)).rejects.toMatchObject({ code: "stale" });
  });

  it.each([
    [409, "conflict", "conflict"],
    [404, "not_found", "conflict"],
    [403, "forbidden", "forbidden"],
    [422, "validation_failed", "invalid"],
    [413, "payload_too_large", "file_too_large"],
    [503, "unavailable", "unavailable"],
  ])("turns a %i %s into %s", async (status, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code));
    const error = await apiWith(fetch).confirmTotals("-Ntotals0000000001").catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code: expected });
    expect(migrationErrorMessage(error)).not.toBe("");
  });

  it("does nothing when the person closes the confirm-it-is-you dialog", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(403, "confirm_it_is_you"));
    const error = await apiWith(fetch).confirmTotals("-Ntotals0000000001").catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code: "not_confirmed" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("reads the switch and the clients it could not carry over, with whose proposal is whose", async () => {
    const json: OverviewJson = {
      ...OVERVIEW,
      switch: {
        id: "-Nswitch0000000001",
        trial_id: "-Ntrial00000000001",
        as_of: "2026-11-12",
        proposed_by: ME,
        proposed_at: "2026-11-14T05:00:00Z",
        state: "approved",
        decided_by: GRACE,
        decision_reason: null,
        clients_carried: 1912,
        not_carried: [
          {
            legacy_client_ref: "C-1",
            code: "document_taken",
            attempt: 2,
            noted_at: "2026-11-14T06:00:00Z",
            state: "close_proposed",
            resolution_id: "-Nresolution000001",
            proposed_by: DANIEL,
            reason: "Same person",
          },
          { legacy_client_ref: "C-2", code: "refused", attempt: 1, noted_at: "2026-11-14T06:01:00Z", state: "noted", resolution_id: null, proposed_by: null, reason: null },
        ],
      },
    };
    const overview = await apiWith(vi.fn<typeof globalThis.fetch>(() => reply(200, json))).getOverview();
    expect(overview.switch).toMatchObject({ proposedBy: "You", decidedBy: "Grace Finance", mine: true, clientsCarried: 1912 });
    expect(overview.switch?.notCarried).toEqual([
      {
        legacyClientRef: "C-1",
        code: "document_taken",
        attempt: 2,
        notedAt: "2026-11-14T06:00:00Z",
        state: "close_proposed",
        resolutionId: "-Nresolution000001",
        proposedBy: "A colleague",
        reason: "Same person",
        mine: false,
      },
      expect.objectContaining({ legacyClientRef: "C-2", proposedBy: null, mine: false }),
    ]);
    // An older backend sends no switch at all.
    expect((await apiWith(vi.fn<typeof globalThis.fetch>(() => reply(200, OVERVIEW))).getOverview()).switch).toBeNull();
  });

  it("proposes and decides the switch and each resolution, then reads the overview again", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) => (String(url).endsWith("/staff/migration") ? reply(200, OVERVIEW) : reply(200, {})));
    const api = apiWith(fetch);
    const sheet = (text: string) => ({ size: text.length, text: () => Promise.resolve(text) }) as unknown as File;
    await api.proposeSwitch({ clients: sheet("a\n"), investments: sheet("b\n") });
    await api.decideSwitch("-Nswitch0000000001", true);
    await api.proposeResolution("-Nswitch0000000001", "C-1", "retry", "Fixed the name");
    await api.decideResolution("-Nresolution000001", false, "Not yet");
    const calls = fetch.mock.calls
      .map(([url, init]) => [String(url), init?.body ? JSON.parse(String(init.body)) : null])
      .filter(([url]) => !String(url).endsWith("/staff/migration"));
    expect(calls).toEqual([
      ["https://api.test/staff/migration/switch", { clients_csv: "a\n", investments_csv: "b\n" }],
      ["https://api.test/staff/migration/switch/-Nswitch0000000001/decision", { approve: true, reason: null }],
      ["https://api.test/staff/migration/switch/-Nswitch0000000001/resolutions", { legacy_client_ref: "C-1", action: "retry", reason: "Fixed the name" }],
      ["https://api.test/staff/migration/switch/resolutions/-Nresolution000001/decision", { approve: false, reason: "Not yet" }],
    ]);
    expect(fetch).toHaveBeenCalledTimes(8);
  });

  it.each([
    ["not_the_trial_sheet", ["clients_csv"], "validation_failed", "not_the_trial_sheet"],
    ["missing", ["clients", 4, "phone"], "validation_failed", "phone_missing"],
    ["phone_taken", ["clients", 4, "phone"], "phone_taken", "phone_taken"],
  ])("says why the switch was refused: %s", async (field, path, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(422, { request_id: "req", error: { code, message: code, fields: [{ field_path: path, code: field }] } }),
    );
    const error = await apiWith(fetch).decideSwitch("-Nswitch0000000001", true).catch((caught: unknown) => caught);
    expect(error).toMatchObject({ code: expected });
    expect(migrationErrorMessage(error)).not.toMatch(/could not reach/);
  });
});
