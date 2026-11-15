import { earnedLabel, earningFrom } from "../format";

describe("how long the interest shown has been earning", () => {
  it("counts the days of an investment that began its own term", () => {
    expect(earnedLabel({ daysEarned: 12, termDays: 91, startDate: "2026-11-01", maturityDate: "2027-01-31" })).toBe("12 days earned");
  });

  it("names the day a kept part began, since its interest is only its own", () => {
    // The term began on 1 Nov. Some was sent on 6 Nov and this part carried on from then.
    expect(earnedLabel({ daysEarned: 12, termDays: 91, startDate: "2026-11-06", maturityDate: "2027-01-31" })).toBe(
      "earning since 6 Nov 2026",
    );
  });

  it("counts days for an open term", () => {
    expect(earnedLabel({ daysEarned: 3, termDays: null, startDate: "2026-11-06", maturityDate: null })).toBe("3 days earned");
  });
});

describe("when a paid investment starts earning", () => {
  it("is the day after it starts, which a weekend payment moves to Monday's start", () => {
    // Paid on Saturday 14 Nov: it starts on Monday 16 Nov and earns from Tuesday.
    expect(earningFrom("2026-11-16")).toBe("starts earning on 17 Nov 2026");
    expect(earningFrom("2026-12-31")).toBe("starts earning on 1 Jan 2027");
  });

  it("says it in general words before the start date is known", () => {
    expect(earningFrom(null)).toBe("starts earning the day after the business day it was paid");
  });
});
