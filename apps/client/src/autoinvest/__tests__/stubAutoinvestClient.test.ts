import { createStubAutoinvestClient, newStubStore } from "../stubAutoinvestClient";
import { AutoinvestError, NewPlan } from "../types";

const NOW = () => new Date("2026-11-13T09:00:00Z");
const NEW_CLIENT = "+256772555010";

function request(change: Partial<NewPlan> = {}): NewPlan {
  return {
    productCode: "TREA-91",
    amount: { currency: "UGX", minor: 250_000 },
    frequency: "monthly",
    day: 28,
    network: "mtn",
    payer: NEW_CLIENT,
    atMaturity: "roll_over",
    idempotencyKey: "key-0000000000000001",
    ...change,
  };
}

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    return error instanceof AutoinvestError ? error.code : "other";
  }
  return "none";
}

it("starts a new client with no plans and a demo account with one", async () => {
  const store = newStubStore();
  expect(await createStubAutoinvestClient(NEW_CLIENT, store, NOW).listPlans()).toEqual([]);
  const [demo] = await createStubAutoinvestClient("+256772123456", store, NOW).listPlans();
  expect(demo?.runs.map((run) => run.status)).toEqual(["paid", "missed", "paid"]);
});

it("makes one plan for one key, from its first due day after today", async () => {
  const client = createStubAutoinvestClient(NEW_CLIENT, newStubStore(), NOW);
  const plan = await client.createPlan(request());
  expect(plan.nextRunOn).toBe("2026-11-28");
  expect((await client.createPlan(request())).id).toBe(plan.id);
  expect(await client.listPlans()).toHaveLength(1);
});

it("refuses what a run could never pay for", async () => {
  const client = createStubAutoinvestClient(NEW_CLIENT, newStubStore(), NOW);
  expect(
    await code(client.createPlan(request({ amount: { currency: "UGX", minor: 99_999 } }))),
  ).toBe("below_minimum");
  expect(
    await code(client.createPlan(request({ amount: { currency: "UGX", minor: 5_000_001 } }))),
  ).toBe("amount_too_large");
});

it("returns the same plan for the same key, and refuses the key with other details", async () => {
  const client = createStubAutoinvestClient(NEW_CLIENT, newStubStore(), NOW);
  const first = await client.createPlan(request());
  expect((await client.createPlan(request())).id).toBe(first.id);
  expect(
    await code(client.createPlan(request({ amount: { currency: "UGX", minor: 300_000 } }))),
  ).toBe("key_reused");
  expect(await code(client.createPlan(request({ frequency: "weekly", day: 5 })))).toBe(
    "key_reused",
  );
  expect(await client.listPlans()).toHaveLength(1);
});

it("keeps at most ten open plans", async () => {
  const client = createStubAutoinvestClient(NEW_CLIENT, newStubStore(), NOW);
  for (let index = 0; index < 10; index += 1) {
    await client.createPlan(request({ idempotencyKey: `key-${index}-000000000000` }));
  }
  expect(await code(client.createPlan(request({ idempotencyKey: "key-11-00000000000" })))).toBe(
    "too_many_plans",
  );
});

it("pauses, resumes from the next due day and stops for good", async () => {
  const client = createStubAutoinvestClient(NEW_CLIENT, newStubStore(), NOW);
  const plan = await client.createPlan(request({ frequency: "weekly", day: 5 }));
  expect((await client.pausePlan(plan.id)).nextRunOn).toBeNull();
  expect((await client.resumePlan(plan.id)).nextRunOn).toBe("2026-11-20");
  expect((await client.stopPlan(plan.id)).status).toBe("stopped");
  expect(await client.listPlans()).toEqual([]);
});

it("fails to load for a number ending in 99", async () => {
  const client = createStubAutoinvestClient("+256772555099", newStubStore(), NOW);
  expect(await code(client.listPlans())).toBe("unavailable");
});
