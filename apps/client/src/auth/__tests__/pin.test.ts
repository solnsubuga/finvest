import { pinProblem } from "../pin";

describe("a new PIN", () => {
  it.each(["48151", "10293", "13579", "90817", "11112", "24680"])("accepts %s", (pin) => {
    expect(pinProblem(pin)).toBeNull();
  });

  it.each(["", "1234", "123456", "12a45", "1234 "])("refuses %p as not five digits", (pin) => {
    expect(pinProblem(pin)).toBe("format");
  });

  it.each(["00000", "77777", "12345", "56789", "54321", "98765", "01234"])(
    "refuses %s as too simple",
    (pin) => {
      expect(pinProblem(pin)).toBe("too_simple");
    },
  );
});
