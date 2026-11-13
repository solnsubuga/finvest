import { daysUntil, earnedBars, thousands } from "../chart";
import { createStubInsightsClient } from "../stubInsightsClient";
import { InsightsMonth } from "../types";

const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

function month(start: string, earned: number): InsightsMonth {
  return {
    month: start,
    invested: ugx(0),
    earned: ugx(earned),
    paidOut: ugx(0),
  };
}

it("labels bars in thousands without rounding up", () => {
  expect(thousands(31_408)).toBe("31.4");
  expect(thousands(31_499)).toBe("31.4");
  expect(thousands(0)).toBe("0.0");
  expect(thousands(1_234_567)).toBe("1,234.5");
});

it("scales bars to the tallest month and marks the current one", () => {
  const bars = earnedBars([month("2026-10-01", 61_800), month("2026-11-01", 30_900)], 100);
  expect(bars.map((bar) => [bar.label, bar.height, bar.current])).toEqual([
    ["Oct", 100, false],
    ["Nov", 50, true],
  ]);
  expect(earnedBars([month("2026-11-01", 0)], 100)[0]?.height).toBe(4);
});

it("counts days to a date", () => {
  expect(daysUntil("2027-01-09", "2026-11-13")).toBe(57);
});

it("has history for the demo accounts and none for a new one", async () => {
  const now = () => new Date("2026-11-13T09:00:00Z");
  const demo = await createStubInsightsClient("+256772123456", now).getInsights(6);
  expect(demo.months.map((m) => m.month)).toEqual([
    "2026-06-01",
    "2026-07-01",
    "2026-08-01",
    "2026-09-01",
    "2026-10-01",
    "2026-11-01",
  ]);
  expect(demo.months[5]?.earned).toEqual(ugx(31_408));
  const fresh = await createStubInsightsClient("+256772555010", now).getInsights(6);
  expect(fresh.months.every((m) => m.earned.minor === 0)).toBe(true);
  expect(fresh.upcoming).toEqual([]);
  await expect(createStubInsightsClient("+256772555099", now).getInsights(6)).rejects.toThrow();
});
