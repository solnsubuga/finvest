import { createStubAuthClient, DEMO_CODE, DEMO_PASSWORD, DEMO_PHONE } from "../stubAuthClient";
import { AuthError, describeAuthError } from "../types";

const NEW_PHONE = "+256701555012";
const signupInput = { phone: NEW_PHONE, email: "new@example.com" };

const client = () => createStubAuthClient({ delayMs: 0 });

async function codeOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof AuthError) {
      return error.code;
    }
    throw error;
  }
  throw new Error("expected the call to fail");
}

describe("sign up", () => {
  it("opens a full session after the code and a password", async () => {
    const auth = client();
    const challenge = await auth.startSignup(signupInput);
    const ticket = await auth.confirmSignupCode(challenge.challengeId, DEMO_CODE);
    const session = await auth.completeSignup(ticket.ticket, "green matooke sunday");
    expect(session).toEqual({ phone: NEW_PHONE, displayName: null, restricted: false });
  });

  it("refuses a number that already has an account", async () => {
    expect(await codeOf(client().startSignup({ ...signupInput, phone: DEMO_PHONE }))).toBe(
      "phone_taken",
    );
  });

  it("refuses a wrong code and lets the client try again", async () => {
    const auth = client();
    const challenge = await auth.startSignup(signupInput);
    expect(await codeOf(auth.confirmSignupCode(challenge.challengeId, "000000"))).toBe(
      "invalid_code",
    );
    await expect(auth.confirmSignupCode(challenge.challengeId, DEMO_CODE)).resolves.toBeTruthy();
  });

  it("uses a code only once", async () => {
    const auth = client();
    const challenge = await auth.startSignup(signupInput);
    await auth.confirmSignupCode(challenge.challengeId, DEMO_CODE);
    expect(await codeOf(auth.confirmSignupCode(challenge.challengeId, DEMO_CODE))).toBe("expired");
  });

  it("refuses a leaked password and keeps the ticket for another try", async () => {
    const auth = client();
    const challenge = await auth.startSignup(signupInput);
    const ticket = await auth.confirmSignupCode(challenge.challengeId, DEMO_CODE);
    expect(await codeOf(auth.completeSignup(ticket.ticket, "Password123"))).toBe("leaked_password");
    await expect(auth.completeSignup(ticket.ticket, "green matooke sunday")).resolves.toBeTruthy();
  });

  it("refuses an unknown ticket", async () => {
    expect(await codeOf(client().completeSignup("nope", "green matooke sunday"))).toBe("expired");
  });
});

describe("log in", () => {
  it("never opens a session from the password alone", async () => {
    const challenge = await client().startLogin(DEMO_PHONE, DEMO_PASSWORD);
    expect(challenge).not.toHaveProperty("session");
    expect(challenge.methods).toEqual(["passkey", "code"]);
  });

  it("gives the same answer for a wrong password and an unknown number", async () => {
    const auth = client();
    expect(await codeOf(auth.startLogin(DEMO_PHONE, "wrong password"))).toBe("invalid_credentials");
    expect(await codeOf(auth.startLogin(NEW_PHONE, DEMO_PASSWORD))).toBe("invalid_credentials");
  });

  it("opens a full session with a passkey", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    const session = await auth.verifyWithPasskey(challenge.loginId);
    expect(session).toEqual({ phone: DEMO_PHONE, displayName: "Amara Nakato", restricted: false });
  });

  it("opens a restricted session with a code", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    await auth.sendLoginCode(challenge.loginId);
    expect(await codeOf(auth.verifyWithCode(challenge.loginId, "000000"))).toBe("invalid_code");
    const session = await auth.verifyWithCode(challenge.loginId, DEMO_CODE);
    expect(session.restricted).toBe(true);
  });

  it("uses a login only once", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    await auth.verifyWithPasskey(challenge.loginId);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
    expect(await codeOf(auth.verifyWithCode(challenge.loginId, DEMO_CODE))).toBe("expired");
  });

  it("offers only a code to an account with no passkey", async () => {
    const auth = client();
    const signup = await auth.startSignup(signupInput);
    const ticket = await auth.confirmSignupCode(signup.challengeId, DEMO_CODE);
    await auth.completeSignup(ticket.ticket, "green matooke sunday");

    const challenge = await auth.startLogin(NEW_PHONE, "green matooke sunday");
    expect(challenge.methods).toEqual(["code"]);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
  });
});

describe("describeAuthError", () => {
  it("turns a code into a sentence and hides anything unexpected", () => {
    expect(describeAuthError(new AuthError("invalid_code"))).toMatch(/code is not right/);
    expect(describeAuthError(new Error("ECONNRESET 10.0.0.1"))).toBe(
      "We could not reach Finvest. Check your connection and try again.",
    );
  });
});
