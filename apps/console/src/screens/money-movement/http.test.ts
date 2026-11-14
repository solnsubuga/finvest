import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { payoutsErrorMessage } from "./api.ts";
import { createHttpPayoutsApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, field?: string) {
  const fields = field ? [{ field_path: ["body"], code: field }] : [];
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

const STRANDED = {
  id: "-NpayoutA00000000001",
  client_id: "-Nclient00000000000A",
  status: "uncertain",
  amount: { minor: 1_200_000, currency: "UGX" },
  first_sent_at: "2026-11-08T09:00:00Z",
  resolution: {
    id: "-NresolutionA0000001",
    payout_id: "-NpayoutA00000000001",
    outcome: "failed",
    statement_line_id: null,
    statement_id: "-NstatementA00000001",
    reason: "No line on any day",
    status: "proposed",
    proposed_by: "-Nstaff000000000000B",
    proposed_at: "2026-11-13T08:00:00Z",
    decided_by: null,
    decided_at: null,
    decision_reason: null,
  },
};

const body = (init: RequestInit | undefined) => JSON.parse(String(init?.body)) as Record<string, unknown>;
const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];

describe("payouts that need a person, over HTTP", () => {
  it("reads payouts held up at ioTec", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, [
        {
          id: "-NpayoutB00000000001",
          client_id: "-Nclient00000000000A",
          kind: "mobile_money",
          amount: { minor: 350_000, currency: "UGX" },
          created_at: "2026-11-13T06:00:00Z",
          first_tried_at: "2026-11-13T06:00:05Z",
          last_tried_at: "2026-11-13T08:50:00Z",
          tries: 6,
        },
      ]),
    );
    const [item] = await createHttpPayoutsApi(createHttp("https://api.test", fetch)).listHeldUp();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payouts/held-up");
    expect(item).toEqual({
      id: "-NpayoutB00000000001",
      clientId: "-Nclient00000000000A",
      amount: { minor: 350_000, currency: "UGX" },
      createdAt: "2026-11-13T06:00:00Z",
      firstTriedAt: "2026-11-13T06:00:05Z",
      lastTriedAt: "2026-11-13T08:50:00Z",
      tries: 6,
    });
  });

  it("reads stranded payouts with their open proposal", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [STRANDED, { ...STRANDED, id: "-NpayoutC00000000001", resolution: null }]));
    const [proposed, open] = await createHttpPayoutsApi(createHttp("https://api.test", fetch)).listStranded();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payouts/stranded");
    expect(proposed?.resolution).toEqual({
      id: "-NresolutionA0000001",
      outcome: "failed",
      statementLineId: null,
      statementId: "-NstatementA00000001",
      reason: "No line on any day",
      proposedBy: "-Nstaff000000000000B",
      proposedAt: "2026-11-13T08:00:00Z",
    });
    expect(open?.resolution).toBeNull();
  });

  it("proposes with only the evidence the outcome needs, and an idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, STRANDED.resolution));
    await createHttpPayoutsApi(createHttp("https://api.test", fetch)).propose("key-1", STRANDED.id, {
      outcome: "paid",
      statementLineId: "-NlineA0000000000001",
      statementId: "-NstatementA00000001",
      reason: "On the 9th",
    });
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe(`https://api.test/staff/payouts/${STRANDED.id}/resolutions`);
    expect(body(init)).toEqual({ outcome: "paid", statement_line_id: "-NlineA0000000000001", statement_id: null, reason: "On the 9th" });
    expect(header(init, "Idempotency-Key")).toBe("key-1");
  });

  it("approves with a key, and declines or withdraws without one", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, {}));
    const api = createHttpPayoutsApi(createHttp("https://api.test", fetch));
    await api.approve("key-2", "-NresolutionA0000001", "Checked");
    await api.decline("-NresolutionA0000001", "Ask ioTec");
    await api.withdraw("-NresolutionA0000001", "Wrong statement");
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/payout-resolutions/-NresolutionA0000001/approve",
      "https://api.test/staff/payout-resolutions/-NresolutionA0000001/decline",
      "https://api.test/staff/payout-resolutions/-NresolutionA0000001/withdraw",
    ]);
    expect(header(fetch.mock.calls[0]?.[1], "Idempotency-Key")).toBe("key-2");
    expect(body(fetch.mock.calls[1]?.[1])).toEqual({ reason: "Ask ioTec" });
  });

  it("words the backend's named refusals, and a confirmation the person did not give", async () => {
    const cases: [Promise<Response>, string][] = [
      [refusal(409, "conflict", "statement_day_missing"), "A day between the send and that statement has no imported statement"],
      [refusal(403, "forbidden", "proposed_by_you"), "You proposed this, so a different person must decide it."],
      [refusal(403, "confirm_it_is_you"), "you did not confirm it is you"],
      [refusal(404, "not_found"), "no longer exists"],
    ];
    for (const [response, words] of cases) {
      const fetch = vi.fn<typeof globalThis.fetch>(() => response);
      // No dialog is mounted, so the refusal reaches the screen as is.
      const api = createHttpPayoutsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
      const error = await api.approve("key", "-NresolutionA0000001", "Checked").catch((caught: unknown) => caught);
      expect(payoutsErrorMessage(error)).toContain(words);
    }
  });
});
