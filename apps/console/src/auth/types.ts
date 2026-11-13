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

/**
 * What a member of staff may do on a screen they can open. Looking is the
 * screen's `.view` permission. Each action that changes something has its own,
 * the same names as the backend's catalogue.
 */
export const ACTION_PERMISSIONS = [
  // Running a trial import, and proposing or approving a sign-off for the switch.
  "migration.import",
  "migration.sign_off",
  // Writing to a client in the help chat, solving or filing a chat, keeping saved replies.
  "support.reply",
  // Logging a complaint for a client, assigning it, moving it and adding notes.
  "complaints.manage",
  // Recording the outcome and closing a complaint.
  "complaints.close",
  // Proposing or deciding a change to the words of a client message.
  "wordings.change",
] as const;

export const PERMISSIONS = [...SCREEN_PERMISSIONS, ...ACTION_PERMISSIONS] as const;
export type ScreenPermission = (typeof SCREEN_PERMISSIONS)[number];
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
