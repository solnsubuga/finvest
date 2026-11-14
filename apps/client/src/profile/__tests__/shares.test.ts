import { bpsToPercent, evenShares, percentToBps } from "../shares";

describe("next of kin shares", () => {
  it("reads a percent a person types as whole basis points", () => {
    expect(percentToBps("60")).toBe(6_000);
    expect(percentToBps("33.33")).toBe(3_333);
    expect(percentToBps("0.5")).toBe(50);
    expect(percentToBps(" 100 ")).toBe(10_000);
  });

  it("refuses what is not a share", () => {
    for (const text of ["", "0", "100.01", "101", "1.234", "abc", "-5", "1e2"]) {
      expect(percentToBps(text)).toBeNull();
    }
  });

  it("writes basis points back as a percent", () => {
    expect(bpsToPercent(6_000)).toBe("60%");
    expect(bpsToPercent(3_334)).toBe("33.34%");
    expect(bpsToPercent(50)).toBe("0.5%");
  });

  it("splits 100% evenly, with the spare to the first person, always totalling 100%", () => {
    expect(evenShares(0)).toEqual([]);
    expect(evenShares(3)).toEqual([3_334, 3_333, 3_333]);
    for (let n = 1; n <= 5; n += 1) {
      expect(evenShares(n).reduce((a, b) => a + b, 0)).toBe(10_000);
    }
  });
});
