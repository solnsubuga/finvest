import { lockState, paydayLine } from "@/goals/Keep";
import { Goal } from "@/goals/types";

const ugx = (minor: number) => ({ currency: "UGX" as const, minor });
const goal: Goal = {
  id: "g",
  name: "School fees",
  category: "school_fees",
  target: ugx(2_000_000),
  targetDate: "2027-11-13",
  status: "active",
  productCode: "TREA-91",
  createdAt: "2026-11-13T09:00:00Z",
  progress: { value: ugx(0), interest: ugx(0), awaitingPayment: ugx(0), percent: 0, standing: "on_track" },
  plan: null,
  savings: [],
  payday: null,
  lockedUntil: null,
  unlockRequestedAt: null,
};

describe("a goal's lock", () => {
  it("can be locked only while open with a date still to come", () => {
    expect(lockState(goal, "2026-11-14")).toBe("can_lock");
    expect(lockState({ ...goal, targetDate: null }, "2026-11-14")).toBe("none");
    expect(lockState({ ...goal, targetDate: "2026-11-14" }, "2026-11-14")).toBe("none");
    expect(lockState({ ...goal, status: "closed" }, "2026-11-14")).toBe("none");
  });

  it("shows a running lock and an unlock that was asked for, even on a closed goal", () => {
    expect(lockState({ ...goal, lockedUntil: "2027-11-13" })).toBe("locked");
    const asked = { ...goal, lockedUntil: "2026-11-21", unlockRequestedAt: "2026-11-14T10:00:00Z" };
    expect(lockState(asked)).toBe("unlocking");
    expect(lockState({ ...asked, status: "closed" })).toBe("unlocking");
  });

  it("words the payday reminder", () => {
    expect(paydayLine(goal)).toBeNull();
    expect(paydayLine({ ...goal, payday: 25 })).toBe("We remind you on the 25th of each month");
    expect(paydayLine({ ...goal, payday: 1 })).toBe("We remind you on the 1st of each month");
  });
});
