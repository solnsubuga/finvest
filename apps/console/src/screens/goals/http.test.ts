import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createHttpGoalsApi, createHttpGroupsApi } from "./http.ts";

const GOAL = {
  goal_id: "-NgoalA0000000000001",
  client_id: "-Nclient00000000000A",
  name: "School fees, Term 1",
  category: "school_fees",
  currency: "UGX",
  target_minor: 2_000_000,
  target_date: "2027-04-12",
  status: "active",
  product_code: "TREA-91",
  // 22:30 in Kampala on 13 September is still that day; 21:30 UTC is 00:30 the next day.
  created_at: "2026-09-13T21:30:00Z",
  closed_at: null,
};

const CARD = {
  id: GOAL.client_id,
  verified_name: "NAMUBIRU Sarah",
  account_number: "1004238176",
  status: "active",
  created_at: "2026-09-01T08:00:00Z",
};

const GROUP = {
  group_id: "-NgroupA000000000001",
  owner_client_id: "-Nclient00000000000B",
  name: "Family land in Mukono",
  category: "land_or_home",
  currency: "USD",
  target_minor: 1_200_000,
  target_date: null,
  status: "active",
  created_at: "2026-08-16T06:00:00Z",
  closed_at: null,
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string) {
  return reply(status, { request_id: "req", error: { code, message: code, fields: [] } });
}

/** Answers by path, so each test says only what the routes hold. */
function routes(table: Record<string, () => Promise<Response>>) {
  return vi.fn<typeof globalThis.fetch>((url) => {
    const path = new URL(String(url)).pathname;
    const answer = table[path];
    return answer ? answer() : refusal(404, "not_found");
  });
}

function goalsApi(fetch: typeof globalThis.fetch) {
  const http = createHttp("https://api.test", fetch);
  return createHttpGoalsApi(http, createHttpDirectory(http));
}

describe("goals over HTTP", () => {
  it("lists goals with the client's name, account number and the product's name", async () => {
    const fetch = routes({
      "/staff/goals": () => reply(200, { goals: [GOAL] }),
      "/staff/clients/cards": () => reply(200, { cards: [CARD] }),
      "/staff/products": () => reply(200, [{ code: "TREA-91", name: "91 Day Treasury Bill" }]),
    });
    const [goal] = await goalsApi(fetch).listGoals(null);
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/goals?limit=200");
    expect(goal).toEqual({
      id: GOAL.goal_id,
      clientId: GOAL.client_id,
      clientName: "NAMUBIRU Sarah",
      accountNumber: "1004238176",
      name: "School fees, Term 1",
      category: "school_fees",
      currency: "UGX",
      target: "2000000",
      targetDate: "2027-04-12",
      status: "active",
      productName: "91 Day Treasury Bill",
      createdOn: "2026-09-14",
      closedOn: null,
    });
  });

  it("asks the server for one status", async () => {
    const fetch = routes({ "/staff/goals": () => reply(200, { goals: [] }) });
    await goalsApi(fetch).listGoals("closed");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/goals?limit=200&status=closed");
  });

  it("names products from the public list for staff who may not see products, and by code if that fails too", async () => {
    const publicList = vi.fn(() => reply(200, { updated_on: "2026-11-13", products: [{ code: "TREA-91", name: "91 Day Treasury Bill" }] }));
    const fetch = routes({
      "/staff/goals": () => reply(200, { goals: [GOAL, { ...GOAL, goal_id: "-NgoalB0000000000001", product_code: "OLD-364" }] }),
      "/staff/clients/cards": () => reply(200, { cards: [] }),
      "/staff/products": () => refusal(403, "forbidden"),
      "/public/products": publicList,
    });
    const api = goalsApi(fetch);
    const goals = await api.listGoals(null);
    expect(goals.map((goal) => goal.productName)).toEqual(["91 Day Treasury Bill", "OLD-364"]);
    // A client whose card cannot be read still shows, without an account number.
    expect(goals[0]).toMatchObject({ clientName: "Client details could not be read", accountNumber: null });
    await api.listGoals(null);
    expect(publicList).toHaveBeenCalledTimes(1);
  });

  it("opens a goal with its progress and savings as whole-number strings", async () => {
    const fetch = routes({
      [`/staff/goals/${GOAL.goal_id}`]: () =>
        reply(200, {
          goal: GOAL,
          progress: { value_minor: 1_585_080, interest_minor: 25_080, awaiting_payment_minor: 0, percent: 79, standing: "on_track" },
          savings: [
            {
              investment_id: "-NinvestA00000000001",
              product_code: "TREA-91",
              status: "withdrawn",
              amount_minor: 200_000,
              accrued_net_minor: 931,
              start_date: "2026-10-28",
              maturity_date: "2027-01-27",
            },
          ],
        }),
      "/staff/clients/cards": () => reply(200, { cards: [CARD] }),
      "/staff/products": () => reply(200, [{ code: "TREA-91", name: "91 Day Treasury Bill" }]),
    });
    const detail = await goalsApi(fetch).getGoal(GOAL.goal_id);
    expect(detail.progress).toEqual({ value: "1585080", interest: "25080", awaitingPayment: "0", percent: 79, standing: "on_track" });
    expect(detail.savings).toEqual([
      {
        investmentId: "-NinvestA00000000001",
        productName: "91 Day Treasury Bill",
        status: "withdrawn",
        amount: "200000",
        accruedNet: "931",
        startDate: "2026-10-28",
        maturityDate: "2027-01-27",
        rolledFrom: null,
      },
    ]);
  });

  it.each([
    [404, "not_found", "not_found"],
    [403, "forbidden", "forbidden"],
    [500, "internal_error", "unavailable"],
  ])("turns a %i %s into %s", async (status, code, expected) => {
    const fetch = routes({ [`/staff/goals/${GOAL.goal_id}`]: () => refusal(status, code) });
    expect(await goalsApi(fetch).getGoal(GOAL.goal_id).catch((e: unknown) => e)).toMatchObject({ code: expected });
  });
});

