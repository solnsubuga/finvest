import {
  AuthClient,
  AuthError,
  AuthenticatorSetup,
  LoginChallenge,
  SecondStep,
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
 *   - any SMS or authenticator code is accepted when it equals DEMO_CODE
 *   - DEMO_PHONE with DEMO_PASSWORD has a passkey, an authenticator app and SMS
 *   - DEMO_PHONE_AUTHENTICATOR with DEMO_PASSWORD has an authenticator app and
 *     SMS, but no passkey
 *   - an account created by signing up has SMS only, until a passkey or an
 *     authenticator app is added from Sign in and security
 *   - the authenticator key shown during set-up is random letters made on the
 *     spot. It is not kept and no real app code is checked against it.
 */
export const DEMO_CODE = "123456";
export const DEMO_PHONE = "+256772123456";
export const DEMO_PHONE_AUTHENTICATOR = "+256701000200";
export const DEMO_PASSWORD = "correcthorse1";

/** Stands in for the server's leaked password list. */
const LEAKED = new Set(["password123", "1234567890", "qwertyuiop"]);

type Account = {
  password: string;
  displayName: string | null;
  hasPasskey: boolean;
  hasAuthenticator: boolean;
};

/** Every account can receive an SMS code, so "code" is always last. */
function methodsFor(account: Account): SecondStep[] {
  const methods: SecondStep[] = [];
  if (account.hasPasskey) {
    methods.push("passkey");
  }
  if (account.hasAuthenticator) {
    methods.push("authenticator");
  }
  methods.push("code");
  return methods;
}

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/** Looks like an authenticator key. Random, never stored, never checked. */
function demoKey(): string {
  let key = "";
  for (let index = 0; index < 32; index += 1) {
    key += BASE32.charAt(Math.floor(Math.random() * BASE32.length));
  }
  return key;
}

/** Adding a second step needs a session this fresh, as the sign-in API does. */
export const RECENT_SIGN_IN_MS = 10 * 60 * 1000;

/** How long a started authenticator set-up can wait for its code. */
export const SETUP_LIFETIME_MS = 5 * 60 * 1000;

export type StubOptions = {
  /** The clock, so tests can let a session grow old. */
  now?: () => number;
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
};

export function createStubAuthClient(options: StubOptions = {}): AuthClient {
  const delayMs = options.delayMs ?? 450;
  const now = options.now ?? Date.now;
  const accounts = new Map<string, Account>([
    [
      DEMO_PHONE,
      {
        password: DEMO_PASSWORD,
        displayName: "Amara Nakato",
        hasPasskey: true,
        hasAuthenticator: true,
      },
    ],
    [
      DEMO_PHONE_AUTHENTICATOR,
      {
        password: DEMO_PASSWORD,
        displayName: "Joseph Okello",
        hasPasskey: false,
        hasAuthenticator: true,
      },
    ],
  ]);
  const signups = new Map<string, SignupStart>();
  const tickets = new Map<string, SignupStart>();
  const logins = new Map<string, string>();
  /** A set-up that was started and not yet confirmed, for this session only. */
  let pendingAuthenticatorAt: number | null = null;
  let signedInAt = 0;
  /** The number of whoever is signed in, as the server would know from the session. */
  let signedIn: string | null = null;
  let counter = 0;

  const nextId = (prefix: string) => `${prefix}_${(counter += 1)}`;
  const wait = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const sessionFor = (phone: string, restricted: boolean): Session => {
    signedIn = phone;
    signedInAt = now();
    pendingAuthenticatorAt = null;
    return { phone, displayName: accounts.get(phone)?.displayName ?? null, restricted };
  };

  const currentAccount = (): Account => {
    const account = signedIn ? accounts.get(signedIn) : undefined;
    if (!account) {
      throw new AuthError("expired");
    }
    return account;
  };

  const recentAccount = (): Account => {
    const account = currentAccount();
    if (now() - signedInAt > RECENT_SIGN_IN_MS) {
      throw new AuthError("sign_in_again");
    }
    return account;
  };

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
      accounts.set(signup.phone, {
        password,
        displayName: null,
        hasPasskey: false,
        hasAuthenticator: false,
      });
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
      return { loginId, phone, methods: methodsFor(account) };
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

    async verifyWithAuthenticator(loginId, code): Promise<Session> {
      await wait();
      const phone = logins.get(loginId);
      if (!phone || !accounts.get(phone)?.hasAuthenticator) {
        throw new AuthError("expired");
      }
      if (code !== DEMO_CODE) {
        throw new AuthError("invalid_code");
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

    async listSecondSteps(): Promise<SecondStep[]> {
      await wait();
      return methodsFor(currentAccount());
    },

    async addPasskey(): Promise<void> {
      await wait();
      recentAccount().hasPasskey = true;
    },

    async startAuthenticatorSetup(): Promise<AuthenticatorSetup> {
      await wait();
      recentAccount();
      pendingAuthenticatorAt = now();
      const key = demoKey();
      const label = encodeURIComponent(`Finvest:${signedIn ?? ""}`);
      return { key, uri: `otpauth://totp/${label}?secret=${key}&issuer=Finvest` };
    },

    async confirmAuthenticatorSetup(code): Promise<void> {
      await wait();
      const account = recentAccount();
      if (pendingAuthenticatorAt === null || now() - pendingAuthenticatorAt > SETUP_LIFETIME_MS) {
        pendingAuthenticatorAt = null;
        throw new AuthError("expired");
      }
      if (code !== DEMO_CODE) {
        throw new AuthError("invalid_code");
      }
      pendingAuthenticatorAt = null;
      account.hasAuthenticator = true;
    },

    async signOut(): Promise<void> {
      await wait();
      signedIn = null;
      pendingAuthenticatorAt = null;
    },
  };
}
