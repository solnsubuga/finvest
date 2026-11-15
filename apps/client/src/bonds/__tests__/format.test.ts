import { couponsLabel, formatPrice, priceLabel } from "../format";

describe("bond prices", () => {
  it("are shown per 100 from the integer, to between two and four decimals", () => {
    expect(formatPrice(985_000)).toBe("98.50");
    expect(formatPrice(1_000_000)).toBe("100.00");
    expect(formatPrice(1_012_500)).toBe("101.25");
    expect(formatPrice(998_750)).toBe("99.875");
    expect(formatPrice(1_000_001)).toBe("100.0001");
    expect(priceLabel(null)).toBe("No price yet");
    expect(() => formatPrice(98.5)).toThrow();
  });
});

describe("coupon wording", () => {
  it("names the next coupon, or the last one at maturity", () => {
    expect(couponsLabel({ nextCouponDate: "2027-05-14", couponsLeft: 21 })).toBe("Every six months, next on 14 May 2027");
    expect(couponsLabel({ nextCouponDate: "2037-05-14", couponsLeft: 1 })).toBe("One left, paid with the face value on 14 May 2037");
    expect(couponsLabel({ nextCouponDate: null, couponsLeft: 0 })).toBe("All paid");
  });
});
