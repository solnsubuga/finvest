/**
 * The console's view of goals.
 *
 * `http.ts` calls the staff routes in backend/app/modules/goals/api.py
 * (GET /staff/goals and GET /staff/goals/{id}, see docs/goals.md); `stub.ts`
 * stands in for them under `pnpm dev` and previews.
 *
 * Amounts travel as whole-number strings in the smallest unit of the goal's
 * currency (shillings, or cents for a dollar goal), so nothing here is ever a
 * floating point number. The screen only reads: a goal holds no money, and only
 * its client changes or closes it.
 */

import type { Currency } from "@/screens/products/api.ts";

export type { Currency } from "@/screens/products/api.ts";

export type GoalStatus = "active" | "closed";
export type GoalCategory = "school_fees" | "emergency" | "land_or_home" | "business" | "wedding" | "retirement" | "other";
export type Standing = "reached" | "on_track" | "behind" | "no_date";
export type SavingStatus = "awaiting_payment" | "active" | "matured" | "withdrawn" | "unwound" | "transferred" | "cancelled";

export interface Goal {
  id: string;
  clientId: string;
  /** The verified name, or a placeholder when the client's card cannot be read. */
  clientName: string;
  /** Null until the client's identity check has passed, or when their card cannot be read. */
  accountNumber: string | null;
  name: string;
  category: GoalCategory;
  /** Every amount of the goal and its savings is in this currency. */
  currency: Currency;
  /** The smallest unit of the currency, as a whole-number string. */
  target: string;
  /** YYYY-MM-DD, or null for a goal with no date such as an emergency fund. */
  targetDate: string | null;
  status: GoalStatus;
  /** The product's name, or its code when the name cannot be read. */
  productName: string;
  /** YYYY-MM-DD in Kampala. */
  createdOn: string;
  closedOn: string | null;
}

export interface Saving {
  investmentId: string;
  productName: string;
  status: SavingStatus;
  amount: string;
  /** Interest after tax added so far. */
  accruedNet: string;
  startDate: string | null;
  maturityDate: string | null;
  /** The saving this one rolled over from, when it did. Not sent to the console yet, so null there. */
  rolledFrom: string | null;
}

export interface Progress {
  /** What the goal's running investments are worth today, after tax. */
  value: string;
  interest: string;
  awaitingPayment: string;
  percent: number;
  standing: Standing;
}

export interface GoalDetail {
  goal: Goal;
  progress: Progress;
  savings: readonly Saving[];
}

export interface GoalsApi {
  /** Goals newest first, open or closed or both. */
  listGoals(status: GoalStatus | null): Promise<readonly Goal[]>;
  getGoal(goalId: string): Promise<GoalDetail>;
}

export class GoalsError extends Error {
  constructor(readonly code: "not_connected" | "unavailable" | "forbidden" | "not_found") {
    super(code);
    this.name = "GoalsError";
  }
}

export function goalsErrorMessage(error: unknown): string {
  if (error instanceof GoalsError) {
    switch (error.code) {
      case "not_connected":
        return "The console is not connected to Finvest yet.";
      case "forbidden":
        return "Your role cannot see goals.";
      case "not_found":
        return "This goal could not be found.";
      case "unavailable":
        break;
    }
  }
  return "We could not reach Finvest. Try again in a moment.";
}

/** Until the API client exists, a normal build has nothing to show. */
export const notConnectedGoalsApi: GoalsApi = {
  listGoals: () => Promise.reject(new GoalsError("not_connected")),
  getGoal: () => Promise.reject(new GoalsError("not_connected")),
};
