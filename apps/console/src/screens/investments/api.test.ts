import { describe, expect, it } from "vitest";
import { choiceLabel } from "./api.ts";

describe("choiceLabel", () => {
  it("names the goal that takes the interest", () => {
    expect(choiceLabel({ atMaturity: "interest_to_goal", interestGoalName: "School fees" })).toBe("Roll over, interest to goal: School fees");
  });

  it("keeps the plain labels for the other choices", () => {
    expect(choiceLabel({ atMaturity: "roll_over", interestGoalName: null })).toBe("Roll over");
    expect(choiceLabel({ atMaturity: null, interestGoalName: null })).toBe("Open term");
  });
});
