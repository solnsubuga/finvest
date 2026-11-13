import { resolveAuthClient } from "../resolveClient";
import * as stub from "../stubAuthClient";
import { AuthError } from "../types";

describe("resolveAuthClient", () => {
  it("uses the demo when the stub was loaded, and says so", () => {
    const resolved = resolveAuthClient(stub);
    expect(resolved.demo).toBe(true);
    expect(resolved.demoCode).toBe(stub.DEMO_CODE);
  });

  it("loads the stub in development, which is how the tests run", () => {
    expect(resolveAuthClient().demo).toBe(true);
  });

  it("fails closed without the stub: nobody can sign in, not even the demo account", async () => {
    const { client, demo, demoCode } = resolveAuthClient(null);
    expect(demo).toBe(false);
    expect(demoCode).toBeNull();
    await expect(client.startLogin(stub.DEMO_PHONE, stub.DEMO_PASSWORD)).rejects.toEqual(
      new AuthError("unavailable"),
    );
    await expect(
      client.startSignup({ phone: "+256701555012", email: "new@example.com" }),
    ).rejects.toBeInstanceOf(AuthError);
    await expect(client.verifyWithCode("login_1", stub.DEMO_CODE)).rejects.toBeInstanceOf(
      AuthError,
    );
    await expect(client.verifyWithPasskey("login_1")).rejects.toBeInstanceOf(AuthError);
  });
});
