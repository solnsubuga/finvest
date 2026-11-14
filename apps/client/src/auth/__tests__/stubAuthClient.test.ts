import {
  createStubAuthClient,
  DEMO_CODE,
  DEMO_PASSWORD,
  DEMO_PHONE,
  DEMO_PHONE_AUTHENTICATOR,
  RECENT_SIGN_IN_MS,
  SETUP_LIFETIME_MS,
} from "../stubAuthClient";
import { DEMO_CLAIM_SELFIE } from "@/claim/demo";

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
    expect(session).toEqual({ phone: NEW_PHONE, displayName: null, restricted: false, email: "new@example.com", emailVerified: false, pinSet: false, locked: false });
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
    const challenge = await client().startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    expect(challenge).not.toHaveProperty("session");
    expect(challenge.methods).toEqual(["passkey", "authenticator", "code"]);
  });

  it("gives the same answer for a wrong password and an unknown number", async () => {
    const auth = client();
    expect(await codeOf(auth.startLogin({ phone: DEMO_PHONE }, "wrong password"))).toBe("invalid_credentials");
    expect(await codeOf(auth.startLogin({ phone: NEW_PHONE }, DEMO_PASSWORD))).toBe("invalid_credentials");
  });

  it("logs in by email only with a proved address", async () => {
    const auth = client();
    // Joseph proved his address; Amara has not proved hers yet.
    const challenge = await auth.startLogin({ email: "Joseph@Example.com" }, DEMO_PASSWORD);
    expect(challenge).toMatchObject({ phone: null, email: "joseph@example.com" });
    expect(await codeOf(auth.startLogin({ email: "amara@example.com" }, DEMO_PASSWORD))).toBe(
      "invalid_email_credentials",
    );
  });

  it("opens a full session with a passkey", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    const session = await auth.verifyWithPasskey(challenge.loginId);
    expect(session).toEqual({ phone: DEMO_PHONE, displayName: "Amara Nakato", restricted: false, email: "amara@example.com", emailVerified: false, pinSet: false, locked: false });
  });

  it("opens a restricted session with a code", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    await auth.sendLoginCode(challenge.loginId, "sms");
    expect(await codeOf(auth.verifyWithCode(challenge.loginId, "000000"))).toBe("invalid_code");
    const session = await auth.verifyWithCode(challenge.loginId, DEMO_CODE);
    expect(session.restricted).toBe(true);
  });

  it("offers only the methods the client has set up", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE_AUTHENTICATOR }, DEMO_PASSWORD);
    expect(challenge.methods).toEqual(["authenticator", "code"]);
    expect(await codeOf(auth.verifyWithPasskey(challenge.loginId))).toBe("expired");
  });

  it("opens a full session with an authenticator app code", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    expect(await codeOf(auth.verifyWithAuthenticator(challenge.loginId, "000000"))).toBe(
      "invalid_code",
    );
    const session = await auth.verifyWithAuthenticator(challenge.loginId, DEMO_CODE);
    expect(session).toEqual({ phone: DEMO_PHONE, displayName: "Amara Nakato", restricted: false, email: "amara@example.com", emailVerified: false, pinSet: false, locked: false });
  });

  it("lets the client switch method within one log in", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    // Starts with the authenticator, gets it wrong, then uses an SMS code.
    await codeOf(auth.verifyWithAuthenticator(challenge.loginId, "000000"));
    await auth.sendLoginCode(challenge.loginId, "sms");
    const session = await auth.verifyWithCode(challenge.loginId, DEMO_CODE);
    expect(session.restricted).toBe(true);
  });

  it("uses a login only once", async () => {
    const auth = client();
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
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

    const challenge = await auth.startLogin({ phone: NEW_PHONE }, "green matooke sunday");
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
    const login = await auth.startLogin({ phone: NEW_PHONE }, "green matooke sunday");
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
    const login = await auth.startLogin({ phone: DEMO_PHONE_AUTHENTICATOR }, DEMO_PASSWORD);
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

describe("claiming an account moved from Finvest 1.0", () => {
  it("opens the account with the code and a new password, once", async () => {
    const auth = client();
    const { challengeId } = await auth.startClaim(DEMO_CLAIM_SELFIE.token);
    expect(await codeOf(auth.completeClaim(DEMO_CLAIM_SELFIE.token, challengeId, "000000", DEMO_PASSWORD))).toBe(
      "invalid_code",
    );
    const session = await auth.completeClaim(DEMO_CLAIM_SELFIE.token, challengeId, DEMO_CODE, DEMO_PASSWORD);
    expect(session).toMatchObject({ phone: DEMO_CLAIM_SELFIE.phone, restricted: false });
    // The link works once, and the new password logs in.
    expect(await codeOf(auth.startClaim(DEMO_CLAIM_SELFIE.token))).toBe("not_found");
    await auth.signOut();
    const login = await auth.startLogin({ phone: DEMO_CLAIM_SELFIE.phone }, DEMO_PASSWORD);
    expect(login.methods).toEqual(["code"]);
  });

  it("refuses a link it never sent, and an older code once a new one is sent", async () => {
    const auth = client();
    expect(await codeOf(auth.startClaim("not-a-link-we-sent"))).toBe("not_found");
    const first = await auth.startClaim(DEMO_CLAIM_SELFIE.token);
    await auth.startClaim(DEMO_CLAIM_SELFIE.token);
    expect(await codeOf(auth.completeClaim(DEMO_CLAIM_SELFIE.token, first.challengeId, DEMO_CODE, DEMO_PASSWORD))).toBe(
      "expired",
    );
  });
});

describe("changing how the client signs in", () => {
  async function signedIn(step: "passkey" | "code", now: () => number = Date.now) {
    const auth = createStubAuthClient({ delayMs: 0, now });
    const challenge = await auth.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
    if (step === "passkey") await auth.verifyWithPasskey(challenge.loginId);
    else await auth.verifyWithCode(challenge.loginId, DEMO_CODE);
    return auth;
  }

  it("asks for a passkey or app after a log in by SMS code, then lets the change through", async () => {
    const auth = await signedIn("code");
    expect(await codeOf(auth.changeEmail("nakato@example.com"))).toBe("confirm_it_is_you");
    await auth.confirmWithAuthenticator(DEMO_CODE);
    expect(await auth.changeEmail("Nakato@Example.com")).toBe("nakato@example.com");
    expect(await codeOf(auth.changeEmail("nakato@example.com"))).toBe("same_as_now");
  });

  it("needs the current password, and a strong one, to change it", async () => {
    const auth = await signedIn("passkey");
    expect(await codeOf(auth.changePassword("wrong one", "lantern meadow quartz"))).toBe("invalid_credentials");
    expect(await codeOf(auth.changePassword(DEMO_PASSWORD, "password123"))).toBe("leaked_password");
    await auth.changePassword(DEMO_PASSWORD, "lantern meadow quartz");
  });

  it("moves to a new number only with the code sent to it", async () => {
    let clock = 0;
    const auth = await signedIn("passkey", () => clock);
    const id = await auth.startPhoneChange("701 555 013");
    expect(await codeOf(auth.confirmPhoneChange(id, "000000"))).toBe("invalid_code");
    expect(await auth.confirmPhoneChange(id, DEMO_CODE)).toBe(NEW_PHONE.replace("012", "013"));
    clock += RECENT_SIGN_IN_MS + 1;
    expect(await codeOf(auth.startPhoneChange("701 555 014"))).toBe("confirm_it_is_you");
  });
});
