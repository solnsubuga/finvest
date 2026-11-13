import type { Role, StaffSession } from "./types.ts";

/**
 * The console's view of staff sign-in.
 *
 * This is a stand-in for the generated client in packages/api-client, which
 * does not exist until the backend publishes its OpenAPI description. When it
 * does, an implementation of this interface that calls the real endpoints
 * replaces the stub, and the screens do not change.
 *
 * Staff sign in with a password and then a second step: a passkey or a code
 * from an authenticator app, whichever they have set up. There is no SMS option.
 *
 * The real implementation keeps the session in an HttpOnly cookie set by the
 * backend. It must not put a session or token in web storage, which the stub
 * does only because it has no backend.
 */
export interface AuthApi {
  /** The current session, or null when nobody is signed in. */
  getSession(): Promise<StaffSession | null>;
  /** Step one. Resolves with a challenge that the second step must answer. Never a session. */
  startSignIn(email: string, password: string): Promise<SignInChallenge>;
  /**
   * Step two. For a passkey, asks the browser for it. For an authenticator app,
   * sends the code the person typed. Either is exchanged for a session.
   */
  finishSignIn(challenge: SignInChallenge, answer: SecondStepAnswer): Promise<StaffSession>;
  signOut(): Promise<void>;

  /**
   * First-time set-up from a one-time link. The token comes from the link and
   * is only ever sent in a request body.
   */
  getEnrolment(token: string): Promise<Enrolment>;
  /** Sets the password, creates a passkey in the browser and signs the person in. */
  enrolWithPasskey(token: string, password: string): Promise<StaffSession>;
  /** Returns the secret to add to an authenticator app. Nothing is saved until it is confirmed. */
  startAuthenticatorEnrolment(token: string): Promise<AuthenticatorSetup>;
  /** Sets the password, confirms the first code and signs the person in. */
  enrolWithAuthenticator(token: string, password: string, code: string): Promise<StaffSession>;
}

export interface Enrolment {
  name: string;
  email: string;
  role: Role;
  /** The second steps this person may choose from. */
  methods: readonly SecondStepMethod[];
}

export interface AuthenticatorSetup {
  /** The key to type in by hand when the QR code cannot be scanned. */
  secret: string;
  /** The otpauth address the QR code holds. */
  uri: string;
}

export const SECOND_STEP_METHODS = ["passkey", "authenticator"] as const;
export type SecondStepMethod = (typeof SECOND_STEP_METHODS)[number];

export interface SignInChallenge {
  challengeId: string;
  /**
   * The second steps this person has set up. Every staff account must have at
   * least one. If this is empty the console refuses to go on.
   */
  methods: readonly SecondStepMethod[];
}

export type SecondStepAnswer = { method: "passkey" } | { method: "authenticator"; code: string };

export type AuthErrorCode =
  | "invalid_credentials"
  | "passkey_failed"
  | "invalid_code"
  | "second_step_missing"
  | "link_expired"
  | "password_too_short"
  | "password_too_long"
  | "password_leaked"
  | "rate_limited"
  | "not_connected"
  | "unavailable";

export class AuthError extends Error {
  readonly code: AuthErrorCode;

  constructor(code: AuthErrorCode) {
    super(code);
    this.name = "AuthError";
    this.code = code;
  }
}

/** From docs/rules.md. The backend checks it again and also checks leaked password lists. */
export const PASSWORD_MIN_LENGTH = 10;

/** Wording for each error. Never says whether the email or the password was wrong. */
export const AUTH_ERROR_MESSAGES: Record<AuthErrorCode, string> = {
  invalid_credentials: "That email and password do not match a staff account.",
  passkey_failed: "We could not confirm your passkey. Try again.",
  invalid_code: "That code is not right. Check your authenticator app and try again.",
  second_step_missing:
    "Your account has no passkey or authenticator app yet, so you cannot sign in. Use your set-up link, or ask an admin for a new one.",
  link_expired: "This set-up link has expired or has already been used. Ask an admin for a new one.",
  password_too_short: `Use at least ${PASSWORD_MIN_LENGTH} characters for your password.`,
  password_too_long: "That password is too long. Use a shorter one.",
  password_leaked: "That password has appeared in a known leak. Choose a different one.",
  rate_limited: "Too many attempts. Wait a few minutes and try again.",
  not_connected: "Sign-in is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function authErrorMessage(error: unknown): string {
  const code = error instanceof AuthError ? error.code : "unavailable";
  return AUTH_ERROR_MESSAGES[code];
}

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedAuthApi: AuthApi = {
  getSession: () => Promise.resolve(null),
  startSignIn: () => Promise.reject(new AuthError("not_connected")),
  finishSignIn: () => Promise.reject(new AuthError("not_connected")),
  signOut: () => Promise.resolve(),
  getEnrolment: () => Promise.reject(new AuthError("not_connected")),
  enrolWithPasskey: () => Promise.reject(new AuthError("not_connected")),
  startAuthenticatorEnrolment: () => Promise.reject(new AuthError("not_connected")),
  enrolWithAuthenticator: () => Promise.reject(new AuthError("not_connected")),
};
