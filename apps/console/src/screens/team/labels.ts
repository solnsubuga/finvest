import type { Permission, Role, ScreenPermission } from "@/auth/types.ts";
import { NAV_ITEMS } from "@/nav/nav.ts";
import type { Change, ChangeKind, Member, StaffStatus } from "./api.ts";

export const STATUS_LABELS: Record<StaffStatus, string> = {
  invited: "Invited",
  active: "Active",
  disabled: "Deactivated",
};

export const KIND_LABELS: Record<ChangeKind, string> = {
  invite: "Invite",
  change_role: "Change role",
  deactivate: "Deactivate",
  reactivate: "Reactivate",
  reset_sign_in: "Reset sign-in",
};

/** Actions on a screen, each listed under the screen it is done on. Opening a screen is its menu label. */
const ACTIONS: readonly { permission: Permission; label: string; under: ScreenPermission }[] = [
  { permission: "support.reply", label: "Reply to clients", under: "support.view" },
  { permission: "complaints.manage", label: "Log and work complaints", under: "complaints.view" },
  { permission: "complaints.close", label: "Close complaints", under: "complaints.view" },
  { permission: "clients.lock", label: "Lock client accounts", under: "clients.view" },
  { permission: "email_suppressions.lift", label: "Lift email suppressions", under: "clients.view" },
  { permission: "reconciliation.change", label: "Import statements, close days and settle stranded payouts", under: "reconciliation.view" },
  { permission: "bank_transfers.change", label: "Match bank transfers", under: "reconciliation.view" },
  { permission: "identity_checks.review", label: "Decide identity checks", under: "identity_checks.view" },
  { permission: "deposit_reassignments.change", label: "Move payments credited to the wrong client", under: "reconciliation.view" },
  { permission: "payout_accounts.change", label: "Check payout accounts", under: "review_queue.view" },
  { permission: "dealing_orders.change", label: "Deal and settle orders", under: "dealing_desk.view" },
  { permission: "tax_remittances.change", label: "Pay withholding tax to URA", under: "finance.view" },
  { permission: "rates.change", label: "Change rates", under: "rates.view" },
  { permission: "investment_corrections.change", label: "Undo investments and refund", under: "investments.view" },
  { permission: "team.change", label: "Change the team", under: "team.view" },
  { permission: "audit.view", label: "Read the audit trail", under: "reports.view" },
  { permission: "wordings.change", label: "Change client messages", under: "settings.view" },
  { permission: "announcements.send", label: "Send announcements", under: "settings.view" },
  { permission: "migration.import", label: "Run a trial import", under: "migration.view" },
  { permission: "migration.sign_off", label: "Sign off the switch", under: "migration.view" },
];

export interface PermissionRow {
  permission: Permission;
  label: string;
  /** An action inside a screen, shown indented under it. */
  action: boolean;
}

/** Every screen in menu order, with the actions done on it after it. */
export function permissionRows(): PermissionRow[] {
  return NAV_ITEMS.flatMap((item) => [
    { permission: item.permission, label: item.label, action: false },
    ...ACTIONS.filter((action) => action.under === item.permission).map((action) => ({
      permission: action.permission,
      label: action.label,
      action: true,
    })),
  ]);
}

const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export function secondSteps(member: Member): string {
  if (member.hasPasskey && member.hasAuthenticator) return "Passkey and authenticator app";
  if (member.hasPasskey) return "Passkey";
  if (member.hasAuthenticator) return "Authenticator app";
  return "None set up";
}

/** One line saying what a change will do, as the second admin reads it. */
export function describeChange(change: Change, roleLabel: (role: Role) => string): string {
  switch (change.kind) {
    case "invite":
      return `Invite ${change.staffName} (${change.email ?? ""}) as ${change.role ? roleLabel(change.role) : "staff"}`;
    case "change_role":
      return `Move ${change.staffName} from ${change.currentRole ? roleLabel(change.currentRole) : "their role"} to ${change.role ? roleLabel(change.role) : "another role"}`;
    case "deactivate":
      return `Deactivate ${change.staffName}. Their sessions end at once.`;
    case "reactivate":
      return `Reactivate ${change.staffName}. They set up sign-in again from a new link.`;
    case "reset_sign_in":
      return `Reset sign-in for ${change.staffName}. They get a new set-up link by email.`;
  }
}
