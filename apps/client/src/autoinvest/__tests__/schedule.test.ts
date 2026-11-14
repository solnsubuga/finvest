import {
  firstRun,
  formatDay,
  formatLongDay,
  kampalaToday,
  nextAfter,
  ordinal,
  parseShillings,
  runReason,
  scheduleLabel,
} from "../schedule";

// Friday 13 November 2026, the same day as the backend's tests.
const FRIDAY = "2026-11-13";

function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
}

describe("first run", () => {
  it("is never the day the plan is set up, as on the backend", () => {
    expect(firstRun("weekly", 5, FRIDAY)).toBe("2026-11-20");
    expect(firstRun("weekly", 6, FRIDAY)).toBe("2026-11-14");
    expect(firstRun("monthly", 13, FRIDAY)).toBe("2026-12-13");
    expect(firstRun("monthly", 28, FRIDAY)).toBe("2026-11-28");
    expect(firstRun("monthly", 31, "2026-12-31")).toBe("2027-01-31");
  });

  it("uses a month's last day when the month lacks the day", () => {
    expect(nextAfter("monthly", 31, "2026-04-01")).toBe("2026-04-30");
    expect(nextAfter("monthly", 31, "2027-02-01")).toBe("2027-02-28");
    expect(nextAfter("monthly", 31, "2028-02-01")).toBe("2028-02-29");
    expect(nextAfter("monthly", 30, "2027-02-27")).toBe("2027-02-28");
  });

  it("lands on the plan's weekday within the next seven days, every day of two years", () => {
    for (let offset = 0; offset < 730; offset += 1) {
      const day = addDays("2026-01-01", offset);
      for (let weekday = 1; weekday <= 7; weekday += 1) {
        const next = nextAfter("weekly", weekday, day);
        const gap = (Date.parse(next) - Date.parse(day)) / 86_400_000;
        expect(gap).toBeGreaterThanOrEqual(1);
        expect(gap).toBeLessThanOrEqual(7);
        const isoDay = new Date(`${next}T00:00:00Z`).getUTCDay() || 7;
        expect(isoDay).toBe(weekday);
      }
    }
  });

  it("runs a monthly plan once in every month", () => {
    for (const day of [1, 15, 28, 29, 30, 31]) {
      let run = "2025-12-31";
      const months = new Set<string>();
      for (let count = 0; count < 36; count += 1) {
        run = nextAfter("monthly", day, run);
        months.add(run.slice(0, 7));
      }
      expect(months.size).toBe(36);
    }
  });
});

describe("labels", () => {
  it("says when a plan runs", () => {
    expect(scheduleLabel("weekly", 5)).toBe("Every Friday");
    expect(scheduleLabel("monthly", 28)).toBe("Every month on the 28th");
    expect(scheduleLabel("monthly", 31)).toBe("Every month on the 31st, or the month's last day");
  });

  it("writes ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31].map(ordinal)).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "11th",
      "12th",
      "13th",
      "21st",
      "22nd",
      "23rd",
      "31st",
    ]);
  });

  it("writes days", () => {
    expect(formatDay("2026-11-28")).toBe("Sat 28 Nov");
    expect(formatLongDay("2027-01-09")).toBe("9 Jan 2027");
  });

  it("knows the Kampala day whatever the phone's zone", () => {
    expect(kampalaToday(new Date("2026-11-13T20:59:00Z"))).toBe("2026-11-13");
    expect(kampalaToday(new Date("2026-11-13T21:00:00Z"))).toBe("2026-11-14");
  });

  it("explains a missed or failed run", () => {
    expect(runReason(null, 1, "failed")).toMatch(/once more/);
    expect(runReason(null, 2, "failed")).toMatch(/Tried twice/);
    expect(runReason("late", 1, "missed")).toMatch(/rather than collect late/);
    expect(runReason("closing_account", 1, "missed")).toMatch(/close your account was being checked/);
    expect(runReason(null, 1, "paid")).toBeNull();
  });
});

describe("amounts", () => {
  it("takes whole shillings only", () => {
    expect(parseShillings("250,000")).toBe(250_000);
    expect(parseShillings(" 1 000 000 ")).toBe(1_000_000);
    expect(parseShillings("250000.5")).toBeNull();
    expect(parseShillings("-5")).toBeNull();
    expect(parseShillings("0")).toBeNull();
    expect(parseShillings("")).toBeNull();
    expect(parseShillings("1e6")).toBeNull();
  });
});
