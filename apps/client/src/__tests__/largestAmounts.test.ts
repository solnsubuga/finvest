import { targetTooLarge } from "@/goals/amount";
import { isAboveLargest, LARGEST_SINGLE_AMOUNT, largestSentence } from "@/investing/limits";

describe("the largest single investment and goal target", () => {
  it("matches the server: UGX 5 billion or USD 1.5 million, the figure itself allowed", () => {
    expect(LARGEST_SINGLE_AMOUNT.UGX.minor).toBe(5_000_000_000);
    expect(isAboveLargest({ minor: 5_000_000_000, currency: "UGX" })).toBe(false);
    expect(isAboveLargest({ minor: 5_000_000_001, currency: "UGX" })).toBe(true);
    expect(isAboveLargest({ minor: 150_000_000, currency: "USD" })).toBe(false);
    expect(isAboveLargest({ minor: 150_000_001, currency: "USD" })).toBe(true);
  });

  it("tells the client how to invest more than one investment holds", () => {
    expect(largestSentence("UGX")).toBe(
      "The most one investment can hold is UGX 5,000,000,000. Split a larger sum over several investments.",
    );
  });

  it("refuses a goal target above it", () => {
    expect(targetTooLarge(5_000_000_000)).toBeNull();
    expect(targetTooLarge(1_000_000_000_000)).toBe("The largest goal is UGX 5,000,000,000. Set a smaller target.");
  });
});
