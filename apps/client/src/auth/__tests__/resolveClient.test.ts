import { resolveAuthClient } from "../resolveClient";
import { DEMO_PASSWORD, DEMO_PHONE } from "../stubAuthClient";
import { AuthError } from "../types";

describe("resolveAuthClient", () => {
  it("uses the stub in development", () => {
    expect(resolveAuthClient({ development: true, demoFlag: undefined }).demo).toBe(true);
  });

  it("uses the stub in a production build only when the flag is exactly 1", () => {
    expect(resolveAuthClient({ development: false, demoFlag: "1" }).demo).toBe(true);
    for (const demoFlag of [undefined, "", "0", "true", "yes"]) {
      expect(resolveAuthClient({ development: false, demoFlag }).demo).toBe(false);
    }
  });

  it("fails closed in production: the demo account cannot sign in", async () => {
    const { client } = resolveAuthClient({ development: false, demoFlag: undefined });
    await expect(client.startLogin(DEMO_PHONE, DEMO_PASSWORD)).rejects.toEqual(
      new AuthError("unavailable"),
    );
    await expect(
      client.startSignup({ phone: "+256701555012", email: "new@example.com" }),
    ).rejects.toBeInstanceOf(AuthError);
    await expect(client.verifyWithCode("login_1", "123456")).rejects.toBeInstanceOf(AuthError);
    await expect(client.verifyWithPasskey("login_1")).rejects.toBeInstanceOf(AuthError);
  });
});
