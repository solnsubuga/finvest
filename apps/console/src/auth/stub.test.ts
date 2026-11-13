import { describe, expect, it } from "vitest";
import { AuthError } from "./api.ts";
import { createStubAuthApi, STUB_WRONG_CODE } from "./stub.ts";

describe("stub sign-in", () => {
  it("needs both steps before there is a session", async () => {
    const api = createStubAuthApi();
    const challenge = await api.startSignIn("finance@finvest.test", "anything");
    expect(await api.getSession()).toBeNull();

    const session = await api.finishSignIn(challenge, { method: "authenticator", code: "123456" });
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
    await api.finishSignIn(challenge, { method: "passkey" });
    await expect(api.finishSignIn(challenge, { method: "passkey" })).rejects.toEqual(new AuthError("passkey_failed"));
  });

  it("offers only the second steps the person has set up", async () => {
    const api = createStubAuthApi();
    expect((await api.startSignIn("admin@finvest.test", "anything")).methods).toEqual([
      "passkey",
      "authenticator",
    ]);
    expect((await api.startSignIn("finance@finvest.test", "anything")).methods).toEqual(["authenticator"]);
    expect((await api.startSignIn("compliance@finvest.test", "anything")).methods).toEqual(["passkey"]);
  });

  it("refuses an account with no second step, with no challenge and no session", async () => {
    const api = createStubAuthApi();
    await expect(api.startSignIn("invited@finvest.test", "anything")).rejects.toEqual(
      new AuthError("second_step_missing"),
    );
    expect(await api.getSession()).toBeNull();
  });

  it("refuses a second step the person has not set up", async () => {
    const api = createStubAuthApi();
    const challenge = await api.startSignIn("compliance@finvest.test", "anything");
    await expect(api.finishSignIn(challenge, { method: "authenticator", code: "123456" })).rejects.toEqual(
      new AuthError("invalid_code"),
    );
    expect(await api.getSession()).toBeNull();
  });

  it("refuses a wrong or malformed code, then accepts a right one on the same challenge", async () => {
    const api = createStubAuthApi();
    const challenge = await api.startSignIn("finance@finvest.test", "anything");
    for (const code of [STUB_WRONG_CODE, "12345", "abcdef", ""]) {
      await expect(api.finishSignIn(challenge, { method: "authenticator", code })).rejects.toEqual(
        new AuthError("invalid_code"),
      );
    }
    expect(await api.getSession()).toBeNull();
    expect((await api.finishSignIn(challenge, { method: "authenticator", code: "123456" })).role).toBe("finance");
  });

  it("forgets the session on sign out", async () => {
    const api = createStubAuthApi();
    await api.finishSignIn(await api.startSignIn("admin@finvest.test", "anything"), { method: "passkey" });
    await api.signOut();
    expect(await api.getSession()).toBeNull();
  });
});
