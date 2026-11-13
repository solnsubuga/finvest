import {
  createStubAuthClient,
  DEMO_CODE,
  DEMO_PASSWORD,
  DEMO_PHONE,
  DEMO_PHONE_AUTHENTICATOR,
  RECENT_SIGN_IN_MS,
  SETUP_LIFETIME_MS,
} from "../stubAuthClient";
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

  it("does not reveal that a number has an account until the code proves ownership", async () => {
    const auth = client();
    const taken = await auth.startSignup({ ...signupInput, phone: DEMO_PHONE });
    const fresh = await auth.startSignup(signupInput);
    expect(Object.keys(taken).sort()).toEqual(Object.keys(fresh).sort());

    // A wrong code gets the same answer for both numbers.
    expect(await codeOf(auth.confirmSignupCode(taken.challengeId, "000000"))).toBe("invalid_code");
    expect(await codeOf(auth.confirmSignupCode(fresh.challengeId, "000000"))).toBe("invalid_code");

    // Only the right code, which only the owner has, says the account exists.
    expect(await codeOf(auth.confirmSignupCode(taken.challengeId, DEMO_CODE))).toBe("phone_taken");
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
    expect(challenge.methods).toEqual(["passkey", "authenticator", "code"]);
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

  it("offers only the methods the client has set up", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE_AUTHENTICATOR, DEMO_PASSWORD);
    expect(challenge.methods).toEqual(["authenticator", "code"]);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
  });

  it("opens a full session with an authenticator app code", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    expect(await codeOf(auth.verifyWithAuthenticator(challenge.loginId, "000000"))).toBe(
      "invalid_code",
    );
    const session = await auth.verifyWithAuthenticator(challenge.loginId, DEMO_CODE);
    expect(session).toEqual({ phone: DEMO_PHONE, displayName: "Amara Nakato", restricted: false });
  });

  it("lets the client switch method within one log in", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    // Starts with the authenticator, gets it wrong, then uses an SMS code.
    await codeOf(auth.verifyWithAuthenticator(challenge.loginId, "000000"));
    await auth.sendLoginCode(challenge.loginId);
    const session = await auth.verifyWithCode(challenge.loginId, DEMO_CODE);
    expect(session.restricted).toBe(true);
  });

  it("uses a login only once", async () => {
    const auth = client();
    const challenge = await auth.startLogin(DEMO_PHONE, DEMO_PASSWORD);
    await auth.verifyWithPasskey(challenge.loginId);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
    expect(await codeOf(auth.verifyWithCode(challenge.loginId, DEMO_CODE))).toBe("expired");
    expect(await codeOf(auth.verifyWithAuthenticator(challenge.loginId, DEMO_CODE))).toBe(
      "expired",
    );
  });

  it("offers only a code to an account with no passkey", async () => {
    const auth = client();
    const signup = await auth.startSignup(signupInput);
    const ticket = await auth.confirmSignupCode(signup.challengeId, DEMO_CODE);
    await auth.completeSignup(ticket.ticket, "green matooke sunday");

    const challenge = await auth.startLogin(NEW_PHONE, "green matooke sunday");
    expect(challenge.methods).toEqual(["code"]);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
    expect(await codeOf(auth.verifyWithAuthenticator(challenge.loginId, DEMO_CODE))).toBe(
      "expired",
    );
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

describe("setting up a second step", () => {
  async function signUp(auth: ReturnType<typeof client>) {
    const challenge = await auth.startSignup(signupInput);
    const ticket = await auth.confirmSignupCode(challenge.challengeId, DEMO_CODE);
    return auth.completeSignup(ticket.ticket, "green matooke sunday");
  }

  it("is refused for someone who is not signed in", async () => {
    const auth = client();
    expect(await codeOf(auth.listSecondSteps())).toBe("expired");
    expect(await codeOf(auth.addPasskey())).toBe("expired");
    expect(await codeOf(auth.startAuthenticatorSetup())).toBe("expired");
  });

  it("starts a new client with SMS only", async () => {
    const auth = client();
    await signUp(auth);
    expect(await auth.listSecondSteps()).toEqual(["code"]);
  });

  it("offers a passkey at the next log in once one is added", async () => {
    const auth = client();
    await signUp(auth);
    await auth.addPasskey();
    expect(await auth.listSecondSteps()).toEqual(["passkey", "code"]);
    await auth.signOut();
    const login = await auth.startLogin(NEW_PHONE, "green matooke sunday");
    expect(login.methods).toEqual(["passkey", "code"]);
    expect((await auth.verifyWithPasskey(login.loginId)).restricted).toBe(false);
  });

  it("hands out a key and an otpauth address that carry the same key", async () => {
    const auth = client();
    await signUp(auth);
    const setup = await auth.startAuthenticatorSetup();
    expect(setup.key).toMatch(/^[A-Z2-7]{32}$/);
    const uri = new URL(setup.uri);
    expect(uri.protocol).toBe("otpauth:");
    expect(uri.searchParams.get("secret")).toBe(setup.key);
    expect(uri.searchParams.get("issuer")).toBe("Finvest");
    // A new key every time: nothing fixed sits in the source.
    expect((await auth.startAuthenticatorSetup()).key).not.toBe(setup.key);
  });

  it("does not switch the authenticator on until a right code confirms it", async () => {
    const auth = client();
    await signUp(auth);
    await auth.startAuthenticatorSetup();
    expect(await auth.listSecondSteps()).toEqual(["code"]);
    expect(await codeOf(auth.confirmAuthenticatorSetup("000000"))).toBe(
      "invalid_code",
    );
    expect(await auth.listSecondSteps()).toEqual(["code"]);
    await auth.confirmAuthenticatorSetup(DEMO_CODE);
    expect(await auth.listSecondSteps()).toEqual(["authenticator", "code"]);
    // A set-up is used once.
    expect(await codeOf(auth.confirmAuthenticatorSetup(DEMO_CODE))).toBe("expired");
  });

  it("forgets a half-finished set-up at sign out, so the next person cannot finish it", async () => {
    const auth = client();
    await signUp(auth);
    await auth.startAuthenticatorSetup();
    await auth.signOut();
    const login = await auth.startLogin(DEMO_PHONE_AUTHENTICATOR, DEMO_PASSWORD);
    await auth.verifyWithCode(login.loginId, DEMO_CODE);
    expect(await codeOf(auth.confirmAuthenticatorSetup(DEMO_CODE))).toBe("expired");
  });

  it("asks for a fresh log in before adding anything to a session older than 10 minutes", async () => {
    let time = 0;
    const auth = createStubAuthClient({ delayMs: 0, now: () => time });
    await signUp(auth);
    await auth.startAuthenticatorSetup();
    time = RECENT_SIGN_IN_MS + 1;
    expect(await codeOf(auth.addPasskey())).toBe("sign_in_again");
    expect(await codeOf(auth.startAuthenticatorSetup())).toBe("sign_in_again");
    expect(await codeOf(auth.confirmAuthenticatorSetup(DEMO_CODE))).toBe("sign_in_again");
    // Looking at what is set up is still allowed, and nothing was added.
    expect(await auth.listSecondSteps()).toEqual(["code"]);
  });

  it("ends a started set-up after five minutes, and a new one works", async () => {
    let time = 0;
    const auth = createStubAuthClient({ delayMs: 0, now: () => time });
    await signUp(auth);
    await auth.startAuthenticatorSetup();
    time = SETUP_LIFETIME_MS + 1;
    expect(await codeOf(auth.confirmAuthenticatorSetup(DEMO_CODE))).toBe("expired");
    expect(await auth.listSecondSteps()).toEqual(["code"]);
    await auth.startAuthenticatorSetup();
    await auth.confirmAuthenticatorSetup(DEMO_CODE);
    expect(await auth.listSecondSteps()).toEqual(["authenticator", "code"]);
  });

  it("says in plain words that nothing was added when the device prompt is dismissed", () => {
    expect(describeAuthError(new AuthError("cancelled"))).toBe(
      "Nothing was added. You can try again when you are ready.",
    );
  });
});
