import type { GoalView, GoalsApiPlanView as PlanView, SavingView } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { Money } from "@/payments/types";

import { Goal, GoalError, GoalsClient, Plan, Saving } from "./types";

/**
 * The goal screens' contract, answered by the real API (`/client/goals`,
 * backend/app/modules/goals). Every figure arrives after tax, as whole
 * numbers of the smallest unit, and is passed through untouched.
 */

function money(minor: number, currency: Money["currency"]): Money {
  return { minor, currency };
}

export function toPlan(view: PlanView): Plan {
  return {
    productCode: view.product_code,
    productName: view.product_name,
    ratePercent: view.rate_percent,
    termDays: view.term_days,
    saving: money(view.saving_minor, view.currency),
    savings: view.savings,
    firstOn: view.first_on,
    lastOn: view.last_on,
    putIn: money(view.put_in_minor, view.currency),
    interest: money(view.interest_minor, view.currency),
    withoutInvesting: money(view.without_investing_minor, view.currency),
    raisedToMinimum: view.raised_to_minimum,
  };
}

export function toSaving(view: SavingView, currency: Money["currency"]): Saving {
  return {
    investmentId: view.investment_id,
    productName: view.product_name,
    status: view.status,
    amount: money(view.amount_minor, currency),
    value: money(view.value_minor, currency),
    payBy: view.pay_by,
    startDate: view.start_date,
    maturityDate: view.maturity_date,
  };
}

export function toGoal(view: GoalView): Goal {
  const currency = view.currency;
  return {
    id: view.goal_id,
    name: view.name,
    category: view.category,
    target: money(view.target_minor, currency),
    targetDate: view.target_date,
    status: view.status,
    productCode: view.product_code,
    createdAt: view.created_at,
    progress: {
      value: money(view.progress.value_minor, currency),
      interest: money(view.progress.interest_minor, currency),
      awaitingPayment: money(view.progress.awaiting_payment_minor, currency),
      percent: view.progress.percent,
      standing: view.progress.standing,
    },
    plan: view.plan ? toPlan(view.plan) : null,
    savings: view.savings.map((saving) => toSaving(saving, currency)),
  };
}

/** Which call failed, where the same refusal means different things. */
type Call = "create" | "save" | "other";

/** Turns what the API or the network said into a reason a goal screen can show. */
export function toGoalError(error: unknown, call: Call = "other"): GoalError {
  if (error instanceof GoalError) return error;
  if (!(error instanceof ApiError)) return new GoalError("unavailable");
  switch (error.code) {
    case "not_found":
      return new GoalError("not_found");
    case "idempotency_key_reused":
      return new GoalError("key_reused");
    case "forbidden":
      // Saving makes an investment, and nobody invests before their ID check is approved.
      // Anywhere else the app cannot say why, so it does not guess.
      return new GoalError(call === "save" ? "not_verified" : "not_allowed");
    case "conflict":
      // Creating: the most open goals at once. Anything else: the goal is closed.
      return new GoalError(call === "create" ? "too_many_goals" : "closed");
    case "validation_failed": {
      const codes = error.fields.map((field) => field.code);
      if (codes.includes("sooner_than_every_term")) return new GoalError("sooner_than_every_term");
      if (codes.includes("below_minimum")) return new GoalError("below_minimum");
      return new GoalError("invalid");
    }
    case "offline":
      return new GoalError("offline");
    default:
      return new GoalError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>, call: Call = "other"): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toGoalError(error, call);
  }
}

export function createApiGoalsClient(): GoalsClient {
  return {
    getPlan: (target, targetDate) =>
      wrap(async () => {
        const view = await api("GET /client/goals/plan", {
          query: { target_minor: target.minor, target_date: targetDate, currency: target.currency },
        });
        return view ? toPlan(view) : null;
      }),

    createGoal: (details, idempotencyKey) =>
      wrap(
        async () =>
          toGoal(
            await api("POST /client/goals", {
              body: {
                name: details.name,
                category: details.category,
                target_minor: details.target.minor,
                currency: details.target.currency,
                target_date: details.targetDate,
              },
              idempotencyKey,
            }),
          ),
        "create",
      ),

    listGoals: () => wrap(async () => (await api("GET /client/goals", {})).goals.map(toGoal)),

    getGoal: (goalId) => wrap(async () => toGoal(await api("GET /client/goals/{goal_id}", { params: { goal_id: goalId } }))),

    closeGoal: (goalId) =>
      wrap(async () => {
        try {
          return toGoal(await api("POST /client/goals/{goal_id}/close", { params: { goal_id: goalId } }));
        } catch (error) {
          // Closing moves no money, but a lost answer may still have closed it. Look before
          // saying it failed.
          if (!(error instanceof ApiError) || !error.uncertain) throw error;
          const goal = await api("GET /client/goals/{goal_id}", { params: { goal_id: goalId } }).catch(() => {
            throw error;
          });
          if (goal.status !== "active") return toGoal(goal);
          throw error;
        }
      }),

    saveToGoal: (goalId, amount, idempotencyKey) =>
      wrap(async () =>
        toSaving(
          await api("POST /client/goals/{goal_id}/savings", {
            params: { goal_id: goalId },
            body: { amount_minor: amount.minor, currency: amount.currency },
            idempotencyKey,
          }),
          amount.currency,
        ),
        "save",
      ),
  };
}
