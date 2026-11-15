import { runningSummary } from "@/home/format";

const none = { minor: 0, currency: "UGX" as const };

describe("the running count on Home and Portfolio", () => {
  it("counts goal savings and names them, so it adds up with Home's two rows", () => {
    expect(runningSummary({ activeCount: 3, inGoals: { worth: none, count: 1 } })).toBe("3 running, 1 in a goal");
    expect(runningSummary({ activeCount: 4, inGoals: { worth: none, count: 2 } })).toBe("4 running, 2 in goals");
  });

  it("says only the count when nothing is in a goal", () => {
    expect(runningSummary({ activeCount: 2, inGoals: { worth: none, count: 0 } })).toBe("2 running");
  });
});
