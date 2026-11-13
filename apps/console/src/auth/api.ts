import type { StaffSession } from "./types.ts";

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
}

export const SECOND_STEP_METHODS = ["passkey", "authenticator"] as const;
export type SecondStepMethod = (typeof SECOND_STEP_METHODS)[number];

export interface SignInChallenge {
  challengeId: string;
  /** The second steps this person has set up. Never empty. */
  methods: readonly SecondStepMethod[];
}

export type SecondStepAnswer = { method: "passkey" } | { method: "authenticator"; code: string };

export type AuthErrorCode =
  | "invalid_credentials"
  | "passkey_failed"
  | "code_invalid"
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

/** Wording for each error. Never says whether the email or the password was wrong. */
export const AUTH_ERROR_MESSAGES: Record<AuthErrorCode, string> = {
  invalid_credentials: "That email and password do not match a staff account.",
  passkey_failed: "We could not confirm your passkey. Try again.",
  code_invalid: "That code is not right. Check your authenticator app and try again.",
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
};
