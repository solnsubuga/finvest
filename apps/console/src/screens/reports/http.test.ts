import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { AuditError } from "./api.ts";
import { createHttpAuditApi, toQuery } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const EVENT = {
  id: "-NauditEvent00000001",
  occurred_at: "2026-11-13T07:40:00Z",
  category: "approval",
  action: "identity.team.change_approved",
  outcome: "succeeded",
  actor_kind: "staff",
  actor_id: "-Nstaff000000000000A",
  actor_name: "Grace Auma",
  resource_type: "staff_change",
  resource_id: "-Nchange00000000000A",
  reason: "Agreed",
  correlation_id: "req_1",
  details: { kind: "invite" },
};

describe("audit trail over HTTP", () => {
  it("sends Kampala days as whole days, and leaves out what is not set", () => {
    expect(toQuery({})).toBe("");
    const query = new URLSearchParams(toQuery({ from: "2026-11-01", to: "2026-11-30", outcome: "denied", resourceId: "  " }, "abc").slice(1));
    expect(Object.fromEntries(query)).toEqual({
      outcome: "denied",
      since: "2026-11-01T00:00:00+03:00",
      until: "2026-12-01T00:00:00+03:00",
      cursor: "abc",
    });
  });

  it("reads a page and the filter lists in the console's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((input) =>
      String(input).includes("facets") ? reply(200, { actions: ["a.b"], resource_types: ["client"] }) : reply(200, { events: [EVENT], next_cursor: "next" }),
    );
    const api = createHttpAuditApi(createHttp("https://api.test", fetch));
    const page = await api.events({ category: "approval" });
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/audit/events?category=approval", expect.objectContaining({ method: "GET", credentials: "include" }));
    expect(page.nextCursor).toBe("next");
    expect(page.events[0]).toMatchObject({ actorName: "Grace Auma", resourceId: "-Nchange00000000000A", correlationId: "req_1" });
    expect(await api.facets()).toEqual({ actions: ["a.b"], resourceTypes: ["client"] });
  });

  it("downloads the export with the same filters, and names the file in Kampala time", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => Promise.resolve(new Response("occurred_at\r\n", { status: 200, headers: { "Content-Type": "text/csv" } })));
    const api = createHttpAuditApi(createHttp("https://api.test", fetch), () => new Date("2026-11-13T21:30:05Z"));
    const exported = await api.exportCsv({ actorKind: "client" });
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/audit/events.csv?actor_kind=client", expect.anything());
    expect(exported.filename).toBe("finvest-audit-20261114-003005.csv");
    expect(await exported.file.text()).toBe("occurred_at\r\n");
  });

  it("turns the API's refusals into the screen's errors", async () => {
    const answers = [
      reply(422, { error: { code: "validation_failed", fields: [{ field_path: ["query"], code: "too_many_events" }] } }),
      reply(422, { error: { code: "validation_failed", fields: [{ field_path: ["query", "actor_id"], code: "invalid" }] } }),
      reply(403, { error: { code: "forbidden", fields: [] } }),
    ];
    const fetch = vi.fn<typeof globalThis.fetch>(() => answers.shift() ?? Promise.reject(new TypeError("offline")));
    const api = createHttpAuditApi(createHttp("https://api.test", fetch));
    await expect(api.exportCsv({})).rejects.toEqual(new AuditError("too_many"));
    await expect(api.events({ actorId: "x" })).rejects.toMatchObject({ code: "invalid", field: "actor_id" });
    await expect(api.facets()).rejects.toMatchObject({ code: "forbidden" });
    await expect(api.events({})).rejects.toMatchObject({ code: "unavailable" });
  });
});
