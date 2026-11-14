import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiClaimClient } from "../apiClaimClient";

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

const RID = "R".repeat(20);
type Route = { status: number; body?: unknown };

function serve(routes: Record<string, Route>) {
  const seen: { key: string; body: unknown }[] = [];
  setFetch(async (url, init) => {
    const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
    seen.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
    const route = routes[key];
    if (!route) throw new TypeError("Network request failed");
    return new Response(route.body === undefined ? null : JSON.stringify(route.body), { status: route.status });
  });
  return seen;
}

function refused(status: number, code: string, fields: { field_path: string[]; code: string }[] = []): Route {
  return { status, body: { request_id: RID, error: { code, message: "x", fields } } };
}

const state = (status: string, step = "full_id_check") => ({
  status: 200,
  body: { status, identity_step: step, problem_reported: status === "problem_reported" },
});

afterEach(() => setFetch(null));

describe("the claim client for the real API", () => {
  it("reads the claim, and no claim for a client who signed up in the app", async () => {
    serve({ "GET /client/claim": state("signed_in", "full_id_check") });
    expect(await createApiClaimClient().getClaim()).toEqual({ status: "signed_in", identityStep: "full_id_check" });
    serve({ "GET /client/claim": refused(404, "not_found") });
    expect(await createApiClaimClient().getClaim()).toBeNull();
  });

  it("names what is missing when handing in, and a step out of turn as moved on", async () => {
    serve({ "POST /client/claim/identity": refused(422, "validation_failed", [{ field_path: ["details"], code: "missing" }]) });
    await expect(createApiClaimClient().handIn()).rejects.toMatchObject({ code: "incomplete", missing: ["details"] });
    serve({ "POST /client/claim/confirm": refused(409, "conflict") });
    await expect(createApiClaimClient().confirm()).rejects.toMatchObject({ code: "cannot_change" });
  });

  it("sends the problem trimmed, and refuses an empty one", async () => {
    const seen = serve({ "POST /client/claim/problem": state("problem_reported") });
    expect((await createApiClaimClient().reportProblem("  Wrong amount  ")).status).toBe("problem_reported");
    expect(seen[0]?.body).toEqual({ description: "Wrong amount" });
    serve({ "POST /client/claim/problem": refused(422, "validation_failed", [{ field_path: ["description"], code: "invalid" }]) });
    await expect(createApiClaimClient().reportProblem(" ")).rejects.toMatchObject({ code: "not_accepted" });
  });

  it("says offline when the network fails", async () => {
    serve({});
    await expect(createApiClaimClient().confirm()).rejects.toMatchObject({ code: "offline" });
  });
});
