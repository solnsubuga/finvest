import type { ApprovalItem, Kind } from "./api.ts";

interface KindInfo {
  label: string;
  /** Where the item is decided. */
  path: (item: ApprovalItem) => string;
}

const at = (path: string) => () => path;

export const KIND_INFO: Record<Kind, KindInfo> = {
  wording_change: { label: "Client message wording", path: at("/settings") },
  announcement: { label: "Announcement to clients", path: at("/settings") },
  ledger_correction: { label: "Ledger correction", path: at("/finance") },
  pot_transfer: { label: "Transfer between pots", path: at("/reconciliation") },
  bank_transfer_match: { label: "Bank transfer match", path: at("/reconciliation") },
  day_close: { label: "Closing the day", path: at("/reconciliation") },
  dealing_order: { label: "Dealing order", path: at("/dealing-desk") },
  dealing_result: { label: "Dealing result", path: at("/dealing-desk") },
  // The subject is "UGX from 2026-12-01", or "UGX paid in" or "UGX taken out".
  buffer_rules: { label: "Buffer rules change", path: at("/dealing-desk") },
  buffer_capital: { label: "Finvest's money in the buffer", path: at("/dealing-desk") },
  rate_change: { label: "Rate change", path: at("/rates") },
  migration_totals: { label: "Migration totals", path: at("/migration") },
  migration_sign_off: { label: "Migration sign-off", path: at("/migration") },
  phone_change: { label: "Client's mobile number", path: (item) => `/clients?client=${encodeURIComponent(item.subject)}` },
  team_change: { label: "Team change", path: at("/team") },
  // The subject is "lock <client ID>" or "unlock <client ID>".
  client_lock: { label: "Client lock", path: (item) => `/clients?client=${encodeURIComponent(item.subject.split(" ").pop() ?? "")}` },
  suppression_lift: { label: "Email suppression lift", path: at("/clients") },
  complaint_outcome: { label: "Complaint outcome", path: at("/complaints") },
  payout_account: { label: "Payout account check", path: at("/review-queue") },
  // The subject is the payment's ID; the reassignment opens by its own.
  deposit_reassignment: { label: "Payment to move to another client", path: (item) => `/reconciliation?reassignment=${encodeURIComponent(item.id)}` },
  tax_remittance: { label: "Payment to URA", path: at("/finance") },
  // The subject is the outcome proposed: "paid" or "failed".
  payout_resolution: { label: "Stranded payout to settle", path: at("/money-movement") },
  // The subject is the payout's ID, which the held payouts tab opens by.
  payout_release: {
    label: "Large payout to release",
    path: (item) => `/review-queue?tab=held&payout=${encodeURIComponent(item.subject)}`,
  },
};

const SUBJECT_WORDS: Record<string, string> = {
  invite: "Invite",
  change_role: "Role change",
  deactivate: "Deactivate",
  reactivate: "Reactivate",
  reset_sign_in: "Reset sign-in",
  mobile_money: "Mobile money",
  bank: "Bank account",
};

/** The subject in words where it is a code, such as `change_role` or `lock -N…`. */
export function describeSubject(item: ApprovalItem): string {
  if (item.kind === "client_lock") {
    const [kind, client] = item.subject.split(" ");
    return `${kind === "unlock" ? "Unlock" : "Lock"} client ${client ?? ""}`.trim();
  }
  if (item.kind === "suppression_lift") return `Address ${item.subject}…`;
  if (item.kind === "phone_change") return `Client ${item.subject}`;
  return SUBJECT_WORDS[item.subject] ?? item.subject;
}

export function kindLabel(item: ApprovalItem): string {
  return item.kind ? KIND_INFO[item.kind].label : "Approval";
}

export function linkFor(item: ApprovalItem): string | null {
  return item.kind ? KIND_INFO[item.kind].path(item) : null;
}

/** "5 minutes ago", "3 hours ago", "2 days ago". */
export function waitingFor(iso: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} days ago`;
}
