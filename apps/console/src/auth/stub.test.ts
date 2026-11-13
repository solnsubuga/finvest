import { describe, expect, it } from "vitest";
import { AuthError } from "./api.ts";
import { createStubAuthApi } from "./stub.ts";

describe("stub sign-in", () => {
  it("needs both steps before there is a session", async () => {
    const api = createStubAuthApi();
    const challenge = await api.startSignIn("finance@finvest.test", "anything");
    expect(await api.getSession()).toBeNull();

    const session = await api.finishSignIn(challenge);
    expect(session.role).toBe("finance");
    expect(await api.getSession()).toEqual(session);
  });

  it("refuses an unknown email and an empty password with the same error", async () => {
    const api = createStubAuthApi();
    await expect(api.startSignIn("nobody@finvest.test", "anything")).rejects.toEqual(
      new AuthError("invalid_credentials"),
    );
    await expect(api.startSignIn("admin@finvest.test", "")).rejects.toEqual(
      new AuthError("invalid_credentials"),
    );
  });

  it("uses a challenge once", async () => {
    const api = createStubAuthApi();
    const challenge = await api.startSignIn("admin@finvest.test", "anything");
    await api.finishSignIn(challenge);
    await expect(api.finishSignIn(challenge)).rejects.toEqual(new AuthError("passkey_failed"));
  });

  it("forgets the session on sign out", async () => {
    const api = createStubAuthApi();
    await api.finishSignIn(await api.startSignIn("admin@finvest.test", "anything"));
    await api.signOut();
    expect(await api.getSession()).toBeNull();
  });
});
