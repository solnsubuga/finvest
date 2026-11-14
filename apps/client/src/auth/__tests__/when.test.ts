import { accountWhen } from "../when";

// 13 Nov 2026, 20:00 in Kampala.
const NOW = Date.parse("2026-11-13T17:00:00Z");

describe("account times, in Kampala", () => {
  it("says today, yesterday, a date, or a date with the year", () => {
    expect(accountWhen("2026-11-13T11:05:00Z", NOW)).toBe("today at 14:05");
    expect(accountWhen("2026-11-12T06:10:00Z", NOW)).toBe("yesterday at 09:10");
    expect(accountWhen("2026-11-03T15:00:00Z", NOW)).toBe("3 Nov at 18:00");
    expect(accountWhen("2025-12-31T20:30:00Z", NOW)).toBe("31 Dec 2025 at 23:30");
  });

  it("counts the day in Kampala, not in UTC", () => {
    // 22:30 UTC on the 12th is already 01:30 on the 13th in Kampala.
    expect(accountWhen("2026-11-12T22:30:00Z", NOW)).toBe("today at 01:30");
  });

  it("says nothing for a time it cannot read", () => {
    expect(accountWhen("not a time", NOW)).toBe("");
  });
});
