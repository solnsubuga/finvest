/**
 * The console's view of goals.
 *
 * A stand-in for the generated client in packages/api-client, like AutoinvestApi.
 * The shapes follow the staff routes in backend/app/modules/goals/api.py
 * (GET /staff/goals and GET /staff/goals/{id}, see docs/goals.md). When the
 * console is connected, an implementation that calls them replaces the stub in
 * index.tsx and the screen does not change.
 *
 * Amounts travel as whole-number strings in shillings, so nothing here is ever a
 * floating point number. The screen only reads: a goal holds no money, and only
 * its client changes or closes it.
 */

export type GoalStatus = "active" | "closed";
export type GoalCategory = "school_fees" | "emergency" | "land_or_home" | "business" | "wedding" | "retirement" | "other";
export type Standing = "reached" | "on_track" | "behind" | "no_date";
export type SavingStatus = "awaiting_payment" | "active" | "matured" | "cancelled";

export interface Goal {
  id: string;
  clientId: string;
  clientName: string;
  accountNumber: string;
  name: string;
  category: GoalCategory;
  /** Shillings, as a whole-number string. */
  target: string;
  /** YYYY-MM-DD, or null for a goal with no date such as an emergency fund. */
  targetDate: string | null;
  status: GoalStatus;
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
  /** The saving this one rolled over from, when it did. */
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
