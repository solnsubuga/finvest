import { describe, expect, it, vi } from "vitest";
import { ApiError, createHttp } from "@/api/http.ts";
import { TeamError } from "./api.ts";
import { createHttpTeamApi, toTeamError } from "./http.ts";

const CHANGE = {
  id: "-Nchange00000000000A",
  kind: "change_role",
  staff_id: "-Nstaff000000000000A",
  staff_name: "Amina Kintu",
  email: null,
  role: "operations",
  current_role: "support",
  reason: "Moving desks",
  proposed_by: "-Nstaff000000000000B",
  proposed_by_name: "Grace Auma",
  proposed_at: "2026-11-13T07:40:00Z",
  status: "proposed",
  yours: false,
  about_you: false,
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("team over HTTP", () => {
  it("reads the team and leaves out permissions this console does not know", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, {
        members: [],
        changes: [CHANGE],
        roles: [{ role: "support", permissions: ["support.view", "future.thing"] }],
        can_change: true,
      }),
    );
    const team = await createHttpTeamApi(createHttp("https://api.test", fetch)).load();
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/team", expect.objectContaining({ method: "GET", credentials: "include" }));
    expect(team.roles).toEqual([{ role: "support", permissions: ["support.view"] }]);
    expect(team.changes[0]).toMatchObject({ staffName: "Amina Kintu", currentRole: "support", yours: false });
  });

  it("sends a proposal and a decision in the backend's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, CHANGE));
    const api = createHttpTeamApi(createHttp("https://api.test", fetch));
    await api.propose({ kind: "change_role", staffId: "-Nstaff000000000000A", role: "operations", reason: "Moving desks" });
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({
      kind: "change_role",
      staff_id: "-Nstaff000000000000A",
      role: "operations",
      reason: "Moving desks",
    });
    await api.decide("-Nchange00000000000A", "approved", "Fine");
    expect(fetch.mock.calls[1]?.[0]).toBe("https://api.test/staff/team/changes/-Nchange00000000000A/decision");
  });

  it("turns the backend's refusals into the screen's", () => {
    const refused = (code: string, field?: string, status = 409) =>
      toTeamError(new ApiError(code, status, field ? [{ field_path: ["body", "kind"], code: field }] : []));
    expect(refused("conflict", "last_admin").code).toBe("last_admin");
    expect(refused("forbidden", "own_account", 403).code).toBe("own_account");
    expect(refused("conflict", "taken").code).toBe("email_taken");
    expect(refused("conflict").code).toBe("waiting");
    expect(refused("validation_failed", "required", 422).code).toBe("invalid");
    expect(toTeamError(new ApiError("forbidden", 403), true).code).toBe("same_person");
    expect(toTeamError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    expect(toTeamError(new Error("boom"))).toEqual(new TeamError("unavailable"));
  });
});
