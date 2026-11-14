import { Money } from "@/payments/types";

/**
 * Demo only. Stands in for the backend, where an investment's choice to save its interest to
 * a goal is read by the goals module. The investing stub writes here when the client picks a
 * goal, and the goals stub reads it to show the interest coming in.
 */
export type DemoInterestComing = { goalId: string; investmentId: string; productName: string; on: string; amount: Money };

export const demoInterestComing = new Map<string, DemoInterestComing>();

/** The seeded demo goals, by the IDs the goals stub gives them. */
export const DEMO_GOAL_NAMES: Record<string, string> = {
  "demo-goal-1": "School fees, Term 1",
  "demo-goal-5": "Emergency fund",
};
