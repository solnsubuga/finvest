import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpRunRequestsApi } from "./http.ts";

const REQUEST = {
  id: "-Nrun00000000000001",
  kind: "interest",
  run_date: "2026-11-13",
  reason: "The run stopped part way",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T07:40:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
  job_id: null,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

const refused = (code: string, fields: unknown[] = []) => ({ request_id: "r", error: { code, message: code, fields } });

describe("investments: run requests over the API", () => {
  it("lists them in the console's shape", async () => {
    const fetchImpl = respond(200, [REQUEST]);
    const api = createHttpRunRequestsApi(createHttp("https://api.example", fetchImpl));
    const [first] = await api.list();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/investment-run-requests?limit=50");
    expect(first).toMatchObject({ kind: "interest", runDate: "2026-11-13", status: "proposed", proposedBy: "-Nstaff000000000000B" });
  });

  it("asks with the key, and decides on each request's own route", async () => {
    const fetchImpl = respond(201, REQUEST);
    const api = createHttpRunRequestsApi(createHttp("https://api.example", fetchImpl));
    await api.propose("key-1", { kind: "interest", runDate: "2026-11-13", reason: "Stopped" });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/investment-run-requests");
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(String(init?.body))).toEqual({ kind: "interest", run_date: "2026-11-13", reason: "Stopped" });
    await api.decline("key-2", REQUEST.id, "Already fine");
    const [declineUrl, declineInit] = fetchImpl.mock.calls[1] ?? [];
    expect(declineUrl).toBe(`https://api.example/staff/investment-run-requests/${REQUEST.id}/decline`);
    expect(JSON.parse(String(declineInit?.body))).toEqual({ note: "Already fine" });
  });

  it("maps refusals to what the screen says", async () => {
    const cases: [number, unknown, string][] = [
      [409, refused("conflict", [{ field_path: ["run_date"], code: "run_date_passed" }]), "day_passed"],
      [409, refused("conflict"), "conflict"],
      [403, refused("forbidden"), "same_person"],
      [404, refused("not_found"), "not_found"],
    ];
    for (const [status, body, code] of cases) {
      const api = createHttpRunRequestsApi(createHttp("https://api.example", respond(status, body), () => Promise.resolve(false)));
      await expect(api.approve("k", REQUEST.id)).rejects.toMatchObject({ code });
    }
    const api = createHttpRunRequestsApi(
      createHttp("https://api.example", respond(422, refused("validation_failed", [{ field_path: ["run_date"], code: "too_old" }]))),
    );
    await expect(api.propose("k", { kind: "interest", runDate: "2026-09-01", reason: "x" })).rejects.toMatchObject({
      code: "invalid",
      problems: [{ field: "run_date", code: "too_old" }],
    });
  });
});
