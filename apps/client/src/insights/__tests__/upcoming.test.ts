import { Upcoming, upcomingKey } from "../types";
import { upcomingHref, upcomingWords } from "../upcoming";

const amount = { currency: "UGX", minor: 200_000 } as const;

const run: Upcoming = {
  kind: "autoinvest_run",
  on: "2026-12-01",
  planId: "plan-1",
  productName: "91 Day Treasury Bill",
  goalId: null,
  goalName: null,
  amount,
};
const saving: Upcoming = {
  ...run,
  kind: "goal_saving",
  planId: "plan-2",
  goalId: "goal-1",
  goalName: "School fees",
};
const due: Upcoming = { kind: "goal_date", on: "2027-01-15", goalId: "goal-1", goalName: "School fees", amount };
const maturity: Upcoming = {
  kind: "maturity",
  on: "2026-12-20",
  investmentId: "inv-1",
  productName: "182 Day Treasury Bill",
  atMaturity: "pay_out",
  amount,
};

describe("coming up rows", () => {
  it("says what each day holds", () => {
    expect(upcomingWords(maturity)).toEqual({
      title: "182 Day Treasury Bill matures",
      detail: "Pays out to your account",
    });
    expect(upcomingWords({ ...maturity, atMaturity: "roll_over" }).detail).toBe("Rolls over and keeps earning");
    expect(upcomingWords({ ...maturity, atMaturity: "pay_interest" }).detail).toBe("Pays the interest, keeps the rest invested");
    expect(upcomingWords(run)).toEqual({
      title: "Automatic investment",
      detail: "Into 91 Day Treasury Bill, from your mobile money",
    });
    expect(upcomingWords(saving).title).toBe("Monthly saving for School fees");
    expect(upcomingWords(due)).toEqual({ title: "School fees is due", detail: "Your goal's target" });
  });

  it("opens the investment, plan or goal", () => {
    expect(upcomingHref(maturity)).toBe("/investment/inv-1");
    expect(upcomingHref(run)).toBe("/autoinvest/plan-1");
    expect(upcomingHref(saving)).toBe("/goals/goal-1");
    expect(upcomingHref(due)).toBe("/goals/goal-1");
  });

  it("keys a goal's saving and its date apart", () => {
    expect(new Set([run, saving, due, maturity].map(upcomingKey)).size).toBe(4);
  });
});
