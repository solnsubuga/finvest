/**
 * The contract between the goal screens and the goals API.
 *
 * It follows the backend's `/client/goals` routes (docs/goals.md). Today the
 * only implementation is the in-memory stub in `stubGoalsClient.ts`. When the
 * generated `packages/api-client` types exist, a client backed by them
 * replaces it and the screens do not change.
 *
 * A goal holds no money. Each saving is an investment of its own, paid for on
 * the pay screens. Every figure here is after tax, and no tax rate is ever sent.
 */

import { Money } from "@/payments/types";

export type GoalCategory =
  | "school_fees"
  | "emergency"
  | "land_or_home"
  | "business"
  | "wedding"
  | "retirement"
  | "other";

export type Standing = "reached" | "on_track" | "behind" | "no_date";

/** What to save each month to reach a target by its date. An estimate, never a promise. */
export type Plan = {
  productCode: string;
  /** "91 Day Treasury Bill" */
  productName: string;
  /** "10.63": the yearly rate after tax, as a label. */
  ratePercent: string;
  termDays: number;
  saving: Money;
  savings: number;
  /** YYYY-MM-DD */
  firstOn: string;
  lastOn: string;
  putIn: Money;
  interest: Money;
  withoutInvesting: Money;
  /** The target needs less than the product's minimum, so the plan saves the minimum. */
  raisedToMinimum: boolean;
};

export type Progress = {
  value: Money;
  interest: Money;
  awaitingPayment: Money;
  /** 0 to 100 */
  percent: number;
  standing: Standing;
};

export type SavingStatus = "awaiting_payment" | "active" | "cancelled";

/** One investment made for the goal. */
export type Saving = {
  investmentId: string;
  productName: string;
  status: SavingStatus;
  amount: Money;
  value: Money;
  /** ISO 8601, UTC. A saving awaiting payment must be paid by then. */
  payBy: string;
  startDate: string | null;
  maturityDate: string | null;
};

export type Goal = {
  id: string;
  name: string;
  category: GoalCategory;
  target: Money;
  /** YYYY-MM-DD, or null for a goal with no date. */
  targetDate: string | null;
  status: "active" | "closed";
  productCode: string;
  createdAt: string;
  progress: Progress;
  plan: Plan | null;
  savings: Saving[];
};

export type GoalDetails = {
  name: string;
  category: GoalCategory;
  target: Money;
  targetDate: string | null;
};

export type GoalErrorCode =
  | "not_found"
  | "too_many_goals"
  | "closed"
  | "sooner_than_every_term"
  | "below_minimum"
  | "invalid"
  | "unavailable";

export class GoalError extends Error {
  constructor(readonly code: GoalErrorCode) {
    super(code);
    this.name = "GoalError";
  }
}

export type GoalsClient = {
  /** The plan before a goal is saved. Null when every term ends after the date. */
  getPlan(target: Money, targetDate: string): Promise<Plan | null>;
  createGoal(details: GoalDetails, idempotencyKey: string): Promise<Goal>;
  listGoals(): Promise<Goal[]>;
  getGoal(goalId: string): Promise<Goal>;
  closeGoal(goalId: string): Promise<Goal>;
  /**
   * Confirm a saving: a new investment awaiting payment. Returns its ID, which
   * is what the pay screens pay for.
   */
  saveToGoal(goalId: string, amount: Money, idempotencyKey: string): Promise<Saving>;
};

const errorCopy: Record<GoalErrorCode, string> = {
  not_found: "We could not find this goal.",
  too_many_goals: "You have 20 open goals, which is the most at once. Close one to start another.",
  closed: "This goal is closed, so nothing more can be saved to it.",
  sooner_than_every_term:
    "That date is too soon. Your money is invested in Treasury bills, and the shortest one takes 91 days.",
  below_minimum: "Each saving must be at least the product's minimum.",
  invalid: "Something in this goal is not right. Check it and try again.",
  unavailable: "Goals are not available right now. Try again in a moment.",
};

export function describeGoalError(error: unknown): string {
  return error instanceof GoalError ? errorCopy[error.code] : errorCopy.unavailable;
}

export const categoryCopy: Record<GoalCategory, { name: string; hint: string; mark: string }> = {
  school_fees: { name: "School fees", hint: "Due each term", mark: "Sc" },
  emergency: { name: "Emergency fund", hint: "3 to 6 months of costs", mark: "Em" },
  land_or_home: { name: "Land or a home", hint: "Usually 1 to 5 years", mark: "La" },
  business: { name: "Business capital", hint: "Stock, tools, a shop", mark: "Bu" },
  wedding: { name: "Wedding or introduction", hint: "A date to hit", mark: "We" },
  retirement: { name: "Retirement", hint: "Long term", mark: "Re" },
  other: { name: "Something else", hint: "Name your own", mark: "+" },
};

export const CATEGORIES = Object.keys(categoryCopy) as GoalCategory[];

export const standingCopy: Record<Standing, string> = {
  reached: "Reached",
  on_track: "On track",
  behind: "Behind",
  no_date: "No date",
};
