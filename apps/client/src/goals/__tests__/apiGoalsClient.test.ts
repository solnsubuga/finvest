import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiGoalsClient } from "../apiGoalsClient";
import { GoalError, outcomeUnknown } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ID = "GGGGGGGGGGGGGGGGGGGG";
const INV = "IIIIIIIIIIIIIIIIIIII";

const plan = {
  product_code: "TB91",
  product_name: "91 Day Treasury Bill",
  rate_percent: "10.63",
  term_days: 91,
  currency: "UGX",
  saving_minor: 300000,
  savings: 10,
  first_on: "2026-11-16",
  last_on: "2027-08-16",
  put_in_minor: 3000000,
  interest_minor: 120000,
  without_investing_minor: 3000000,
  raised_to_minimum: false,
};

const saving = {
  investment_id: INV,
  product_code: "TB91",
  product_name: "91 Day Treasury Bill",
  rate_percent: "10.63",
  status: "awaiting_payment",
  amount_minor: 300000,
  value_minor: 0,
  interest_minor: 0,
  at_maturity: "roll_over",
  pay_by: "2026-11-14T12:00:00Z",
  start_date: null,
  maturity_date: null,
};

const goal = {
  goal_id: ID,
  name: "School fees",
  category: "school_fees",
  currency: "UGX",
  target_minor: 3000000,
  target_date: "2027-09-01",
  status: "active",
  product_code: "TB91",
  created_at: "2026-11-13T10:00:00Z",
  progress: { value_minor: 0, interest_minor: 0, awaiting_payment_minor: 300000, percent: 0, standing: "on_track" },
  plan,
  saving_rule: null,
  savings: [saving],
};

type Seen = { method: string; path: string; key: string | undefined; body: unknown };

function serve(status: number, body: unknown): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    seen.push({
      method: String(init?.method),
      path: String(url).replace(API_URL, ""),
      key: headers["Idempotency-Key"],
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(status: number, code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields } };
}

afterEach(() => setFetch(null));

