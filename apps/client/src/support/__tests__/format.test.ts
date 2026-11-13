import { clockTime, dateOf, dayHeading, plainDate, sameDay, shortWhen } from "../format";

// Friday 13 November 2026, 15:40 in Kampala.
const NOW = Date.parse("2026-11-13T12:40:00Z");

describe("when a notification arrived", () => {
  it("shows the Kampala time for today", () => {
    expect(shortWhen("2026-11-13T12:32:00Z", NOW)).toBe("15:32");
    expect(clockTime("2026-11-13T03:00:00Z")).toBe("06:00");
  });

  it("counts days in Kampala, not in UTC", () => {
    // 23:30 UTC on Thursday is already 02:30 on Friday in Kampala.
    expect(shortWhen("2026-11-12T23:30:00Z", NOW)).toBe("02:30");
    expect(shortWhen("2026-11-12T20:30:00Z", NOW)).toBe("Thu");
  });

  it("shows the weekday within a week, then the date", () => {
    expect(shortWhen("2026-11-10T09:00:00Z", NOW)).toBe("Tue");
    expect(shortWhen("2026-11-07T09:00:00Z", NOW)).toBe("Sat");
    expect(shortWhen("2026-11-06T09:00:00Z", NOW)).toBe("6 Nov");
    expect(shortWhen("2025-12-31T09:00:00Z", NOW)).toBe("31 Dec 2025");
  });
});

describe("the heading over a day of chat", () => {
  it("names today, yesterday and older days", () => {
    expect(dayHeading("2026-11-13T05:00:00Z", NOW)).toBe("Today");
    expect(dayHeading("2026-11-12T05:00:00Z", NOW)).toBe("Yesterday");
    expect(dayHeading("2026-11-10T05:00:00Z", NOW)).toBe("Tue 10 Nov");
  });

  it("knows when two messages share a Kampala day", () => {
    expect(sameDay("2026-11-12T21:30:00Z", "2026-11-13T12:00:00Z")).toBe(true);
    expect(sameDay("2026-11-12T20:30:00Z", "2026-11-13T12:00:00Z")).toBe(false);
  });
});

describe("dates", () => {
  it("writes a plain date without shifting it", () => {
    expect(plainDate("2026-11-20", NOW)).toBe("Fri 20 Nov");
    expect(plainDate("2027-01-04", NOW)).toBe("Mon 4 Jan 2027");
  });

  it("writes the day of an instant", () => {
    expect(dateOf("2026-11-13T12:00:00Z", NOW)).toBe("13 Nov");
  });
});
