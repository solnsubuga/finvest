import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiProfileClient } from "../apiProfileClient";
import { ProfileError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

type Seen = { method: string; url: string; body: unknown };

function serve(routes: Record<string, () => Response>): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const method = init?.method ?? "GET";
    const path = String(url).slice(API_URL.length);
    seen.push({ method, url: path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const answer = routes[`${method} ${path}`];
    if (!answer) throw new Error(`unexpected ${method} ${path}`);
    return answer();
  });
  return seen;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });
const refused = (code: string, status: number, fields: unknown[] = []) =>
  json({ request_id: "r1", error: { code, message: code, fields } }, status);

const standing = {
  running_investments: 1,
  owed: [{ amount_minor: 5_000_000, currency: "UGX" }],
  open_deposits: 0,
  open_payouts: 0,
  open_goals: 0,
  live_plans: 0,
  blockers: ["investments_running", "money_owed"],
};

describe("the profile client for the real API", () => {
  it("sends next of kin in the API's words and reads them back", async () => {
    const seen = serve({
      "PUT /client/next-of-kin": json({
        people: [
          { id: "P".repeat(20), full_name: "Joseph Nakato", relationship: "spouse", phone: "+256772000111", id_number: "CM900" },
        ],
        updated_at: "2026-11-14T08:00:00Z",
      }),
    });
    const saved = await createApiProfileClient().saveNextOfKin([
      { fullName: "Joseph Nakato", relationship: "spouse", phone: "+256772000111", idNumber: "CM900" },
    ]);
    expect(seen[0]?.body).toEqual({
      people: [
        { full_name: "Joseph Nakato", relationship: "spouse", phone: "+256772000111", id_number: "CM900" },
      ],
    });
    expect(saved.people[0]?.fullName).toBe("Joseph Nakato");
  });

  it("names each refused field without the body prefix", async () => {
    serve({
      "PUT /client/next-of-kin": refused("validation_failed", 422, [
        { field_path: ["body", "people", 0, "phone"], code: "your_own_number" },
      ]),
    });
    const error = await createApiProfileClient().saveNextOfKin([]).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ProfileError);
    expect((error as ProfileError).code).toBe("invalid");
    expect((error as ProfileError).fields).toEqual([{ field: "people.0.phone", code: "your_own_number" }]);
  });

  it("reads the standing in whole minor units", async () => {
    serve({ "GET /client/account-closure": json({ standing, request: null }) });
    const closure = await createApiProfileClient().getClosure();
    expect(closure.standing.owed).toEqual([{ minor: 5_000_000, currency: "UGX" }]);
    expect(closure.standing.blockers).toEqual(["investments_running", "money_owed"]);
  });

  it("tells a refusal for things still open apart from other conflicts", async () => {
    serve({
      "POST /client/account-closure": refused("conflict", 409, [{ field_path: ["blockers"], code: "investments_running" }]),
    });
    const blocked = await createApiProfileClient().askToClose(null).catch((e: unknown) => e);
    expect((blocked as ProfileError).code).toBe("blocked");

    serve({ "POST /client/account-closure": refused("conflict", 409) });
    const other = await createApiProfileClient().askToClose(null).catch((e: unknown) => e);
    expect((other as ProfileError).code).toBe("conflict");
  });

  it("gives a rate limit and other refusals their own words, and keeps 'could not reach' for no answer", async () => {
    const client = createApiProfileClient();
    serve({ "GET /client/next-of-kin": refused("rate_limited", 429) });
    expect(((await client.getNextOfKin().catch((e: unknown) => e)) as ProfileError).code).toBe("rate_limited");
    serve({ "GET /client/next-of-kin": refused("forbidden", 403) });
    expect(((await client.getNextOfKin().catch((e: unknown) => e)) as ProfileError).code).toBe("refused");
    serve({ "GET /client/next-of-kin": refused("not_found", 404) });
    expect(((await client.getNextOfKin().catch((e: unknown) => e)) as ProfileError).code).toBe("refused");
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    expect(((await client.getNextOfKin().catch((e: unknown) => e)) as ProfileError).code).toBe("unavailable");
  });

  it("asks for a fresh sign-in when the server does", async () => {
    serve({ "POST /client/account-closure": refused("sign_in_again", 403) });
    const error = await createApiProfileClient().askToClose("Moving abroad").catch((e: unknown) => e);
    expect((error as ProfileError).code).toBe("sign_in_again");
  });
});
