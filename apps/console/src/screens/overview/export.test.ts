import { describe, expect, it } from "vitest";
import type { Figures } from "./figures.ts";
import { figuresCsv, figuresFilename } from "./export.ts";

const ugx = (minor: number) => ({ minor, currency: "UGX" as const });

const figures: Figures = {
  today: "2026-11-15",
  currency: "UGX",
  money: {
    clientMoneyHeld: ugx(1_000),
    inTransit: ugx(20),
    awaitingStart: ugx(30),
    clientsInvested: 4,
    clientsVerified: 5,
    interest: null,
    days: [{ day: "2026-11-15", moneyIn: ugx(7), moneyOut: ugx(3) }],
  },
  floats: [{ network: "mtn", balance: ugx(900), warningLine: ugx(100), low: false }],
  balancing: null,
  maturities: null,
  holdings: [{ productCode: "X", productName: "=SUM(A1), bills", investments: 1, clients: 1, principal: ugx(50) }],
  attention: [],
};

describe("overview export", () => {
  it("writes the totals on screen, and keeps staff-set names from reading as formulas", () => {
    expect(figuresCsv(figures).split("\r\n")).toEqual([
      "Section,Item,\"Value (UGX, smallest unit, or a count)\"",
      "Client money,Held,1000",
      "Client money,In transit,20",
      "Client money,\"Paid in, waiting to start\",30",
      "Clients,Invested,4",
      "Clients,Verified,5",
      "\"Money in, 2026-11-15\",In,7",
      "\"Money out, 2026-11-15\",Out,3",
      "Mobile money float,MTN,900",
      "Where it sits,\"'=SUM(A1), bills\",50",
      "",
    ]);
    expect(figuresFilename(figures)).toBe("finvest-overview-ugx-2026-11-15.csv");
  });

  it("leaves out a section the role may not see", () => {
    expect(figuresCsv({ ...figures, money: null, floats: null, holdings: null })).toBe(
      "Section,Item,\"Value (UGX, smallest unit, or a count)\"\r\n",
    );
  });
});
