import { describe, expect, it } from "vitest";
import { phoneNameVerdict } from "./labels.ts";

describe("phoneNameVerdict", () => {
  const phone = { registeredName: "Peter Okot", currentNumber: true, blocksApproval: false } as const;

  it("names the network's name only when it is not the ID's", () => {
    expect(phoneNameVerdict({ ...phone, status: "matches" })).toBe("Matches");
    expect(phoneNameVerdict({ ...phone, status: "different", blocksApproval: true })).toBe("Different name: Peter Okot");
    expect(phoneNameVerdict({ ...phone, status: "checking", registeredName: null })).toBe("Checking");
  });

  it("says when the result is about a number the client no longer uses", () => {
    expect(phoneNameVerdict({ ...phone, status: "different", currentNumber: false })).toBe(
      "Different name: Peter Okot (earlier number)",
    );
  });
});
