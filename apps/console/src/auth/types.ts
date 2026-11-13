/** Staff roles, as drawn on the "Team and permissions" screen of the canvas. */
export const ROLES = ["admin", "operations", "finance", "compliance", "support"] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  operations: "Operations",
  finance: "Finance",
  compliance: "Compliance",
  support: "Support",
};

/**
 * What a signed-in member of staff may open. The backend decides these and
 * sends them with the session. The console only uses them to choose what to
 * show: the API checks every request again.
 */
export const SCREEN_PERMISSIONS = [
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
  "dealing_desk.view",
  "finance.view",
  "products.view",
  "rates.view",
  "compliance.view",
  "team.view",
  "reports.view",
  "settings.view",
  "migration.view",
] as const;

/** What a member of staff may do on a screen they can open, beyond looking. */
export const ACTION_PERMISSIONS = ["migration.import", "migration.sign_off"] as const;

/** Every permission the console uses. The backend may send others, for screens not built yet. */
export const PERMISSIONS = [...SCREEN_PERMISSIONS, ...ACTION_PERMISSIONS] as const;

export type Permission = (typeof PERMISSIONS)[number];

export interface StaffSession {
  staffId: string;
  name: string;
  role: Role;
  permissions: readonly Permission[];
}

export function can(session: StaffSession, permission: Permission): boolean {
  return session.permissions.includes(permission);
}
