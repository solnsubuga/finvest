import {
  AuthError,
  PASSWORD_MIN_LENGTH,
  type AuthApi,
  type SecondStepMethod,
  type SignInChallenge,
} from "./api.ts";
import { PERMISSIONS, type Permission, type Role, type StaffSession } from "./types.ts";

/**
 * Sample staff for local work, until the backend's sign-in lands.
 *
 * This is not security. It holds no passwords and checks none, it never calls
 * WebAuthn, it takes any six digits but 000000 as an authenticator code, and
 * it is only used under `pnpm dev` or when VITE_STUB_API is
 * "true". See createAuthApi in ./index.ts.
 */

const ALL: readonly Permission[] = PERMISSIONS;

/**
 * Which screens each role opens. A proposal taken from the role matrix on the
 * canvas ("Team and permissions"). The backend's permissions module is the
 * authority once it exists.
 */
export const STUB_ROLE_PERMISSIONS: Record<Role, readonly Permission[]> = {
  admin: ALL,
  operations: [
    "overview.view",
    "review_queue.view",
    "identity_checks.view",
    "maturities.view",
    "support.view",
    "complaints.view",
    "clients.view",
    "money_movement.view",
    "reconciliation.view",
    "investments.view",
    "automatic_investing.view",
    "goals.view",
    "reports.view",
    "migration.view",
    "migration.import",
    "support.reply",
    "complaints.manage",
    "complaints.close",
    "bank_transfers.change",
    "deposit_reassignments.change",
    "clients.lock",
    "clients.close",
    "email_suppressions.lift",
    "payout_accounts.change",
    "identity_checks.review",
    "client_phones.change",
  ],
  finance: [
    "overview.view",
    "review_queue.view",
    "maturities.view",
    "clients.view",
    "money_movement.view",
    "reconciliation.view",
    "reconciliation.change",
    "investments.view",
    "automatic_investing.view",
    "goals.view",
    "dealing_desk.view",
    "dealing_orders.change",
    "finance.view",
    "tax_remittances.change",
    "products.view",
    "rates.view",
    "rates.change",
    "investment_corrections.change",
    "reports.view",
    "migration.view",
    "migration.sign_off",
    "bank_transfers.change",
    "deposit_reassignments.change",
    "bank_payouts.change",
  ],
  compliance: [
    "overview.view",
    "review_queue.view",
    "identity_checks.view",
    "complaints.view",
    "clients.view",
    "compliance.view",
    "reports.view",
    "complaints.manage",
    "complaints.close",
    "audit.view",
    "clients.lock",
    "clients.close",
    "payout_accounts.change",
    "identity_checks.review",
    "compliance_cases.change",
  ],
  // Support works complaints but does not close them, as the backend's table says.
  support: ["overview.view", "support.view", "complaints.view", "clients.view", "support.reply", "complaints.manage", "email_suppressions.lift"],
};

interface StubStaff {
  email: string;
  staffId: string;
  name: string;
  role: Role;
  /** The second steps this person has set up. Staff choose their own. */
  methods: readonly SecondStepMethod[];
}

const BOTH: readonly SecondStepMethod[] = ["passkey", "authenticator"];

/** The one code the stub refuses, so the error state can be seen. */
export const STUB_WRONG_CODE = "000000";

/** Names are the sample people drawn on the canvas. The .test domain is reserved and cannot receive mail. */
export const STUB_STAFF: readonly StubStaff[] = [
  { email: "admin@finvest.test", staffId: "stub-admin", name: "Solomon N.", role: "admin", methods: BOTH },
  {
    email: "operations@finvest.test",
    staffId: "stub-operations",
    name: "Grace Auma",
    role: "operations",
    methods: BOTH,
  },
  {
    email: "finance@finvest.test",
    staffId: "stub-finance",
    name: "Daniel Opio",
    role: "finance",
    methods: ["authenticator"],
  },
  {
    email: "compliance@finvest.test",
    staffId: "stub-compliance",
    name: "Miriam Atim",
    role: "compliance",
    methods: ["passkey"],
  },
  {
    email: "support@finvest.test",
    staffId: "stub-support",
    name: "Tom Byaruhanga",
    role: "support",
    methods: BOTH,
  },
  // Invited but has not set up a second step, so cannot sign in.
  {
    email: "invited@finvest.test",
    staffId: "stub-invited",
    name: "Sample Invitee",
    role: "support",
    methods: [],
  },
];

const STORAGE_KEY = "finvest.console.stub-session";
const ENROLLED_KEY = "finvest.console.stub-enrolled";

/** The set-up link for the invited sample person is /enrol#sample-invite. Any other token is treated as expired. */
export const STUB_ENROLMENT_TOKEN = "sample-invite";
const STUB_INVITED_ID = "stub-invited";

