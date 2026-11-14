/**
 * The Activity tab's contract: payments in, interest earned and payouts, in
 * one list, newest first. Every amount is what the client sees elsewhere:
 * after tax, with no rate.
 */

import { Money } from "@/payments/types";

export type ActivityKind = "payment" | "interest" | "payout";

/** Where a row stands, in words the client can act on. */
export type ActivityStatus = "done" | "under_way" | "failed";

export type ActivityItem = {
  /** Unique across kinds, for React keys. */
  key: string;
  kind: ActivityKind;
  /** When it happened, ISO 8601. Newest first is by this. */
  at: string;
  /** "Payment for 91 day Treasury bill", true whether it went through or not. */
  title: string;
  /** "MTN Mobile Money", "Bank transfer", "To your mobile money". */
  detail: string;
  amount: Money;
  /** Money that came to the client is "in"; money the client paid is "out". */
  direction: "in" | "out";
  status: ActivityStatus;
  /** "Paid", "Being checked", "Failed". */
  statusLabel: string;
  /** The investment this row belongs to, for its detail screen. Null for a payout. */
  investmentId: string | null;
};

export class ActivityError extends Error {
  constructor() {
    super("unavailable");
    this.name = "ActivityError";
  }
}

export type ActivityList = {
  /** Newest first. */
  items: ActivityItem[];
  /**
   * Null when the list holds everything. Otherwise the client has more
   * payments than the app reads, and the list holds every kind of row from
   * this time on and nothing older, so the screen can say where it starts.
   */
  since: string | null;
};

export interface ActivityClient {
  /** Fails with ActivityError when any part cannot be read. */
  list(): Promise<ActivityList>;
}
