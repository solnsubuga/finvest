/**
 * The contract between the screens and whatever signs a client in.
 *
 * Today the only implementation is the in-memory stub in `stubAuthClient.ts`.
 * When the backend sign-in API is merged, a second implementation backed by
 * the generated `packages/api-client` types replaces it, and the screens do
 * not change.
 */

export type Session = {
  phone: string;
  /** Known only after the identity check, so it is absent for new sign-ups. */
  displayName: string | null;
  /**
   * True when the second step was a code, not a passkey. The server then
   * limits withdrawals to known accounts for 24 hours (docs/rules.md).
   */
  restricted: boolean;
};

export type AuthErrorCode =
  | "invalid_credentials"
  | "invalid_code"
  | "phone_taken"
  | "leaked_password"
  | "expired"
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

export type SecondStep = "passkey" | "code";
export type LoginChallenge = {
  loginId: string;
  phone: string;
  /** In the order they should be offered. A passkey always comes first. */
  methods: SecondStep[];
};

export interface AuthClient {
  /** Sign up, step 1: sends a code by SMS to prove the number. */
  startSignup(input: SignupStart): Promise<SignupChallenge>;
  confirmSignupCode(challengeId: string, code: string): Promise<SignupTicket>;
  /** Sign up, step 2: sets the password and opens the first session. */
  completeSignup(ticket: string, password: string): Promise<Session>;

  /** Log in, step 1: number and password. Never opens a session on its own. */
  startLogin(phone: string, password: string): Promise<LoginChallenge>;
  /** Log in, step 2, preferred. */
  verifyWithPasskey(loginId: string): Promise<Session>;
  /** Log in, step 2, backup. Opens a restricted session. */
  sendLoginCode(loginId: string): Promise<void>;
  verifyWithCode(loginId: string, code: string): Promise<Session>;

  signOut(): Promise<void>;
}

const messages: Record<AuthErrorCode, string> = {
  invalid_credentials: "That number and password do not match. Check both and try again.",
  invalid_code: "That code is not right. Check the SMS and try again.",
  phone_taken: "This number already has an account. Log in instead.",
  leaked_password:
    "This password has appeared in a known leak, so it is not safe to use. Choose a different one.",
  expired: "That took too long, so we stopped for your safety. Please start again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

/** A sentence a client can act on. Never shows codes or technical detail. */
export function describeAuthError(error: unknown): string {
  if (error instanceof AuthError) {
    return messages[error.code];
  }
  return messages.unavailable;
}
