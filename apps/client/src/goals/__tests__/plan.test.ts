import { addMonths, bestPlan, daysBetween, DEMO_PRODUCTS, netInterest } from "@/goals/plan";

const BILL_91 = DEMO_PRODUCTS[0]!;

describe("the demo plan", () => {
  it("earns what the product screen shows on a million for one term", () => {
    expect(netInterest(1_000_000, BILL_91, 91)).toBe(26_490);
  });

  it("matches the backend's plan for two million a year away", () => {
    // backend/tests: TREA-91, 189,000 a month for 10 months, interest 110,146.
    const plan = bestPlan(2_000_000, "2026-11-13", "2027-11-13");
    expect(plan).toMatchObject({
      productCode: "TREA-91",
      saving: { currency: "UGX", minor: 189_000 },
      savings: 10,
      firstOn: "2026-11-13",
      lastOn: "2027-08-13",
      putIn: { minor: 1_890_000 },
      interest: { minor: 110_146 },
      withoutInvesting: { minor: 200_000 },
      raisedToMinimum: false,
    });
  });

  it("has no plan when every term ends after the date", () => {
    expect(bestPlan(2_000_000, "2026-11-13", "2027-01-01")).toBeNull();
  });

  it("asks for the product minimum when the target needs less", () => {
    expect(bestPlan(150_000, "2026-11-13", "2027-11-13")).toMatchObject({
      saving: { minor: 100_000 },
      raisedToMinimum: true,
    });
  });

  it("keeps months on the same day, the 28th at most", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-11-13", 14)).toBe("2028-01-13");
    expect(daysBetween("2026-11-13", "2027-02-12")).toBe(91);
  });
});
