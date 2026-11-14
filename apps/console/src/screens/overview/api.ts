/**
 * The approvals inbox: everything waiting for a second person that the reader may see.
 *
 * Deciding happens on each item's own screen, whose route checks its own permission and
 * refuses the person who proposed the item. The inbox only says what is waiting and where.
 */

import { ApiError, type Http } from "@/api/http.ts";

export const KINDS = [
  "wording_change",
  "ledger_correction",
  "pot_transfer",
  "bank_transfer_match",
  "day_close",
  "dealing_order",
  "dealing_result",
  "buffer_rules",
  "buffer_capital",
  "rate_change",
  "migration_totals",
  "migration_sign_off",
  "phone_change",
  "team_change",
  "client_lock",
  "account_closure",
  "suppression_lift",
  "complaint_outcome",
  "announcement",
  "payout_account",
  "deposit_reassignment",
  "tax_remittance",
  "payout_resolution",
  "payout_release",
  "bank_account_change",
  "organisation",
  "client_pin_reset",
  "client_limits",
  "client_statement",
  "client_withdrawal",
] as const;

export type Kind = (typeof KINDS)[number];

export interface ApprovalItem {
  /** Null for a kind this console does not know yet: it is still shown, without a link. */
  kind: Kind | null;
  id: string;
  /** A short label from the item, such as a product code, a date or a client ID. Never personal data. */
  subject: string;
  proposedBy: string;
  proposedByName: string | null;
  proposedAt: string;
  /** The reader proposed it, so they can only withdraw it. */
  yours: boolean;
  /** The reader may approve or reject it. */
  canDecide: boolean;
}

export interface ApprovalsApi {
  list(): Promise<ApprovalItem[]>;
}

export type ApprovalsErrorCode = "forbidden" | "not_connected" | "unavailable";

export class ApprovalsError extends Error {
  constructor(readonly code: ApprovalsErrorCode) {
    super(code);
    this.name = "ApprovalsError";
  }
}

export function approvalsErrorMessage(error: unknown): string {
  if (error instanceof ApprovalsError) {
    if (error.code === "forbidden") return "Your role cannot open the overview.";
    if (error.code === "not_connected") return "This console is not connected to Finvest yet.";
  }
  return "Finvest could not be reached. Check your connection and try again.";
}

export const notConnectedApprovalsApi: ApprovalsApi = {
  list: () => Promise.reject(new ApprovalsError("not_connected")),
};

interface ItemJson {
  kind: string;
  id: string;
  subject: string;
  proposed_by: string;
  proposed_by_name?: string | null;
  proposed_at: string;
  yours: boolean;
  can_decide: boolean;
}

const KNOWN = new Set<string>(KINDS);

export function toItem(json: ItemJson): ApprovalItem {
  return {
    kind: KNOWN.has(json.kind) ? (json.kind as Kind) : null,
    id: json.id,
    subject: json.subject,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name ?? null,
    proposedAt: json.proposed_at,
    yours: json.yours,
    canDecide: json.can_decide,
  };
}

export function createHttpApprovalsApi(http: Http): ApprovalsApi {
  return {
    async list() {
      try {
        const found = await http.request<{ items: ItemJson[] }>("GET", "/staff/approvals");
        return found.items.map(toItem);
      } catch (error) {
        if (error instanceof ApiError && error.code === "forbidden") throw new ApprovalsError("forbidden");
        throw new ApprovalsError("unavailable");
      }
    },
  };
}
