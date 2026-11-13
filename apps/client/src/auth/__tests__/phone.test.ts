import {
  formatE164,
  formatNational,
  isValidNational,
  maskE164,
  nationalDigits,
  toE164,
} from "../phone";

describe("nationalDigits", () => {
  it.each([
    ["772123456", "772123456"],
    ["772 123 456", "772123456"],
    ["0772123456", "772123456"],
    ["+256772123456", "772123456"],
    ["256772123456", "772123456"],
    ["+256 (0) 772-123-456", "772123456"],
    ["", ""],
    ["abc", ""],
  ])("reads %p as %p", (input, expected) => {
    expect(nationalDigits(input)).toBe(expected);
  });

  it("keeps a number that merely starts with 256 while it is still being typed", () => {
    expect(nationalDigits("256")).toBe("256");
    expect(nationalDigits("256123456")).toBe("256123456");
  });

  it("drops digits past the ninth", () => {
    expect(nationalDigits("77212345678")).toBe("772123456");
  });
});

describe("toE164", () => {
  it.each(["772123456", "0701555012", "+256 772 123 456", "256772123456"])(
    "accepts %p",
    (input) => {
      expect(toE164(input)).toMatch(/^\+2567\d{8}$/);
    },
  );

  it.each(["", "77212345", "412345678", "172123456", "hello"])("rejects %p", (input) => {
    expect(toE164(input)).toBeNull();
  });

  it("returns the full international number", () => {
    expect(toE164("0772 123 456")).toBe("+256772123456");
  });
});

describe("isValidNational", () => {
  it("needs nine digits starting with 7", () => {
    expect(isValidNational("772123456")).toBe(true);
    expect(isValidNational("77212345")).toBe(false);
    expect(isValidNational("872123456")).toBe(false);
  });
});

describe("formatting", () => {
  it("groups digits in threes as they are typed", () => {
    expect(formatNational("7")).toBe("7");
    expect(formatNational("7721")).toBe("772 1");
    expect(formatNational("772123456")).toBe("772 123 456");
  });

  it("formats and masks a stored number", () => {
    expect(formatE164("+256772123456")).toBe("+256 772 123 456");
    expect(maskE164("+256772123456")).toBe("+256 772 ••• 456");
  });
});