describe("the goals client for the real API", () => {
  it("asks for a plan by amount and date and passes the after-tax figures through", async () => {
    const seen = serve(200, plan);
    const result = await createApiGoalsClient().getPlan({ minor: 3000000, currency: "UGX" }, "2027-09-01");
    expect(seen[0]!.path).toBe("/client/goals/plan?target_minor=3000000&target_date=2027-09-01&currency=UGX");
    expect(result).toMatchObject({
      productName: "91 Day Treasury Bill",
      ratePercent: "10.63",
      saving: { minor: 300000, currency: "UGX" },
      interest: { minor: 120000, currency: "UGX" },
    });
  });

  it("says when no plan fits", async () => {
    serve(200, null);
    await expect(createApiGoalsClient().getPlan({ minor: 1, currency: "UGX" }, "2027-09-01")).resolves.toBeNull();
  });

  it("creates a goal under the screen's idempotency key", async () => {
    const seen = serve(201, goal);
    const made = await createApiGoalsClient().createGoal(
      { name: "School fees", category: "school_fees", target: { minor: 3000000, currency: "UGX" }, targetDate: "2027-09-01" },
      "key-1",
    );
    expect(seen[0]).toMatchObject({
      method: "POST",
      path: "/client/goals",
      key: "key-1",
      body: { name: "School fees", category: "school_fees", target_minor: 3000000, currency: "UGX", target_date: "2027-09-01" },
    });
    expect(made.savings[0]).toMatchObject({ investmentId: INV, status: "awaiting_payment", amount: { minor: 300000 } });
    expect(made.progress.awaitingPayment).toEqual({ minor: 300000, currency: "UGX" });
  });

  it("changes a goal with PUT, with no idempotency key, and says when it is closed", async () => {
    const seen = serve(200, { ...goal, name: "University fees", target_minor: 5000000 });
    const changed = await createApiGoalsClient().updateGoal(ID, {
      name: "University fees",
      category: "school_fees",
      target: { minor: 5000000, currency: "UGX" },
      targetDate: null,
    });
    expect(seen[0]).toMatchObject({
      method: "PUT",
      path: `/client/goals/${ID}`,
      key: undefined,
      body: { name: "University fees", category: "school_fees", target_minor: 5000000, currency: "UGX", target_date: null },
    });
    expect(changed).toMatchObject({ name: "University fees", target: { minor: 5000000, currency: "UGX" } });
    serve(409, refusal(409, "conflict"));
    await expect(
      createApiGoalsClient().updateGoal(ID, { name: "x", category: "other", target: { minor: 1, currency: "UGX" }, targetDate: null }),
    ).rejects.toMatchObject({ code: "closed" });
  });

  it("saves to a goal and hands back the investment to pay for", async () => {
    const seen = serve(201, saving);
    const made = await createApiGoalsClient().saveToGoal(ID, { minor: 300000, currency: "UGX" }, "key-2");
    expect(seen[0]).toMatchObject({ path: `/client/goals/${ID}/savings`, key: "key-2", body: { amount_minor: 300000, currency: "UGX" } });
    expect(made.investmentId).toBe(INV);
  });

  it("turns refusals into the reasons the screens show", async () => {
    const client = createApiGoalsClient();
    const money = { minor: 300000, currency: "UGX" as const };
    const details = { name: "x", category: "other" as const, target: money, targetDate: null };

    serve(409, refusal(409, "conflict"));
    await expect(client.createGoal(details, "k")).rejects.toEqual(new GoalError("too_many_goals"));
    await expect(client.saveToGoal(ID, money, "k")).rejects.toEqual(new GoalError("closed"));
    await expect(client.closeGoal(ID)).rejects.toEqual(new GoalError("closed"));

    serve(422, refusal(422, "validation_failed", [{ field_path: ["target_date"], code: "sooner_than_every_term" }]));
    await expect(client.getPlan(money, "2026-12-01")).rejects.toEqual(new GoalError("sooner_than_every_term"));

    serve(422, refusal(422, "validation_failed", [{ field_path: ["amount"], code: "below_minimum" }]));
    await expect(client.saveToGoal(ID, money, "k")).rejects.toEqual(new GoalError("below_minimum"));

    serve(422, refusal(422, "validation_failed", [{ field_path: ["name"], code: "name_empty" }]));
    await expect(client.createGoal(details, "k")).rejects.toEqual(new GoalError("invalid"));

    serve(403, refusal(403, "forbidden"));
    await expect(client.saveToGoal(ID, money, "k")).rejects.toEqual(new GoalError("not_verified"));
    // Only a saving is an investment, so only a saving blames the ID check.
    await expect(client.createGoal(details, "k")).rejects.toEqual(new GoalError("not_allowed"));
    await expect(client.closeGoal(ID)).rejects.toEqual(new GoalError("not_allowed"));

    serve(422, refusal(422, "idempotency_key_reused"));
    await expect(client.saveToGoal(ID, money, "k")).rejects.toEqual(new GoalError("key_reused"));

    serve(404, refusal(404, "not_found"));
    await expect(client.getGoal(ID)).rejects.toEqual(new GoalError("not_found"));
  });

  it("treats a lost connection as an outcome it cannot know", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    const error = await createApiGoalsClient()
      .saveToGoal(ID, { minor: 300000, currency: "UGX" }, "k")
      .catch((e: unknown) => e);
    expect(error).toEqual(new GoalError("offline"));
    expect(outcomeUnknown(error)).toBe(true);
    expect(outcomeUnknown(new GoalError("closed"))).toBe(false);
  });

  it("reads the goal again when the answer to closing it is lost", async () => {
    const closed = { ...goal, status: "closed" };
    const seen: string[] = [];
    let answers = 0;
    setFetch(async (url, init) => {
      seen.push(`${String(init?.method)} ${String(url).replace(API_URL, "")}`);
      answers += 1;
      if (answers === 1) throw new TypeError("Network request failed");
      return new Response(JSON.stringify(closed), { status: 200 });
    });
    await expect(createApiGoalsClient().closeGoal(ID)).resolves.toMatchObject({ status: "closed" });
    expect(seen).toEqual([`POST /client/goals/${ID}/close`, `GET /client/goals/${ID}`]);
  });

  it("still says closing failed when the goal reads as open", async () => {
    let answers = 0;
    setFetch(async () => {
      answers += 1;
      if (answers === 1) throw new TypeError("Network request failed");
      return new Response(JSON.stringify(goal), { status: 200 });
    });
    await expect(createApiGoalsClient().closeGoal(ID)).rejects.toEqual(new GoalError("offline"));
  });

  it("says closing failed, as it was, when the goal cannot be read either", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(createApiGoalsClient().closeGoal(ID)).rejects.toEqual(new GoalError("offline"));
  });
});
