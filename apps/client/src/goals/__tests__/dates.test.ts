import { kampalaToday, nextPayday } from "@/goals/dates";

describe("nextPayday", () => {
  it("is this month's payday when it has not passed", () => {
    expect(nextPayday(25, "2026-11-15")).toBe("2026-11-25");
  });

  it("is today when today is payday", () => {
    expect(nextPayday(15, "2026-11-15")).toBe("2026-11-15");
  });

  it("is next month's once this month's has passed, across a year end", () => {
    expect(nextPayday(10, "2026-11-15")).toBe("2026-12-10");
    expect(nextPayday(10, "2026-12-15")).toBe("2027-01-10");
  });

  it("falls on the last day of a short month", () => {
    expect(nextPayday(31, "2027-02-01")).toBe("2027-02-28");
    expect(nextPayday(31, "2026-11-30")).toBe("2026-11-30");
    expect(nextPayday(30, "2027-01-31")).toBe("2027-02-28");
  });
});

describe("kampalaToday", () => {
  it("is already tomorrow in Kampala after 21:00 UTC", () => {
    expect(kampalaToday(Date.UTC(2026, 10, 15, 21, 30))).toBe("2026-11-16");
    expect(kampalaToday(Date.UTC(2026, 10, 15, 20, 59))).toBe("2026-11-15");
  });
});
