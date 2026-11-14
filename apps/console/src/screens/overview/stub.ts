/**
 * A sample inbox for `pnpm dev` and preview builds. Which items show and who may decide them
 * follows the API's table in docs/approvals.md, so each sample role sees what it would.
 */

import type { Permission } from "@/auth/types.ts";
import type { ApprovalItem, ApprovalsApi, Kind } from "./api.ts";

const SOURCES: Record<Kind, { view: Permission; decide: Permission }> = {
  wording_change: { view: "settings.view", decide: "wordings.change" },
  ledger_correction: { view: "finance.view", decide: "finance.view" },
  pot_transfer: { view: "reconciliation.view", decide: "reconciliation.change" },
  bank_transfer_match: { view: "reconciliation.view", decide: "bank_transfers.change" },
  day_close: { view: "reconciliation.view", decide: "reconciliation.change" },
  dealing_order: { view: "dealing_desk.view", decide: "dealing_desk.view" },
  dealing_result: { view: "dealing_desk.view", decide: "dealing_desk.view" },
  buffer_rules: { view: "dealing_desk.view", decide: "dealing_orders.change" },
  buffer_capital: { view: "dealing_desk.view", decide: "dealing_orders.change" },
  rate_change: { view: "rates.view", decide: "rates.view" },
  migration_totals: { view: "migration.view", decide: "migration.sign_off" },
  migration_sign_off: { view: "migration.view", decide: "migration.sign_off" },
  // The console has no `client_phones.change` yet, so the sample stands in with the client permissions.
  phone_change: { view: "clients.view", decide: "clients.lock" },
  team_change: { view: "team.view", decide: "team.change" },
  client_lock: { view: "clients.view", decide: "clients.lock" },
  suppression_lift: { view: "clients.view", decide: "email_suppressions.lift" },
  complaint_outcome: { view: "complaints.view", decide: "complaints.close" },
  announcement: { view: "settings.view", decide: "announcements.send" },
  payout_account: { view: "review_queue.view", decide: "review_queue.view" },
  deposit_reassignment: { view: "reconciliation.view", decide: "deposit_reassignments.change" },
  tax_remittance: { view: "finance.view", decide: "tax_remittances.change" },
  payout_resolution: { view: "reconciliation.view", decide: "reconciliation.view" },
  payout_release: { view: "review_queue.view", decide: "payout_accounts.change" },
};

interface Reader {
  id: string;
  name: string;
  permissions: readonly Permission[];
}

export function createStubApprovalsApi(reader: Reader, now: () => number = Date.now, sample: "sample" | "empty" = "sample"): ApprovalsApi {
  const ago = (minutes: number) => new Date(now() - minutes * 60 * 1000).toISOString();
  const raw: [Kind, string, string, string, number][] = [
    ["client_lock", "-NstubLock0000000003", "lock -NstubClient00000042", "Grace Auma", 55],
    ["rate_change", "-NstubRate0000000003", "TB364", "Ruth Namata", 190],
    ["bank_transfer_match", "-NstubDeposit0000012", "-NstubDeposit0000012", "Daniel Kato", 35],
    ["team_change", "-NstubChange00000001", "change_role", "Grace Auma", 80],
    ["suppression_lift", "-NstubLift0000000001", "d356ebd92915", "Grace Auma", 120],
    ["complaint_outcome", "-NstubOutcome0000004", "C-1042", "Amina Kintu", 300],
    ["announcement", "-NstubAnnounce000002", "Christmas opening hours", reader.name, 25],
    ["dealing_order", "-NstubOrder000000021", "TB182", "Ruth Namata", 15],
    ["buffer_capital", "-NstubMove0000000002", "UGX paid in", "Daniel Okello", 45],
    ["buffer_rules", "-NstubRules000000001", "UGX from 2026-12-01", "Daniel Okello", 1190],
    ["day_close", "-NstubClose000000012", "2026-11-12", "Daniel Kato", 600],
    ["payout_account", "-NstubDest0000000088", "bank", "Peter Okello", 2900],
    ["payout_release", "-NstubRelease0000003", "-NstubPayout00000017", "Grace Auma", 70],
  ];
  const held = new Set(reader.permissions);
  const items: ApprovalItem[] =
    sample === "empty"
      ? []
      : raw
          .filter(([kind]) => held.has(SOURCES[kind].view))
          .map(([kind, id, subject, name, minutes]) => {
            const yours = name === reader.name;
            return {
              kind,
              id,
              subject,
              proposedBy: yours ? reader.id : `stub-${name.toLowerCase().replace(" ", "-")}`,
              proposedByName: name,
              proposedAt: ago(minutes),
              yours,
              canDecide: !yours && held.has(SOURCES[kind].decide),
            };
          })
          .sort((a, b) => a.proposedAt.localeCompare(b.proposedAt));
  return {
    async list() {
      await new Promise((resolve) => setTimeout(resolve, 150));
      return structuredClone(items);
    },
  };
}
