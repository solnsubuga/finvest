import type { Permission, Role, ScreenPermission } from "@/auth/types.ts";
import { NAV_ITEMS } from "@/nav/nav.ts";
import type { AccessSync, Change, ChangeKind, Member, StaffStatus } from "./api.ts";

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
  { permission: "clients.close", label: "Close client accounts", under: "clients.view" },
  { permission: "client_phones.change", label: "Change a client's mobile number", under: "clients.view" },
  { permission: "client_notes.write", label: "Write staff notes on a client", under: "clients.view" },
  { permission: "client_pins.reset", label: "Reset a client's app PIN", under: "clients.view" },
  { permission: "client_limits.change", label: "Change a client's withdrawal limits", under: "clients.view" },
  { permission: "client_statements.send", label: "Send a client a statement", under: "clients.view" },
  { permission: "client_withdrawals.start", label: "Start a withdrawal a client asked for", under: "clients.view" },
  { permission: "email_suppressions.lift", label: "Lift email suppressions", under: "clients.view" },
  { permission: "bank_transfers.change", label: "Match bank transfers", under: "reconciliation.view" },
  { permission: "reconciliation.change", label: "Close the day and settle stranded payouts", under: "reconciliation.view" },
  { permission: "identity_checks.review", label: "Decide identity checks", under: "identity_checks.view" },
  { permission: "deposit_reassignments.change", label: "Move payments credited to the wrong client", under: "reconciliation.view" },
  { permission: "payout_accounts.change", label: "Check payout accounts", under: "review_queue.view" },
  { permission: "bank_payouts.change", label: "Pay and settle bank payouts", under: "money_movement.view" },
  { permission: "dealing_orders.change", label: "Deal and settle orders", under: "dealing_desk.view" },
  { permission: "tax_remittances.change", label: "Pay withholding tax to URA", under: "finance.view" },
  { permission: "rates.change", label: "Change rates", under: "rates.view" },
  { permission: "products.change", label: "Add, change, pause and open products", under: "products.view" },
  { permission: "investment_corrections.change", label: "Undo investments and refund", under: "investments.view" },
  { permission: "investment_transfers.change", label: "Let large investment transfers through", under: "review_queue.view" },
  { permission: "organisations.review", label: "Check and open company and group accounts", under: "review_queue.view" },
  { permission: "team.change", label: "Change the team", under: "team.view" },
  { permission: "compliance_cases.change", label: "Close screening and monitoring cases", under: "compliance.view" },
  { permission: "audit.view", label: "Read the audit trail", under: "reports.view" },
  { permission: "wordings.change", label: "Change client messages", under: "settings.view" },
  { permission: "announcements.send", label: "Send announcements", under: "settings.view" },
  { permission: "bank_accounts.change", label: "Change the bank account clients pay into", under: "settings.view" },
  { permission: "account_codes.change", label: "Change accounting system codes", under: "settings.view" },
  { permission: "withdrawal_limits.change", label: "Change the withdrawal limits", under: "settings.view" },
  { permission: "payment_channels.change", label: "Pause or open payment channels", under: "settings.view" },
  { permission: "calendar.change", label: "Change holidays, the bank cut-off and reminders", under: "settings.view" },
  { permission: "staff_security.change", label: "Change office networks and staff sign-out", under: "settings.view" },
  { permission: "statement_layouts.change", label: "Change statement import layouts", under: "settings.view" },
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

export type AccessTone = "good" | "waiting" | "bad" | "quiet";

const ACCESS_PROBLEMS: Record<string, string> = {
  cloudflare_token_refused: "Cloudflare refused the token. Check that FINVEST_CLOUDFLARE_ACCESS_TOKEN has Zero Trust edit rights.",
  cloudflare_list_missing: "Cloudflare could not find the staff list. Check FINVEST_CLOUDFLARE_STAFF_LIST_ID.",
  cloudflare_refused: "Cloudflare would not take this address.",
  cloudflare_unavailable: "Cloudflare did not answer.",
  cloudflare_unreachable: "Cloudflare could not be reached.",
  access_not_confirmed: "Cloudflare took the change but does not show it yet.",
};

/** What the team view says about Cloudflare Access for one person. */
export function accessLabel(member: Pick<Member, "status" | "access">): { text: string; tone: AccessTone; detail: string | null } {
  const sync: AccessSync | null = member.access;
  const problem = sync?.errorCode ? (ACCESS_PROBLEMS[sync.errorCode] ?? "Cloudflare did not finish it.") : null;
  if (!sync) return { text: "Access not checked yet", tone: "quiet", detail: "The daily check will look at Cloudflare." };
  switch (sync.state) {
    case "in_access":
      return { text: "Can reach the console", tone: "good", detail: null };
    case "removed":
      return { text: "Removed from Access", tone: "quiet", detail: null };
    case "waiting":
      return {
        text: member.status === "disabled" ? "Removing from Access" : "Adding to Access",
        tone: "waiting",
        detail: problem ? `${problem} Trying again.` : null,
      };
    case "failed":
      return { text: "Access not updated", tone: "bad", detail: problem };
    case "not_set_up":
      return { text: "Access sync is off", tone: "quiet", detail: "Cloudflare settings are not set for the worker, so add or remove this address by hand." };
  }
}
