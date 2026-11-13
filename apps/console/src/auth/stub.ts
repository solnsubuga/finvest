import { AuthError, type AuthApi, type PasskeyChallenge } from "./api.ts";
import { PERMISSIONS, type Permission, type Role, type StaffSession } from "./types.ts";

/**
 * Sample staff for local work, until the backend's sign-in lands.
 *
 * This is not security. It holds no passwords and checks none, it never calls
 * WebAuthn, and it is only used under `pnpm dev` or when VITE_STUB_API is
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
    "reports.view",
    "migration.view",
  ],
  finance: [
    "overview.view",
    "review_queue.view",
    "maturities.view",
    "clients.view",
    "money_movement.view",
    "reconciliation.view",
    "investments.view",
    "dealing_desk.view",
    "finance.view",
    "products.view",
    "rates.view",
    "reports.view",
  ],
  compliance: [
    "overview.view",
    "review_queue.view",
    "identity_checks.view",
    "complaints.view",
    "clients.view",
    "compliance.view",
    "reports.view",
  ],
  support: ["overview.view", "support.view", "complaints.view", "clients.view"],
};

interface StubStaff {
  email: string;
  staffId: string;
  name: string;
  role: Role;
}

/** Names are the sample people drawn on the canvas. The .test domain is reserved and cannot receive mail. */
export const STUB_STAFF: readonly StubStaff[] = [
  { email: "admin@finvest.test", staffId: "stub-admin", name: "Solomon N.", role: "admin" },
  { email: "operations@finvest.test", staffId: "stub-operations", name: "Grace Auma", role: "operations" },
  { email: "finance@finvest.test", staffId: "stub-finance", name: "Daniel Opio", role: "finance" },
  { email: "compliance@finvest.test", staffId: "stub-compliance", name: "Miriam Atim", role: "compliance" },
  { email: "support@finvest.test", staffId: "stub-support", name: "Tom Byaruhanga", role: "support" },
];

const STORAGE_KEY = "finvest.console.stub-session";

function toSession(staff: StubStaff): StaffSession {
  return {
    staffId: staff.staffId,
    name: staff.name,
    role: staff.role,
    permissions: STUB_ROLE_PERMISSIONS[staff.role],
  };
}

export function createStubAuthApi(storage: Storage = window.sessionStorage): AuthApi {
  const pending = new Map<string, StubStaff>();
  let nextChallenge = 1;

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
      const challenge: PasskeyChallenge = { challengeId: `stub-${nextChallenge++}` };
      pending.set(challenge.challengeId, staff);
      return Promise.resolve(challenge);
    },

    finishSignIn(challenge) {
      const staff = pending.get(challenge.challengeId);
      if (!staff) {
        return Promise.reject(new AuthError("passkey_failed"));
      }
      pending.delete(challenge.challengeId);
      storage.setItem(STORAGE_KEY, staff.staffId);
      return Promise.resolve(toSession(staff));
    },

    signOut() {
      storage.removeItem(STORAGE_KEY);
      return Promise.resolve();
    },
  };
}
