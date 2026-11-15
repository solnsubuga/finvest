import { describe, expect, it } from "vitest";
import { holdingName } from "./labels.ts";

const LINE = { accountCode: "client.holding", accountReference: "-NinvestA00000000042", minor: 2_500_000, currency: "UGX" as const };

describe("holding names", () => {
  it("names a holding by its product and maturity, never its ID", () => {
    expect(holdingName({ ...LINE, productName: "364 Day Treasury Bill", maturesOn: "2027-08-12" })).toBe("364 Day Treasury Bill, matures 12 Aug 2027");
  });

  it("names an open term by its product alone, and a line not on an investment not at all", () => {
    expect(holdingName({ ...LINE, productName: "Flexi Savings", maturesOn: null })).toBe("Flexi Savings");
    expect(holdingName({ ...LINE, accountReference: null, productName: null, maturesOn: null })).toBeNull();
  });
});
