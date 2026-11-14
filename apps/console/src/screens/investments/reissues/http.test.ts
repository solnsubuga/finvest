import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpReissuesApi } from "./http.ts";

const REISSUE = {
  id: "-Nreissue0000000001",
  client_id: "-Nclient00000000001",
  issued_from: "2026-10-15",
  issued_to: "2026-11-13",
  reason: "Changed her email",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T07:40:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
  note_count: 3,
  copies: { queued: 0, sent: 0, skipped: 0, failed: 0 },
  finished_at: null,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

const refused = (code: string, fields: unknown[] = []) => ({ request_id: "r", error: { code, message: code, fields } });

describe("investments: note reissues over the API", () => {
  it("lists them in the console's shape and counts a scope", async () => {
    const fetchImpl = respond(200, [REISSUE]);
    const api = createHttpReissuesApi(createHttp("https://api.example", fetchImpl));
    const [first] = await api.list();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/note-reissues?limit=50");
    expect(first).toMatchObject({ clientId: REISSUE.client_id, issuedFrom: "2026-10-15", noteCount: 3, copies: { sent: 0 } });

    const counting = respond(200, { note_count: 40 });
    const counter = createHttpReissuesApi(createHttp("https://api.example", counting));
    expect(await counter.count({ clientId: null, issuedFrom: "2026-11-12", issuedTo: "2026-11-12" })).toBe(40);
    expect(counting.mock.calls[0]?.[0]).toBe("https://api.example/staff/note-reissues/count?issued_from=2026-11-12&issued_to=2026-11-12");
  });

  it("asks with the key, and decides on each request's own route", async () => {
    const fetchImpl = respond(201, REISSUE);
    const api = createHttpReissuesApi(createHttp("https://api.example", fetchImpl));
    await api.propose("key-1", { clientId: null, issuedFrom: "2026-11-12", issuedTo: "2026-11-13", reason: "Emails failed" });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/note-reissues");
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(String(init?.body))).toEqual({ client_id: null, issued_from: "2026-11-12", issued_to: "2026-11-13", reason: "Emails failed" });
    await api.decline("key-2", REISSUE.id, "Already in the app");
    const [declineUrl, declineInit] = fetchImpl.mock.calls[1] ?? [];
    expect(declineUrl).toBe(`https://api.example/staff/note-reissues/${REISSUE.id}/decline`);
    expect(JSON.parse(String(declineInit?.body))).toEqual({ note: "Already in the app" });
  });

  it("maps refusals to what the screen says", async () => {
    const cases: [number, unknown, string][] = [
      [409, refused("conflict"), "conflict"],
      [403, refused("forbidden"), "same_person"],
      [404, refused("not_found"), "not_found"],
    ];
    for (const [status, body, code] of cases) {
      const api = createHttpReissuesApi(createHttp("https://api.example", respond(status, body), () => Promise.resolve(false)));
      await expect(api.approve("k", REISSUE.id)).rejects.toMatchObject({ code });
    }
    const api = createHttpReissuesApi(
      createHttp("https://api.example", respond(422, refused("validation_failed", [{ field_path: ["issued_from"], code: "no_notes" }]))),
    );
    await expect(api.propose("k", { clientId: null, issuedFrom: "2026-11-01", issuedTo: "2026-11-01", reason: "x" })).rejects.toMatchObject({
      code: "invalid",
      problems: [{ field: "issued_from", code: "no_notes" }],
    });
  });
});
