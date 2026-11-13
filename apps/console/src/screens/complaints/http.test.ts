import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpComplaintsApi } from "./http.ts";

const COMPLAINT = {
  id: "-Ncomplaint00000000A",
  reference: "C-1001",
  client_id: "-Nclient00000000000A",
  conversation_id: null,
  category: "withdrawals",
  received_via: "phone",
  summary: "Withdrawal late",
  details: "Asked on Monday, not received.",
  amount: { minor: 500_000, currency: "UGX" },
  status: "investigating",
  owner_id: "-Nstaff000000000000A",
  received_at: "2026-11-10T07:00:00Z",
  due_on: "2026-11-24",
  overdue: false,
  closed_at: null,
  outcome: null,
  resolution: null,
  client_accepted: null,
};

const PENDING = {
  id: "-Nproposal000000000A",
  outcome: "upheld",
  resolution: "Paid with interest for the delay.",
  client_accepted: true,
  proposed_by: "-Nstaff000000000000A",
  proposed_at: "2026-11-13T07:00:00Z",
};

const DETAIL = {
  complaint: COMPLAINT,
  history: [
    {
      id: "-Nevent00000000000A1",
      action: "logged",
      actor: "staff",
      actor_id: "-Nstaff000000000000A",
      from_status: null,
      to_status: "new",
      note: null,
      occurred_at: "2026-11-10T07:00:00Z",
    },
  ],
  pending_outcome: PENDING,
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string) {
  return reply(status, { request_id: "req", error: { code, message: code, fields: [] } });
}

describe("complaints over HTTP", () => {
  it("reads the register with its figures", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, {
        summary: {
          open: 3,
          overdue: 1,
          received_this_quarter: 9,
          closed_this_quarter: 6,
          upheld_this_quarter: 2,
          average_days_to_close_tenths: 24,
          target_working_days: 10,
        },
        complaints: [COMPLAINT],
      }),
    );
    const register = await createHttpComplaintsApi(createHttp("https://api.test", fetch)).register("all");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/complaints?show=all");
    expect(register.summary).toEqual({
      open: 3,
      overdue: 1,
      receivedThisQuarter: 9,
      closedThisQuarter: 6,
      upheldThisQuarter: 2,
      averageDaysToCloseTenths: 24,
      targetWorkingDays: 10,
    });
    expect(register.complaints[0]).toMatchObject({ reference: "C-1001", clientId: COMPLAINT.client_id, amount: { minor: 500_000, currency: "UGX" } });
  });

  it("reads one complaint with its history and the outcome waiting", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, DETAIL));
    const detail = await createHttpComplaintsApi(createHttp("https://api.test", fetch)).complaint(COMPLAINT.id);
    expect(detail.history[0]).toMatchObject({ action: "logged", actorId: "-Nstaff000000000000A", toStatus: "new" });
    expect(detail.pendingOutcome).toMatchObject({ id: PENDING.id, clientAccepted: true, proposedBy: PENDING.proposed_by });
  });

  it("follows each command with the complaint as it now stands", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url, init) => {
      if (init?.method === "GET") return reply(200, DETAIL);
      return String(url).endsWith("/outcome") ? reply(201, PENDING) : reply(200, COMPLAINT);
    });
    const api = createHttpComplaintsApi(createHttp("https://api.test", fetch));
    await api.log({
      clientKey: "key-00000001",
      clientId: COMPLAINT.client_id,
      category: "withdrawals",
      receivedVia: "phone",
      summary: "Withdrawal late",
      details: "Asked on Monday.",
      amount: null,
    });
    await api.assign(COMPLAINT.id, "-Nstaff000000000000B");
    await api.move(COMPLAINT.id, "escalated", "Needs a manager");
    await api.addNote(COMPLAINT.id, "Called the client");
    await api.proposeOutcome(COMPLAINT.id, { outcome: "upheld", resolution: "Paid.", clientAccepted: true });
    const decided = await api.decideOutcome(COMPLAINT.id, PENDING.id, "rejected", "Not yet paid");
    expect(decided.complaint.reference).toBe("C-1001");
    const writes = fetch.mock.calls
      .filter((call) => call[1]?.method !== "GET")
      .map((call) => [call[1]?.method, String(call[0]).replace("https://api.test/staff/complaints", ""), JSON.parse(String(call[1]?.body))]);
    expect(writes).toEqual([
      [
        "POST",
        "",
        {
          client_key: "key-00000001",
          client_id: COMPLAINT.client_id,
          category: "withdrawals",
          received_via: "phone",
          summary: "Withdrawal late",
          details: "Asked on Monday.",
          amount: null,
        },
      ],
      ["PUT", `/${COMPLAINT.id}/owner`, { owner_id: "-Nstaff000000000000B" }],
      ["POST", `/${COMPLAINT.id}/status`, { status: "escalated", note: "Needs a manager" }],
      ["POST", `/${COMPLAINT.id}/notes`, { note: "Called the client" }],
      ["POST", `/${COMPLAINT.id}/outcome`, { outcome: "upheld", resolution: "Paid.", client_accepted: true }],
      ["POST", `/${COMPLAINT.id}/outcome/decision`, { proposal_id: PENDING.id, decision: "rejected", reason: "Not yet paid" }],
    ]);
    // Six commands, each followed by one read of that complaint.
    const reads = fetch.mock.calls.filter((call) => call[1]?.method === "GET").map((call) => String(call[0]));
    expect(reads).toEqual(Array.from({ length: 6 }, () => `https://api.test/staff/complaints/${COMPLAINT.id}`));
  });

  it("turns refusals into the screen's errors", async () => {
    for (const [status, code, expected] of [
      [409, "conflict", "conflict"],
      [403, "forbidden", "forbidden"],
      [404, "not_found", "not_found"],
      [422, "validation_failed", "invalid"],
    ] as const) {
      const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code));
      await expect(
        createHttpComplaintsApi(createHttp("https://api.test", fetch)).decideOutcome("x", "y", "confirmed", null),
      ).rejects.toMatchObject({ code: expected });
    }
    const offline = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    await expect(createHttpComplaintsApi(createHttp("https://api.test", offline)).register("open")).rejects.toMatchObject({ code: "unavailable" });
  });
});
