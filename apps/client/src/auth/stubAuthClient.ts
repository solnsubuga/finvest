import {
  AuthClient,
  AuthError,
  LoginChallenge,
  Session,
  SignupChallenge,
  SignupStart,
  SignupTicket,
} from "./types";

/**
 * An in-memory stand-in for the sign-in API, so the screens can be built and
 * tested before the backend exists. Nothing here is real: no network, no
 * storage, and everything is forgotten when the app reloads. `resolveClient.ts`
 * keeps it out of production builds.
 *
 * Demo values, not credentials for any real system:
 *   - any SMS code is accepted when it equals DEMO_CODE
 *   - one account exists already, DEMO_PHONE with DEMO_PASSWORD
 */
export const DEMO_CODE = "123456";
export const DEMO_PHONE = "+256772123456";
export const DEMO_PASSWORD = "correcthorse1";

/** Stands in for the server's leaked password list. */
const LEAKED = new Set(["password123", "1234567890", "qwertyuiop"]);

type Account = { password: string; displayName: string | null; hasPasskey: boolean };

export type StubOptions = {
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
};

export function createStubAuthClient(options: StubOptions = {}): AuthClient {
  const delayMs = options.delayMs ?? 450;
  const accounts = new Map<string, Account>([
    [DEMO_PHONE, { password: DEMO_PASSWORD, displayName: "Amara Nakato", hasPasskey: true }],
  ]);
  const signups = new Map<string, SignupStart>();
  const tickets = new Map<string, SignupStart>();
  const logins = new Map<string, string>();
  let counter = 0;

  const nextId = (prefix: string) => `${prefix}_${(counter += 1)}`;
  const wait = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const sessionFor = (phone: string, restricted: boolean): Session => ({
    phone,
    displayName: accounts.get(phone)?.displayName ?? null,
    restricted,
  });

  return {
    async startSignup(input): Promise<SignupChallenge> {
      await wait();
      // Says nothing about whether the number has an account. That is only
      // revealed after the code proves the caller owns the number.
      const challengeId = nextId("signup");
      signups.set(challengeId, input);
      return { challengeId, phone: input.phone };
    },

    async confirmSignupCode(challengeId, code): Promise<SignupTicket> {
      await wait();
      const signup = signups.get(challengeId);
      if (!signup) {
        throw new AuthError("expired");
      }
      if (code !== DEMO_CODE) {
        throw new AuthError("invalid_code");
      }
      signups.delete(challengeId);
      if (accounts.has(signup.phone)) {
        throw new AuthError("phone_taken");
      }
      const ticket = nextId("ticket");
      tickets.set(ticket, signup);
      return { ticket, phone: signup.phone };
    },

    async completeSignup(ticket, password): Promise<Session> {
      await wait();
      const signup = tickets.get(ticket);
      if (!signup) {
        throw new AuthError("expired");
      }
      if (LEAKED.has(password.toLowerCase())) {
        throw new AuthError("leaked_password");
      }
      tickets.delete(ticket);
      accounts.set(signup.phone, { password, displayName: null, hasPasskey: false });
      return sessionFor(signup.phone, false);
    },

    async startLogin(phone, password): Promise<LoginChallenge> {
      await wait();
      const account = accounts.get(phone);
      if (!account || account.password !== password) {
        throw new AuthError("invalid_credentials");
      }
      const loginId = nextId("login");
      logins.set(loginId, phone);
      return {
        loginId,
        phone,
        methods: account.hasPasskey ? ["passkey", "code"] : ["code"],
      };
    },

    async verifyWithPasskey(loginId): Promise<Session> {
      await wait();
      const phone = logins.get(loginId);
      if (!phone || !accounts.get(phone)?.hasPasskey) {
        throw new AuthError("expired");
      }
      logins.delete(loginId);
      return sessionFor(phone, false);
    },

    async sendLoginCode(loginId): Promise<void> {
      await wait();
      if (!logins.has(loginId)) {
        throw new AuthError("expired");
      }
    },

    async verifyWithCode(loginId, code): Promise<Session> {
      await wait();
      const phone = logins.get(loginId);
      if (!phone) {
        throw new AuthError("expired");
      }
      if (code !== DEMO_CODE) {
        throw new AuthError("invalid_code");
      }
      logins.delete(loginId);
      return sessionFor(phone, true);
    },

    async signOut(): Promise<void> {
      await wait();
    },
  };
}
