import {
  earliestFrom,
  monthLabel,
  parseDay,
  periodLabel,
  periodProblem,
  pickFor,
  problemCopy,
  quickPeriod,
} from "../periods";

const TODAY = "2026-11-13";

describe("quick picks", () => {
  it("means the right dates", () => {
    expect(quickPeriod("last_month", TODAY)).toEqual({ from: "2026-10-01", to: "2026-10-31" });
    expect(quickPeriod("last_3_months", TODAY)).toEqual({ from: "2026-08-01", to: TODAY });
    expect(quickPeriod("this_year", TODAY)).toEqual({ from: "2026-01-01", to: TODAY });
  });

  it("crosses the year in January", () => {
    expect(quickPeriod("last_month", "2027-01-05")).toEqual({ from: "2026-12-01", to: "2026-12-31" });
    expect(quickPeriod("last_3_months", "2027-02-10")).toEqual({ from: "2026-11-01", to: "2027-02-10" });
  });

  it("recognises a period that is a pick", () => {
    expect(pickFor("2026-10-01", "2026-10-31", TODAY)).toBe("last_month");
    expect(pickFor("2026-10-02", "2026-10-31", TODAY)).toBeNull();
  });
});

describe("a typed date", () => {
  it.each([
    ["1 Aug 2026", "2026-08-01"],
    ["01 august 2026", "2026-08-01"],
    ["13 Nov, 2026", "2026-11-13"],
    ["1/8/2026", "2026-08-01"],
    ["2026-08-01", "2026-08-01"],
    ["29 Feb 2028", "2028-02-29"],
  ])("reads %s", (text, day) => {
    expect(parseDay(text)).toBe(day);
  });

  it.each(["", "Aug 2026", "31 Feb 2026", "29 Feb 2026", "1 Smarch 2026", "13/13/2026", "2026-8"])(
    "refuses %s",
    (text) => {
      expect(parseDay(text)).toBeNull();
    },
  );
});

describe("the backend's rules", () => {
  it("refuses a backwards, future or too long period", () => {
    expect(periodProblem("2026-11-20", TODAY, TODAY)).toBe("period_backwards");
    expect(periodProblem("2026-11-01", "2026-11-14", TODAY)).toBe("period_in_future");
    expect(periodProblem("2025-11-12", TODAY, TODAY)).toBe("period_too_long");
  });

  it("takes up to 366 days, both ends counted", () => {
    expect(periodProblem(earliestFrom(TODAY), TODAY, TODAY)).toBeNull();
    expect(earliestFrom(TODAY)).toBe("2025-11-13");
    expect(periodProblem(TODAY, TODAY, TODAY)).toBeNull();
  });

  it("says which date to change", () => {
    expect(problemCopy("period_backwards", "2026-11-20", TODAY, TODAY)).toEqual({
      field: "from",
      message: "The start date is after the end date. Pick a start date on or before 13 Nov 2026.",
    });
    expect(problemCopy("period_in_future", "2026-11-01", "2026-11-14", TODAY).field).toBe("to");
    expect(problemCopy("period_too_long", "2025-01-01", TODAY, TODAY).message).toContain(
      "on or after 13 Nov 2025",
    );
  });
});

it("labels periods and months", () => {
  expect(periodLabel("2026-08-01", TODAY)).toBe("1 Aug 2026 to 13 Nov 2026");
  expect(monthLabel("2026-10-01")).toBe("October 2026");
});
