import { createStubGoalsClient } from "@/goals/stubGoalsClient";
import { GoalError } from "@/goals/types";
import { createStubPaymentsClient } from "@/payments/stubPaymentsClient";

const NOW = Date.parse("2026-11-13T09:00:00Z");
const ugx = (minor: number) => ({ currency: "UGX" as const, minor });
const school = {
  name: "School fees",
  category: "school_fees" as const,
  target: ugx(2_000_000),
  targetDate: "2027-11-13",
};

function client(seeded = false) {
  return createStubGoalsClient({ delayMs: 0, now: () => NOW, seeded });
}

describe("the demo goals", () => {
  it("starts empty for a new account and with the canvas's goals for the demo account", async () => {
    expect(await client().listGoals()).toEqual([]);
    const seeded = await client(true).listGoals();
    expect(seeded.map((goal) => goal.name)).toEqual(["Emergency fund", "School fees, Term 1"]);
    expect(seeded[1]?.progress.value.minor).toBeGreaterThan(1_560_000);
  });

  it("sets up a goal once per key and plans it", async () => {
    const goals = client();
    const first = await goals.createGoal(school, "key-0000000001");
    const again = await goals.createGoal(school, "key-0000000001");
    expect(again.id).toBe(first.id);
    expect(first.plan?.saving).toEqual(ugx(189_000));
    await expect(goals.createGoal({ ...school, name: "Other" }, "key-0000000001")).rejects.toEqual(
      new GoalError("key_reused"),
    );
    expect(await goals.listGoals()).toHaveLength(1);
  });

  it("changes a goal, works out its plan again, and refuses a closed goal", async () => {
    const goals = client();
    const made = await goals.createGoal(school, "key-0000000010");
    const changed = await goals.updateGoal(made.id, { ...school, name: "  University   fees ", target: ugx(4_000_000) });
    expect(changed.name).toBe("University fees");
    expect(changed.plan?.saving.minor).toBeGreaterThan(made.plan?.saving.minor ?? 0);
    await expect(goals.updateGoal(made.id, { ...school, targetDate: "2027-01-01" })).rejects.toEqual(
      new GoalError("sooner_than_every_term"),
    );
    await goals.closeGoal(made.id);
    await expect(goals.updateGoal(made.id, school)).rejects.toEqual(new GoalError("closed"));
  });

  it("refuses a date sooner than every term", async () => {
    await expect(
      client().createGoal({ ...school, targetDate: "2027-01-01" }, "key-0000000002"),
    ).rejects.toEqual(new GoalError("sooner_than_every_term"));
  });

  it("makes a saving the pay screens can pay for, once per key", async () => {
    const goals = client();
    const goal = await goals.createGoal(school, "key-0000000003");
    const saving = await goals.saveToGoal(goal.id, ugx(200_000), "key-0000000004");
    const again = await goals.saveToGoal(goal.id, ugx(200_000), "key-0000000004");
    expect(again.investmentId).toBe(saving.investmentId);
    await expect(goals.saveToGoal(goal.id, ugx(300_000), "key-0000000004")).rejects.toEqual(
      new GoalError("key_reused"),
    );
    expect(saving.status).toBe("awaiting_payment");
    const payable = await createStubPaymentsClient({ delayMs: 0 }).getPayable(saving.investmentId);
    expect(payable).toEqual({
      id: saving.investmentId,
      kind: "investment",
      title: "School fees: 91 Day Treasury Bill",
      amount: ugx(200_000),
    });
    const after = await goals.getGoal(goal.id);
    expect(after.progress.awaitingPayment).toEqual(ugx(200_000));
    expect(after.savings).toHaveLength(1);
  });

  it("refuses a saving below the minimum and any saving to a closed goal", async () => {
    const goals = client();
    const goal = await goals.createGoal(school, "key-0000000005");
    await expect(goals.saveToGoal(goal.id, ugx(99_999), "key-0000000006")).rejects.toEqual(
      new GoalError("below_minimum"),
    );
    await goals.closeGoal(goal.id);
    await expect(goals.saveToGoal(goal.id, ugx(200_000), "key-0000000007")).rejects.toEqual(
      new GoalError("closed"),
    );
  });

  it("answers not found for a goal it does not have", async () => {
    await expect(client().getGoal("nope")).rejects.toEqual(new GoalError("not_found"));
  });
});

describe("a saving taken out early in the demo", () => {
  it("is listed as withdrawn and leaves the goal's value", async () => {
    const goals = await createStubGoalsClient({ delayMs: 0, now: () => NOW, seeded: true }).listGoals();
    const emergency = goals.find((goal) => goal.name === "Emergency fund");
    const withdrawn = emergency?.savings.filter((saving) => saving.status === "withdrawn") ?? [];
    expect(withdrawn).toHaveLength(1);
    expect(withdrawn[0]?.value.minor).toBeGreaterThan(250_000);
    const running = emergency?.savings.find((saving) => saving.status === "active");
    expect(emergency?.progress.value).toEqual(running?.value);
  });

  it("locks a goal until its date and unlocks it seven days after asking", async () => {
    const goals = client();
    const made = await goals.createGoal(school, "key-0000000040");
    expect(made).toMatchObject({ lockedUntil: null, unlockRequestedAt: null, payday: null });
    await expect(goals.unlockGoal(made.id)).rejects.toEqual(new GoalError("invalid"));

    const locked = await goals.lockGoal(made.id);
    expect(locked).toMatchObject({ lockedUntil: "2027-11-13", unlockRequestedAt: null });
    const asked = await goals.unlockGoal(made.id);
    expect(asked.lockedUntil).toBe("2026-11-20");
    expect(asked.unlockRequestedAt).not.toBeNull();
    // Asking again changes nothing; locking again cancels the ask.
    expect((await goals.unlockGoal(made.id)).unlockRequestedAt).toBe(asked.unlockRequestedAt);
    expect(await goals.lockGoal(made.id)).toMatchObject({ lockedUntil: "2027-11-13", unlockRequestedAt: null });

    const closed = await goals.closeGoal(made.id);
    expect(closed).toMatchObject({ lockedUntil: "2026-11-20" });
    expect(closed.unlockRequestedAt).not.toBeNull();
  });

  it("refuses to lock a goal with no date, and names a payday from 1 to 31", async () => {
    const goals = client();
    const made = await goals.createGoal({ ...school, targetDate: null }, "key-0000000041");
    await expect(goals.lockGoal(made.id)).rejects.toEqual(new GoalError("invalid"));
    expect((await goals.setPayday(made.id, 31)).payday).toBe(31);
    expect((await goals.setPayday(made.id, null)).payday).toBeNull();
    await expect(goals.setPayday(made.id, 32)).rejects.toEqual(new GoalError("invalid"));
  });
});
