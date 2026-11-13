import { groupDigits, kampalaToday, monthYear, parseShillings } from "@/goals/amount";

describe("typing an amount", () => {
  it("groups digits as they are typed and drops anything else", () => {
    expect(groupDigits("2000000")).toBe("2,000,000");
    expect(groupDigits("2,000,0001")).toBe("20,000,001");
    expect(groupDigits("007")).toBe("7");
    expect(groupDigits("1.5m")).toBe("15");
  });

  it("reads whole shillings only", () => {
    expect(parseShillings("2,000,000")).toBe(2_000_000);
    expect(parseShillings("")).toBeNull();
    expect(parseShillings("0")).toBeNull();
    expect(parseShillings("1.5")).toBeNull();
    expect(parseShillings("12345678901234")).toBeNull();
  });

  it("takes today in Kampala, three hours ahead of UTC", () => {
    expect(kampalaToday(Date.parse("2026-11-13T21:30:00Z"))).toBe("2026-11-14");
    expect(monthYear("2028-11-13")).toBe("November 2028");
  });
});
