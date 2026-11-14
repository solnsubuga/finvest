import {
  AuthClient,
  AuthError,
  AuthenticatorSetup,
  Device,
  LoginChallenge,
  Passkey,
  SecondStep,
  Session,
  SignupChallenge,
  PasswordReset,
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
/** The demo cannot email, so this link token is always accepted. It proves nothing. */
export const DEMO_EMAIL_TOKEN = "demo-email-link-not-a-real-token-000000";

/** Stands in for the server's leaked password list. */
const LEAKED = new Set(["password123", "1234567890", "qwertyuiop"]);

type Account = {
  password: string;
  displayName: string | null;
  hasPasskey: boolean;
  hasAuthenticator: boolean;
  /** The passkeys behind `hasPasskey`. It is true while any are left. */
  passkeys: Passkey[];
  /** Other devices signed in to the account. This device is added on top. */
  otherDevices: Device[];
  email: string | null;
  emailVerified: boolean;
};

const HOUR_MS = 60 * 60 * 1000;

/** Made-up passkeys and devices for the demo accounts, dated relative to now. */
function demoPasskeys(at: number): Passkey[] {
  return [
    {
      id: "passkey_demo_1",
      name: "iPhone",
      createdAt: new Date(at - 40 * 24 * HOUR_MS).toISOString(),
      lastUsedAt: new Date(at - 2 * HOUR_MS).toISOString(),
    },
    {
      id: "passkey_demo_2",
      name: "Chrome on Windows",
      createdAt: new Date(at - 12 * 24 * HOUR_MS).toISOString(),
      lastUsedAt: null,
    },
  ];
}

function demoDevices(at: number): Device[] {
  return [
    {
      id: "device_demo_1",
      label: "Finvest app on iPhone",
      kind: "app",
      confirmedWith: "passkey",
      signedInAt: new Date(at - 6 * 24 * HOUR_MS).toISOString(),
      lastSeenAt: new Date(at - 3 * HOUR_MS).toISOString(),
      current: false,
    },
    {
      id: "device_demo_2",
      label: "Chrome on Windows",
      kind: "browser",
      confirmedWith: "code",
      signedInAt: new Date(at - 2 * 24 * HOUR_MS).toISOString(),
      lastSeenAt: new Date(at - 30 * HOUR_MS).toISOString(),
      current: false,
    },
  ];
}

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
  /** Hears each email link's token, as a test would find it in the email. */
  onEmailLink?: (token: string) => void;
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
        passkeys: demoPasskeys(now()),
        otherDevices: demoDevices(now()),
        email: "amara@example.com",
        emailVerified: false,
      },
    ],
    [
      DEMO_PHONE_AUTHENTICATOR,
      {
        password: DEMO_PASSWORD,
        displayName: "Joseph Okello",
        hasPasskey: false,
        hasAuthenticator: true,
        passkeys: [],
        otherDevices: [],
        email: "joseph@example.com",
        emailVerified: true,
      },
    ],
  ]);
  const signups = new Map<string, SignupStart>();
  const tickets = new Map<string, SignupStart>();
  const logins = new Map<string, string>();
  /** Email links sent, and the number of the account each proves. */
  const emailLinks = new Map<string, string>();
  /** Reset challenges and the number each was for. A number with no account gets one too. */
  const resets = new Map<string, string>();
  /** A set-up that was started and not yet confirmed, for this session only. */
  let pendingAuthenticatorAt: number | null = null;
  let signedInAt = 0;
  let signedInRestricted = false;
  /** How this device's session was confirmed, for its row in the device list. */
  let confirmedWith: Device["confirmedWith"] = "signup";
  /** The number of whoever is signed in, as the server would know from the session. */
  let signedIn: string | null = null;
  let counter = 0;

  const nextId = (prefix: string) => `${prefix}_${(counter += 1)}`;
  const wait = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const sessionFor = (phone: string, restricted: boolean): Session => {
    signedInRestricted = restricted;
    signedIn = phone;
    signedInAt = now();
    pendingAuthenticatorAt = null;
    const account = accounts.get(phone);
    return {
      phone,
      displayName: account?.displayName ?? null,
      restricted,
      email: account?.email ?? null,
      emailVerified: account?.emailVerified ?? false,
    };
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
    async restoreSession(): Promise<Session | null> {
      // Nothing is stored, so a reload always starts signed out. While the app
      // runs it answers for the session it opened, as the API would.
      if (!signedIn) {
        return null;
      }
      const account = accounts.get(signedIn);
      return account
        ? {
            phone: signedIn,
            displayName: account.displayName,
            restricted: signedInRestricted,
            email: account.email,
            emailVerified: account.emailVerified,
          }
        : null;
    },

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
        passkeys: [],
        otherDevices: [],
        email: signup.email.trim() || null,
        emailVerified: false,
      });
      confirmedWith = "signup";
      return sessionFor(signup.phone, false);
    },

    async startLogin(identifier, password): Promise<LoginChallenge> {
      await wait();
      const byEmail = "email" in identifier;
      const email = byEmail ? identifier.email.trim().toLowerCase() : null;
      // Like the API, only a proved address finds an account.
      const phone = byEmail
        ? [...accounts].find(([, held]) => held.emailVerified && held.email === email)?.[0]
        : identifier.phone;
      const account = phone ? accounts.get(phone) : undefined;
      if (!phone || !account || account.password !== password) {
        throw new AuthError(byEmail ? "invalid_email_credentials" : "invalid_credentials");
      }
      const loginId = nextId("login");
      logins.set(loginId, phone);
      return { loginId, phone: byEmail ? null : phone, email, methods: methodsFor(account) };
    },

    async verifyWithPasskey(loginId): Promise<Session> {
      await wait();
      const phone = logins.get(loginId);
      if (!phone || !accounts.get(phone)?.hasPasskey) {
        throw new AuthError("expired");
      }
      logins.delete(loginId);
      confirmedWith = "passkey";
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
      confirmedWith = "authenticator";
      return sessionFor(phone, false);
    },

    async sendLoginCode(loginId, _channel): Promise<void> {
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
      confirmedWith = "code";
      return sessionFor(phone, true);
    },

    async listSecondSteps(): Promise<SecondStep[]> {
      await wait();
      return methodsFor(currentAccount());
    },

    async addPasskey(): Promise<void> {
      await wait();
      const account = recentAccount();
      account.hasPasskey = true;
      account.passkeys.push({
        id: nextId("passkey"),
        name: "This device",
        createdAt: new Date(now()).toISOString(),
        lastUsedAt: null,
      });
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

    async startPasswordReset(phone): Promise<PasswordReset> {
      await wait();
      // A new code replaces any older one for the same number.
      for (const [id, number] of resets) {
        if (number === phone) resets.delete(id);
      }
      const challengeId = nextId("reset");
      resets.set(challengeId, phone);
      return { challengeId, phone };
    },

    async completePasswordReset(challengeId, code, password): Promise<void> {
      await wait();
      const phone = resets.get(challengeId);
      if (!phone) {
        throw new AuthError("expired");
      }
      if (LEAKED.has(password.toLowerCase())) {
        throw new AuthError("leaked_password");
      }
      const account = accounts.get(phone);
      // A number with no account fails exactly like a wrong code.
      if (code !== DEMO_CODE || !account) {
        throw new AuthError("invalid_code");
      }
      resets.delete(challengeId);
      account.password = password;
      signedIn = null;
      pendingAuthenticatorAt = null;
    },

    async listDevices(): Promise<Device[]> {
      await wait();
      const account = currentAccount();
      const here: Device = {
        id: "device_this",
        label: "Chrome on this computer",
        kind: "browser",
        confirmedWith,
        signedInAt: new Date(signedInAt).toISOString(),
        lastSeenAt: new Date(now()).toISOString(),
        current: true,
      };
      return [here, ...account.otherDevices];
    },

    async signOutDevice(id): Promise<void> {
      await wait();
      const account = currentAccount();
      const left = account.otherDevices.filter((device) => device.id !== id);
      if (left.length === account.otherDevices.length) {
        throw new AuthError("not_found");
      }
      account.otherDevices = left;
    },

    async signOutOtherDevices(): Promise<number> {
      await wait();
      const account = currentAccount();
      const ended = account.otherDevices.length;
      account.otherDevices = [];
      return ended;
    },

    async listPasskeys(): Promise<Passkey[]> {
      await wait();
      return [...currentAccount().passkeys];
    },

    async removePasskey(id): Promise<void> {
      await wait();
      const account = recentAccount();
      const left = account.passkeys.filter((passkey) => passkey.id !== id);
      if (left.length === account.passkeys.length) {
        throw new AuthError("not_found");
      }
      account.passkeys = left;
      account.hasPasskey = left.length > 0;
    },

    async turnOffAuthenticator(): Promise<void> {
      await wait();
      const account = recentAccount();
      if (!account.hasAuthenticator) {
        throw new AuthError("not_found");
      }
      account.hasAuthenticator = false;
    },

    async sendEmailProof(): Promise<"sent" | "proved"> {
      await wait();
      const account = currentAccount();
      if (!account.email) {
        throw new AuthError("not_accepted");
      }
      if (account.emailVerified) {
        return "proved";
      }
      // A new link replaces any older one, as on the server.
      for (const [token, phone] of emailLinks) {
        if (phone === signedIn) emailLinks.delete(token);
      }
      // Long enough to look like a link token: the app checks the shape before sending it.
      const token = `${nextId("email_link")}_demo_link_token`;
      emailLinks.set(token, signedIn as string);
      options.onEmailLink?.(token);
      return "sent";
    },

    async confirmEmail(token): Promise<void> {
      await wait();
      if (token === DEMO_EMAIL_TOKEN) {
        return;
      }
      const phone = emailLinks.get(token);
      const account = phone ? accounts.get(phone) : undefined;
      if (!account) {
        throw new AuthError("expired");
      }
      emailLinks.delete(token);
      account.emailVerified = true;
    },

    async signOut(): Promise<void> {
      await wait();
      signedIn = null;
      pendingAuthenticatorAt = null;
    },
  };
}
