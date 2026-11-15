import { shortReference } from "../reference";

// The same pairs are in backend/tests/unit/test_short_references.py. Change both together.
const KNOWN: [string, string][] = [
  ["-P6TNMgBJ0qtT-C9Ed1Q", "FV-3R0D5-3X44V"],
  ["--------------------", "FV-00000-00000"],
  ["zzzzzzzzzzzzzzzzzzzz", "FV-7ZZZZ-ZZZZZ"],
  ["-P6TYZZTunCqFtwZMYmp", "FV-23KWJ-5WF5N"],
];

describe("shortReference", () => {
  it.each(KNOWN)("gives %s the same reference as the backend", (id, expected) => {
    expect(shortReference(id)).toBe(expected);
  });

  it("never starts with a dash and uses only characters that are easy to read out", () => {
    expect(shortReference("-P6TNMgBJ0qtT-C9Ed1Q")).toMatch(/^FV-[0-9A-HJKMNP-TV-Z]{5}-[0-9A-HJKMNP-TV-Z]{5}$/);
  });

  it("shows anything that is not a push ID as it is", () => {
    expect(shortReference("demo-activity-pay-3")).toBe("demo-activity-pay-3");
  });
});
