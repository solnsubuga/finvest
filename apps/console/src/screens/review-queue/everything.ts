/**
 * The whole review queue in one list: everything waiting for a first person to look at it,
 * across deposits, identity checks, payout accounts, withdrawals, transfers and company
 * accounts, longest waiting first (docs/review-queue.md, `GET /staff/review-queue`).
 *
 * Each item is decided where it lives: this list says what is waiting and opens the screen or
 * tab that decides it. Changes waiting for a second person are in the Overview's approvals inbox.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "./api.ts";

export type QueueKind =
  | "bank_transfer"
  | "identity_check"
  | "payout_account"
  | "held_payout"
  | "payout_held_up"
  | "payout_unanswered"
  | "investment_transfer"
  | "organisation";

export type QueueGroup = "deposits" | "identity" | "accounts" | "withdrawals" | "transfers";

export interface QueueItem {
  kind: QueueKind;
  group: QueueGroup;
  id: string;
  clientId: string | null;
  amount: Amount | null;
  /** A code saying why it waits, such as `above_review_limit`. */
  reason: string;
  waitingSince: string;
  /** Who made the first step, when the item has one. */
  proposedBy: string | null;
  /** True when this person made that first step. */
  yours: boolean;
}

export interface WholeQueue {
  items: QueueItem[];
  /** The kinds this person may see, so the screen offers only those filters. */
  kinds: QueueKind[];
  waiting: number;
  /** Mobile money deposits confirmed and payouts paid today with no person needed. */
  clearedAutomaticallyToday: number;
  oldestWaitingSince: string | null;
}

export interface WholeQueueApi {
  load(): Promise<WholeQueue>;
}

interface ItemJson {
  kind: QueueKind;
  group: QueueGroup;
  id: string;
  client_id: string | null;
  amount: Amount | null;
  reason: string;
  waiting_since: string;
  proposed_by: string | null;
  yours: boolean;
}

interface QueueJson {
  items: ItemJson[];
  kinds: QueueKind[];
  waiting: number;
  cleared_automatically_today: number;
  oldest_waiting_since: string | null;
}

export class WholeQueueError extends Error {
  constructor(readonly code: "forbidden" | "not_connected" | "unavailable") {
    super(code);
    this.name = "WholeQueueError";
  }
}

export function wholeQueueMessage(error: unknown): string {
  if (error instanceof WholeQueueError && error.code === "forbidden") {
    return "Your role does not include the review queue.";
  }
  if (error instanceof WholeQueueError && error.code === "not_connected") {
    return "The console is not connected to Finvest, so the queue cannot be shown.";
  }
  return "Finvest could not load the queue just now. Try again in a moment.";
}

export function createHttpWholeQueueApi(http: Http): WholeQueueApi {
  return {
    async load() {
      try {
        const body = await http.request<QueueJson>("GET", "/staff/review-queue");
        return {
          items: body.items.map((item) => ({
            kind: item.kind,
            group: item.group,
            id: item.id,
            clientId: item.client_id,
            amount: item.amount,
            reason: item.reason,
            waitingSince: item.waiting_since,
            proposedBy: item.proposed_by,
            yours: item.yours,
          })),
          kinds: body.kinds,
          waiting: body.waiting,
          clearedAutomaticallyToday: body.cleared_automatically_today,
          oldestWaitingSince: body.oldest_waiting_since,
        };
      } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          throw new WholeQueueError("forbidden");
        }
        throw new WholeQueueError("unavailable");
      }
    },
  };
}

export const notConnectedWholeQueueApi: WholeQueueApi = {
  load: () => Promise.reject(new WholeQueueError("not_connected")),
};

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

const ALL_KINDS: QueueKind[] = [
  "bank_transfer",
  "identity_check",
  "payout_account",
  "held_payout",
  "payout_held_up",
  "payout_unanswered",
  "investment_transfer",
  "organisation",
];

/** A sample queue for `pnpm dev` and previews. Not real people or money. */
export function createStubWholeQueueApi(
  me: string,
  clock: () => number = Date.now,
  sample: "sample" | "empty" | "error" = "sample",
): WholeQueueApi {
  return {
    load() {
      if (sample === "error") return Promise.reject(new WholeQueueError("unavailable"));
      const now = clock();
      const at = (ago: number) => new Date(now - ago).toISOString();
      const items: QueueItem[] =
        sample === "empty"
          ? []
          : [
              {
                kind: "payout_unanswered",
                group: "withdrawals",
                id: "sample-payout-unanswered",
                clientId: "sample-client-brian",
                amount: { minor: 850_000, currency: "UGX" },
                reason: "no_answer",
                waitingSince: at(26 * HOUR),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "bank_transfer",
                group: "deposits",
                id: "sample-deposit-sarah",
                clientId: "sample-client-sarah",
                amount: { minor: 2_000_000, currency: "UGX" },
                reason: "find_on_statement",
                waitingSince: at(3 * HOUR + 10 * MINUTE),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "identity_check",
                group: "identity",
                id: "sample-check-amara",
                clientId: "sample-client-amara",
                amount: null,
                reason: "needs_review",
                waitingSince: at(2 * HOUR + 40 * MINUTE),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "held_payout",
                group: "withdrawals",
                id: "sample-payout-joan",
                clientId: "sample-client-joan",
                amount: { minor: 12_000_000, currency: "UGX" },
                reason: "above_review_limit",
                waitingSince: at(95 * MINUTE),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "payout_account",
                group: "accounts",
                id: "sample-account-peter",
                clientId: "sample-client-peter",
                amount: null,
                reason: "bank_statement",
                waitingSince: at(70 * MINUTE),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "payout_held_up",
                group: "withdrawals",
                id: "sample-payout-kira",
                clientId: "sample-client-kira",
                amount: { minor: 400_000, currency: "UGX" },
                reason: "wallet_short",
                waitingSince: at(45 * MINUTE),
                proposedBy: null,
                yours: false,
              },
              {
                kind: "investment_transfer",
                group: "transfers",
                id: "sample-transfer-paul",
                clientId: "sample-client-paul",
                amount: { minor: 25_000_000, currency: "UGX" },
                reason: "size",
                waitingSince: at(30 * MINUTE),
                proposedBy: me,
                yours: true,
              },
              {
                kind: "organisation",
                group: "accounts",
                id: "sample-organisation-savings",
                clientId: null,
                amount: null,
                reason: "savings_group",
                waitingSince: at(12 * MINUTE),
                proposedBy: null,
                yours: false,
              },
            ];
      return Promise.resolve({
        items,
        kinds: ALL_KINDS,
        waiting: items.length,
        clearedAutomaticallyToday: sample === "empty" ? 0 : 214,
        oldestWaitingSince: items[0]?.waitingSince ?? null,
      });
    },
  };
}
