import { describe, expect, it } from "vitest";
import type { MaturityDay } from "./api.ts";
import { comingUp, maturitiesCsv, maturitiesFilename } from "./export.ts";

const day = (on: string, more: Partial<MaturityDay> = {}): MaturityDay => ({
  day: on,
  currency: "UGX",
  investments: 2,
  principal: { minor: 3_000_000, currency: "UGX" },
  rolledOver: 0,
  paidOut: 0,
  held: 0,
  openRollOver: 1,
  openPayOut: 1,
  ...more,
});

describe("maturities export", () => {
  it("writes one line per day and currency, whole numbers only, no client", () => {
    const csv = maturitiesCsv([day("2026-11-14", { rolledOver: 2, openRollOver: 0, openPayOut: 0 }), day("2026-11-20")]);
    expect(csv.split("\r\n")).toEqual([
      "Day,Currency,Terms,Principal (smallest unit),Rolled over,Paid out,Held,Still to roll over,Still to pay out",
      "2026-11-14,UGX,2,3000000,2,0,0,0,0",
      "2026-11-20,UGX,2,3000000,0,0,0,1,1",
      "",
    ]);
    expect(maturitiesFilename("2026-11-15")).toBe("finvest-maturities-2026-11-15.csv");
  });

  it("adds up only the days after today, per currency", () => {
    const ahead = comingUp(
      [day("2026-11-15"), day("2026-11-16"), day("2026-11-20", { openPayOut: 2 }), day("2026-11-21", { currency: "USD", principal: { minor: 500, currency: "USD" } })],
      "2026-11-15",
    );
    expect(ahead).toEqual([
      { currency: "UGX", terms: 4, principal: 6_000_000, rollOver: 2, payOut: 3 },
      { currency: "USD", terms: 2, principal: 500, rollOver: 1, payOut: 1 },
    ]);
  });
});