describe("group goals over HTTP", () => {
  it("lists groups in their own currency, with the Kampala day they were set up", async () => {
    const fetch = routes({ "/staff/goal-groups": () => reply(200, { groups: [GROUP] }) });
    const [group] = await createHttpGroupsApi(createHttp("https://api.test", fetch)).listGroups("active");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/goal-groups?limit=200&status=active");
    expect(group).toEqual({
      id: GROUP.group_id,
      ownerClientId: GROUP.owner_client_id,
      name: "Family land in Mukono",
      category: "land_or_home",
      currency: "USD",
      target: "1200000",
      targetDate: null,
      status: "active",
      createdOn: "2026-08-16",
      closedOn: null,
    });
  });

  it("opens a group with members who joined and left on Kampala days", async () => {
    const fetch = routes({
      [`/staff/goal-groups/${GROUP.group_id}`]: () =>
        reply(200, {
          group: GROUP,
          progress: { value_minor: 280_000, percent: 23, standing: "no_date" },
          members: [
            {
              goal_id: "-NgoalM0000000000001",
              client_id: GROUP.owner_client_id,
              display_name: "Aunt Rose",
              joined_at: "2026-08-16T21:15:00Z",
              left_at: null,
              value_minor: 280_000,
              value_on: "2026-11-13",
            },
            {
              goal_id: "-NgoalM0000000000002",
              client_id: "-Nclient00000000000C",
              display_name: "Moses",
              joined_at: "2026-08-20T10:00:00Z",
              left_at: "2026-10-01T22:00:00Z",
              value_minor: 0,
              value_on: null,
            },
          ],
        }),
    });
    const detail = await createHttpGroupsApi(createHttp("https://api.test", fetch)).getGroup(GROUP.group_id);
    expect(detail.progress).toEqual({ value: "280000", percent: 23, standing: "no_date" });
    expect(detail.members).toEqual([
      { goalId: "-NgoalM0000000000001", clientId: GROUP.owner_client_id, displayName: "Aunt Rose", joinedOn: "2026-08-17", leftOn: null, value: "280000", valueOn: "2026-11-13" },
      { goalId: "-NgoalM0000000000002", clientId: "-Nclient00000000000C", displayName: "Moses", joinedOn: "2026-08-20", leftOn: "2026-10-02", value: "0", valueOn: null },
    ]);
  });

  it("says when a group no longer exists", async () => {
    const fetch = routes({});
    expect(await createHttpGroupsApi(createHttp("https://api.test", fetch)).getGroup("-Nmissing").catch((e: unknown) => e)).toMatchObject({
      code: "not_found",
    });
  });
});
