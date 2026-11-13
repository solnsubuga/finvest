import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { AnnouncementsError } from "./announcements.ts";
import { WordingsError } from "./api.ts";
import { createHttpAnnouncementsApi, createHttpWordingsApi } from "./http.ts";

const TEXT = { title: "Money in", body: "We received {amount}.", sms: "Finvest: we received {amount}." };
const PREVIEW = { title: "Money in", body: "We received UGX 50,000.", sms: "Finvest: we received UGX 50,000." };

function wording(proposal: boolean) {
  return {
    event: "deposit_received",
    category: "money",
    variables: ["amount"],
    wording: TEXT,
    preview: PREVIEW,
    edited: false,
    changed_at: null,
    proposal: proposal
      ? {
          id: "-Nproposal000000000A",
          wording: { ...TEXT, title: "Money received" },
          preview: { ...PREVIEW, title: "Money received" },
          reason: "Clearer",
          proposed_by: "-Nstaff000000000000B",
          proposed_at: "2026-11-13T07:40:00Z",
          yours: false,
        }
      : null,
  };
}

const ANNOUNCEMENT = {
  id: "-Nannounce000000000A",
  title: "New bills",
  message: "New Treasury bills are open in the app.",
  reason: "Auction results",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-13T07:40:00Z",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
  told: 0,
  yours: false,
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, fields: { field_path: string[]; code: string }[] = []) {
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

describe("wordings over HTTP", () => {
  it("reads the messages in the console's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { wordings: [wording(true)] }));
    const [first] = await createHttpWordingsApi(createHttp("https://api.test", fetch)).list();
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/notification-wordings", expect.objectContaining({ method: "GET" }));
    expect(first).toMatchObject({ event: "deposit_received", changedAt: null, proposal: { proposedBy: "-Nstaff000000000000B", yours: false } });
  });

  it("answers a proposal with the message as it now stands", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(201, wording(true).proposal))
      .mockImplementationOnce(() => reply(200, { wordings: [wording(true)] }));
    const updated = await createHttpWordingsApi(createHttp("https://api.test", fetch)).propose("deposit_received", TEXT, "Clearer");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/notification-wordings/deposit_received/proposals");
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ wording: TEXT, reason: "Clearer" });
    expect(updated.proposal?.id).toBe("-Nproposal000000000A");
  });

  it("answers a decision with the message it changed", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { wordings: [wording(true)] }))
      .mockImplementationOnce(() => reply(200, wording(true).proposal))
      .mockImplementationOnce(() => reply(200, { wordings: [wording(false)] }));
    const api = createHttpWordingsApi(createHttp("https://api.test", fetch));
    await api.list();
    const updated = await api.decide("-Nproposal000000000A", "approved", "Reads well");
    expect(fetch.mock.calls[1]?.[0]).toBe("https://api.test/staff/notification-wordings/proposals/-Nproposal000000000A/decision");
    expect(updated).toMatchObject({ event: "deposit_received", proposal: null });
  });

  it("marks the fields the backend refused, by the last part of each path", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      refusal(422, "validation_failed", [
        { field_path: ["body"], code: "unknown_placeholder" },
        { field_path: ["body", "wording", "sms"], code: "string_too_long" },
        { field_path: ["reason"], code: "required" },
      ]),
    );
    const error = await createHttpWordingsApi(createHttp("https://api.test", fetch))
      .propose("deposit_received", TEXT, "Clearer")
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(WordingsError);
    expect((error as WordingsError).code).toBe("invalid");
    expect((error as WordingsError).problems).toEqual([
      { field: "body", code: "unknown_placeholder" },
      { field: "sms", code: "too_long" },
    ]);
  });

  it("names a refusal to decide your own change, and a change already waiting", async () => {
    const decide = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { wordings: [wording(true)] }))
      .mockImplementationOnce(() => refusal(403, "forbidden"));
    const api = createHttpWordingsApi(createHttp("https://api.test", decide));
    await api.list();
    await expect(api.decide("-Nproposal000000000A", "approved", "Fine")).rejects.toMatchObject({ code: "same_person" });

    const propose = vi.fn<typeof globalThis.fetch>(() => refusal(409, "conflict"));
    await expect(createHttpWordingsApi(createHttp("https://api.test", propose)).propose("deposit_received", TEXT, "x")).rejects.toMatchObject({
      code: "conflict",
    });
    const offline = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    await expect(createHttpWordingsApi(createHttp("https://api.test", offline)).list()).rejects.toMatchObject({ code: "unavailable" });
  });
});

describe("announcements over HTTP", () => {
  it("lists, proposes and decides", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { announcements: [ANNOUNCEMENT] }))
      .mockImplementationOnce(() => reply(201, { ...ANNOUNCEMENT, yours: true }))
      .mockImplementationOnce(() => reply(200, { ...ANNOUNCEMENT, status: "rejected", decided_by: "-Nstaff000000000000C", decision_reason: "Not now" }));
    const api = createHttpAnnouncementsApi(createHttp("https://api.test", fetch));
    expect((await api.list())[0]).toMatchObject({ proposedBy: "-Nstaff000000000000B", decidedBy: null, told: 0 });
    const proposed = await api.propose({ title: "New bills", message: "Open now.", reason: "Auction" });
    expect(proposed.yours).toBe(true);
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ title: "New bills", message: "Open now.", reason: "Auction" });
    const decided = await api.decide(ANNOUNCEMENT.id, "rejected", "Not now");
    expect(fetch.mock.calls[2]?.[0]).toBe(`https://api.test/staff/announcements/${ANNOUNCEMENT.id}/decision`);
    expect(decided).toMatchObject({ status: "rejected", decisionReason: "Not now" });
  });

  it("turns refusals into the screen's errors", async () => {
    const invalid = vi.fn<typeof globalThis.fetch>(() =>
      refusal(422, "validation_failed", [{ field_path: ["message"], code: "outside_link" }]),
    );
    const error = await createHttpAnnouncementsApi(createHttp("https://api.test", invalid))
      .propose({ title: "t", message: "m", reason: "r" })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AnnouncementsError);
    expect((error as AnnouncementsError).problems).toEqual([{ field: "message", code: "outside_link" }]);
    const gone = vi.fn<typeof globalThis.fetch>(() => refusal(404, "not_found"));
    await expect(createHttpAnnouncementsApi(createHttp("https://api.test", gone)).decide("x", "approved", null)).rejects.toMatchObject({
      code: "not_found",
    });
  });
});
