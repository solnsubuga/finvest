/**
 * The contract between the screens and whatever signs a client in.
 *
 * `apiAuthClient.ts` answers it from the real API. The in-memory stub in
 * `stubAuthClient.ts` answers it in tests and in demo builds.
 */

export type Session = {
  phone: string;
  /** Known only after the identity check, so it is absent for new sign-ups. */
  displayName: string | null;
  /**
   * True when the second step was a code sent by SMS or email. The server
   * then limits withdrawals to known accounts for 24 hours (docs/rules.md).
   * A passkey or an authenticator app opens a full session.
   */
  restricted: boolean;
};

export type AuthErrorCode =
  | "invalid_credentials"
  | "invalid_code"
  | "phone_taken"
  | "leaked_password"
  | "expired"
  | "cancelled"
  | "sign_in_again"
  | "passkey_failed"
  | "passkey_unsupported"
  | "rate_limited"
  | "not_accepted"
  | "offline"
  | "unavailable";

export class AuthError extends Error {
  readonly code: AuthErrorCode;

  constructor(code: AuthErrorCode) {
    super(code);
    this.name = "AuthError";
    this.code = code;
  }
}

export type SignupStart = { phone: string; email: string; inviteCode?: string };
export type SignupChallenge = { challengeId: string; phone: string };
export type SignupTicket = { ticket: string; phone: string };
/** A password reset in progress: the texted code is checked only when the new password is set. */
export type PasswordReset = { challengeId: string; phone: string };

/**
 * The ways to confirm a log in. A client can use any one they have set up:
 * a passkey, an authenticator app, or a code sent by SMS.
 */
export type SecondStep = "passkey" | "authenticator" | "code";
export type LoginChallenge = {
  loginId: string;
  phone: string;
  /**
   * Only the methods this client has set up, strongest first. The first is
   * offered up front and the client can switch to any of the others.
   */
  methods: SecondStep[];
};

/**
 * What a client scans or types into an authenticator app. The server makes
 * the key. The app shows it once, during set-up, and never stores it.
 */
export type AuthenticatorSetup = {
  /** The key in base32, for typing by hand when the QR code cannot be scanned. */
  key: string;
  /** The same key as an otpauth:// address, for the QR code and for opening an app. */
  uri: string;
};

export interface AuthClient {
  /**
   * The session this device already holds, if it is still open: the API's
   * cookie on the web, the stored token on a phone. Null when there is none.
   */
  restoreSession(): Promise<Session | null>;

  /**
   * Sign up, step 1: sends a code by SMS to prove the number. It answers the
   * same way whether or not the number already has an account, so it cannot
   * be used to find out who is a client.
   */
  startSignup(input: SignupStart): Promise<SignupChallenge>;
  /**
   * Fails with `phone_taken` when the code is right and the number already
   * has an account. Only the owner of the number can learn that.
   */
  confirmSignupCode(challengeId: string, code: string): Promise<SignupTicket>;
  /** Sign up, step 2: sets the password and opens the first session. */
  completeSignup(ticket: string, password: string): Promise<Session>;

  /** Log in, step 1: number and password. Never opens a session on its own. */
  startLogin(phone: string, password: string): Promise<LoginChallenge>;
  /** Log in, step 2, with a passkey. */
  verifyWithPasskey(loginId: string): Promise<Session>;
  /** Log in, step 2, with the 6 digit code from an authenticator app. */
  verifyWithAuthenticator(loginId: string, code: string): Promise<Session>;
  /** Log in, step 2, with a code sent by SMS. Opens a restricted session. */
  sendLoginCode(loginId: string): Promise<void>;
  verifyWithCode(loginId: string, code: string): Promise<Session>;

  /**
   * The second steps the signed-in client has set up, strongest first, in the
   * same shape as `LoginChallenge.methods`. SMS is always there.
   */
  listSecondSteps(): Promise<SecondStep[]>;
  /**
   * Adding a second step needs a session opened in the last few minutes. An
   * older one fails with `sign_in_again`, and the client logs in again first.
   *
   * Creates a passkey on this device and saves its public half to the
   * account. Fails with `cancelled` when the client dismisses the device
   * prompt, and nothing is saved.
   */
  addPasskey(): Promise<void>;
  /**
   * Authenticator app set-up, step 1: a fresh key. Nothing is switched on yet.
   * The server ties the pending set-up to this session, so there is no id to
   * carry, and it ends when the session does.
   */
  startAuthenticatorSetup(): Promise<AuthenticatorSetup>;
  /**
   * Authenticator app set-up, step 2: a code from the app proves the key was
   * saved. Only then does the app become a way to log in.
   */
  confirmAuthenticatorSetup(code: string): Promise<void>;

  /**
   * Forgot password, step 1: texts a code if the number has an account. It
   * answers the same way whether or not it does, so it cannot be used to find
   * out who is a client.
   */
  startPasswordReset(phone: string): Promise<PasswordReset>;
  /**
   * Forgot password, step 2: the texted code and the new password together.
   * Every device is signed out, and for 24 hours withdrawals go only to
   * accounts used before. It opens no session: the client logs in next.
   */
  completePasswordReset(challengeId: string, code: string, password: string): Promise<void>;

  signOut(): Promise<void>;
}

const messages: Record<AuthErrorCode, string> = {
  invalid_credentials: "That number and password do not match. Check both and try again.",
  invalid_code: "That code is not right. Check it and try again.",
  phone_taken: "This number already has a Finvest account. Log in instead.",
  leaked_password:
    "This password has appeared in a known leak, so it is not safe to use. Choose a different one.",
  sign_in_again:
    "For your safety, log in again before you change how you sign in. We will bring you straight back here.",
  cancelled: "Nothing was added. You can try again when you are ready.",
  expired: "That took too long, so we stopped for your safety. Please start again.",
  passkey_failed: "We could not confirm your passkey. Try again, or use another way to log in.",
  passkey_unsupported:
    "Passkeys are not available in this app yet. Use an authenticator app or an SMS code for now.",
  rate_limited: "Too many tries. Wait a few minutes and try again.",
  not_accepted: "Something in that was not accepted. Check what you entered and try again.",
  offline: "You seem to be offline. Check your connection and try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

/** A sentence a client can act on. Never shows codes or technical detail. */
export function describeAuthError(error: unknown): string {
  if (error instanceof AuthError) {
    return messages[error.code];
  }
  return messages.unavailable;
}
