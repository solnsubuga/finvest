import {
  checkPassword,
  isValidEmail,
  MIN_PASSWORD_LENGTH,
  passwordIsAcceptable,
  passwordStrength,
} from "../password";

const PHONE = "+256772123456";

describe("checkPassword", () => {
  it("accepts a long phrase typed twice", () => {
    const checks = checkPassword("green matooke sunday", "green matooke sunday", PHONE);
    expect(checks).toEqual({ longEnough: true, notPersonal: true, matches: true });
    expect(passwordIsAcceptable(checks)).toBe(true);
  });

  it("requires at least 10 characters", () => {
    expect(checkPassword("a".repeat(MIN_PASSWORD_LENGTH - 1), "", PHONE).longEnough).toBe(false);
    expect(checkPassword("a".repeat(MIN_PASSWORD_LENGTH), "", PHONE).longEnough).toBe(true);
  });

  it("requires both boxes to match", () => {
    const checks = checkPassword("green matooke sunday", "green matooke monday", PHONE);
    expect(checks.matches).toBe(false);
    expect(passwordIsAcceptable(checks)).toBe(false);
  });

  it.each(["amara772123456", "my 0772 123 456 pass", "pw-123456-xyz"])(
    "refuses %p because it contains the mobile number",
    (password) => {
      const checks = checkPassword(password, password, PHONE);
      expect(checks.notPersonal).toBe(false);
      expect(passwordIsAcceptable(checks)).toBe(false);
    },
  );

  it("shows nothing as met for an empty password", () => {
    expect(checkPassword("", "", PHONE)).toEqual({
      longEnough: false,
      notPersonal: false,
      matches: false,
    });
  });
});

describe("passwordStrength", () => {
  it("moves from empty to too short to good to strong", () => {
    expect(passwordStrength("")).toMatchObject({ label: "", ok: false });
    expect(passwordStrength("short")).toMatchObject({ label: "Too short", ok: false });
    expect(passwordStrength("tenletters")).toMatchObject({ label: "Good password", ok: true });
    expect(passwordStrength("green matooke sunday")).toMatchObject({
      label: "Strong password",
      ok: true,
    });
  });
});

describe("isValidEmail", () => {
  it.each(["amara@example.com", " amara.nakato@example.co.ug "])("accepts %p", (email) => {
    expect(isValidEmail(email)).toBe(true);
  });

  it.each(["", "amara", "amara@", "@example.com", "a b@example.com", "amara@example"])(
    "rejects %p",
    (email) => {
      expect(isValidEmail(email)).toBe(false);
    },
  );
});
