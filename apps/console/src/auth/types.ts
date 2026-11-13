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
export const PERMISSIONS = [
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