/** The one password the stub calls leaked, so that error can be seen. It is not a real credential. */
export const STUB_LEAKED_PASSWORD = "password1234";

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/** A fresh random key each time, so no secret is ever written into the repository. */
function randomSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Array.from(bytes, (byte) => BASE32[byte % 32]).join("");
}

function toSession(staff: StubStaff): StaffSession {
  return {
    staffId: staff.staffId,
    name: staff.name,
    role: staff.role,
    permissions: STUB_ROLE_PERMISSIONS[staff.role],
    secondSteps: staff.methods,
  };
}

export function createStubAuthApi(storage: Storage = window.sessionStorage): AuthApi {
  const pending = new Map<string, StubStaff>();
  let nextChallenge = 1;

  /** What the person has set up: the sample value, or what they chose at set-up in this browser session. */
  function methodsFor(staff: StubStaff): readonly SecondStepMethod[] {
    if (staff.staffId !== STUB_INVITED_ID) return staff.methods;
    const chosen = storage.getItem(ENROLLED_KEY);
    return chosen === "passkey" || chosen === "authenticator" ? [chosen] : [];
  }

  function invitee(token: string): StubStaff {
    const staff = STUB_STAFF.find((s) => s.staffId === STUB_INVITED_ID);
    // A link works once: after set-up it is spent.
    if (token !== STUB_ENROLMENT_TOKEN || !staff || methodsFor(staff).length > 0) {
      throw new AuthError("link_expired");
    }
    return staff;
  }

  function checkPassword(password: string): void {
    if (password.length < PASSWORD_MIN_LENGTH) throw new AuthError("password_too_short");
    if (password.length > 128) throw new AuthError("password_too_long");
    if (password === STUB_LEAKED_PASSWORD) throw new AuthError("password_leaked");
  }

  function enrol(token: string, password: string, method: SecondStepMethod): StaffSession {
    const staff = invitee(token);
    checkPassword(password);
    storage.setItem(ENROLLED_KEY, method);
    storage.setItem(STORAGE_KEY, staff.staffId);
    return toSession(staff);
  }

  /** Runs a step that may throw and hands back a promise, as the real client would. */
  function attempt<T>(step: () => T): Promise<T> {
    try {
      return Promise.resolve(step());
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new AuthError("unavailable"));
    }
  }

  return {
    getSession() {
      const staffId = storage.getItem(STORAGE_KEY);
      const staff = STUB_STAFF.find((s) => s.staffId === staffId);
      return Promise.resolve(staff ? toSession(staff) : null);
    },

    startSignIn(email, password) {
      const staff = STUB_STAFF.find((s) => s.email === email.trim().toLowerCase());
      if (!staff || password.length === 0) {
        return Promise.reject(new AuthError("invalid_credentials"));
      }
      const methods = methodsFor(staff);
      if (methods.length === 0) {
        return Promise.reject(new AuthError("second_step_missing"));
      }
      const challenge: SignInChallenge = {
        challengeId: `stub-${nextChallenge++}`,
        methods,
      };
      pending.set(challenge.challengeId, staff);
      return Promise.resolve(challenge);
    },

    finishSignIn(challenge, answer) {
      const staff = pending.get(challenge.challengeId);
      if (!staff || !methodsFor(staff).includes(answer.method)) {
        return Promise.reject(new AuthError(answer.method === "passkey" ? "passkey_failed" : "invalid_code"));
      }
      if (answer.method === "authenticator") {
        // A wrong code leaves the challenge open so the person can try again.
        if (!/^\d{6}$/.test(answer.code) || answer.code === STUB_WRONG_CODE) {
          return Promise.reject(new AuthError("invalid_code"));
        }
      }
      pending.delete(challenge.challengeId);
      storage.setItem(STORAGE_KEY, staff.staffId);
      return Promise.resolve(toSession(staff));
    },

    signOut() {
      storage.removeItem(STORAGE_KEY);
      return Promise.resolve();
    },

    getEnrolment(token) {
      return attempt(() => {
        const staff = invitee(token);
        return { name: staff.name, email: staff.email, role: staff.role, methods: BOTH };
      });
    },

    enrolWithPasskey(token, password) {
      return attempt(() => enrol(token, password, "passkey"));
    },

    startAuthenticatorEnrolment(token) {
      return attempt(() => {
        const staff = invitee(token);
        const secret = randomSecret();
        const label = encodeURIComponent(`Finvest:${staff.email}`);
        return { secret, uri: `otpauth://totp/${label}?secret=${secret}&issuer=Finvest` };
      });
    },

    enrolWithAuthenticator(token, password, code) {
      return attempt(() => {
        invitee(token);
        checkPassword(password);
        if (!/^\d{6}$/.test(code) || code === STUB_WRONG_CODE) throw new AuthError("invalid_code");
        return enrol(token, password, "authenticator");
      });
    },
  };
}
