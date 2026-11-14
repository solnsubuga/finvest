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
  // Logging a complaint for a client, assigning it, moving it, adding notes, and proposing or withdrawing an outcome.
  "complaints.manage",
  // Confirming or rejecting an outcome someone else proposed. Confirming closes the complaint.
  "complaints.close",
  // Proposing or deciding a change to the words of a client message.
  "wordings.change",
  // Proposing or deciding an announcement to every active client. Apart from wordings, so fewer people can do it.
  "announcements.send",
  // Matching a bank transfer to a statement line, refusing it, and approving someone else's match.
  "bank_transfers.change",
  // Proposing to move a bank transfer credited to the wrong client, approving or sending back someone else's, withdrawing your own.
  "deposit_reassignments.change",
  // Checking a client's proof of a payout account, approving someone else's check, refusing it, or removing the account.
  "payout_accounts.change",
  // Proposing a rate change, approving or sending back someone else's, and withdrawing your own.
  "rates.change",
  // Inviting staff, changing a role, deactivating or reactivating, resetting sign-in. Two admins.
  "team.change",
  // Reading the audit trail and exporting it.
  "audit.view",
  // Locking or unlocking a client's account, with a second person.
  "clients.lock",
  // Lifting an email suppression, with a second person.
  "email_suppressions.lift",
  // Preparing, approving, sending back or withdrawing a dealing order, and recording, confirming or rejecting its result.
  "dealing_orders.change",
  // Approving, rejecting, asking for new photos or escalating an identity check. One person decides.
  "identity_checks.review",
] as const;

export const PERMISSIONS = [...SCREEN_PERMISSIONS, ...ACTION_PERMISSIONS] as const;
export type ScreenPermission = (typeof SCREEN_PERMISSIONS)[number];
export type Permission = (typeof PERMISSIONS)[number];

export interface StaffSession {
  staffId: string;
  name: string;
  role: Role;
  permissions: readonly Permission[];
  /**
   * The second steps this person holds, passkey first, which the "confirm it
   * is you" dialog offers. Empty when the API did not say.
   */
  secondSteps: readonly ("passkey" | "authenticator")[];
}

export function can(session: StaffSession, permission: Permission): boolean {
  return session.permissions.includes(permission);
}
