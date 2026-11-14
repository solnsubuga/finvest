import type { AutoinvestApiPlanView as PlanView, ProductOut, RunView } from "@finvest/api-client/client";

import { api, ApiError, isAccountClosing } from "@/api/http";

import { AutoinvestClient, AutoinvestError, Plan, PlanProduct, Run } from "./types";

/**
 * The automatic investing screens' contract, answered by the real API
 * (`/client/autoinvest/plans`, backend/app/modules/autoinvest). Amounts are whole
 * shillings and pass through untouched. Rates are the client's own, after tax.
 */

export function toRun(view: RunView): Run {
  return {
    id: view.run_id,
    dueOn: view.due_on,
    status: view.status,
    attempt: view.attempt === 2 ? 2 : 1,
    code: view.code,
  };
}

export function toPlan(view: PlanView): Plan {
  return {
    id: view.plan_id,
    product: { code: view.product_code, name: view.product_name },
    amount: { minor: view.amount_minor, currency: view.currency },
    frequency: view.frequency,
    day: view.day,
    network: view.network,
    payer: view.payer_msisdn,
    // An open-term product has nothing to do at maturity. Plans list fixed terms only.
    atMaturity: view.at_maturity ?? "roll_over",
    status: view.status,
    nextRunOn: view.next_run_on,
    runs: view.runs.map(toRun),
  };
}

/**
 * The products a plan can invest in: live, in shillings (mobile money collects shillings
 * only), and with a fixed term, because the plan asks what happens at maturity.
 */
export function toPlanProducts(products: ProductOut[]): PlanProduct[] {
  return products
    .filter((product) => product.status === "live" && product.currency === "UGX" && product.term_days !== null)
    .map((product) => ({
      code: product.code,
      name: product.name,
      minimum: { minor: product.minimum_minor, currency: "UGX" },
      ratePercent: product.client_rate_percent,
      termDays: product.term_days as number,
    }));
}

type Call = "create" | "other";

/** Turns what the API or the network said into a reason a plan screen can show. */
export function toAutoinvestError(error: unknown, call: Call = "other"): AutoinvestError {
  if (error instanceof AutoinvestError) return error;
  if (!(error instanceof ApiError)) return new AutoinvestError("unavailable");
  switch (error.code) {
    case "not_found":
      return new AutoinvestError("not_found");
    case "idempotency_key_reused":
      return new AutoinvestError("key_reused");
    case "forbidden":
      // Nobody invests before their identity check is approved.
      return new AutoinvestError("not_verified");
    case "conflict":
      if (isAccountClosing(error)) return new AutoinvestError("account_closing");
      // Creating: the most open plans at once. Anything else: the plan has stopped.
      return new AutoinvestError(call === "create" ? "too_many_plans" : "stopped");
    case "validation_failed": {
      const codes = error.fields.map((field) => field.code);
      if (codes.includes("below_minimum")) return new AutoinvestError("below_minimum");
      if (codes.includes("amount_too_large")) return new AutoinvestError("amount_too_large");
      // A definite refusal: nothing was saved, so it must not read as a lost connection.
      return new AutoinvestError("invalid");
    }
    default:
      return new AutoinvestError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>, call: Call = "other"): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toAutoinvestError(error, call);
  }
}

export function createApiAutoinvestClient(): AutoinvestClient {
  const pause = "POST /client/autoinvest/plans/{plan_id}/pause" as const;
  const resume = "POST /client/autoinvest/plans/{plan_id}/resume" as const;
  const stop = "POST /client/autoinvest/plans/{plan_id}/stop" as const;
  const act = (route: typeof pause | typeof resume | typeof stop, planId: string) =>
    wrap(async () => toPlan(await api(route, { params: { plan_id: planId } })));

  return {
    listProducts: () => wrap(async () => toPlanProducts(await api("GET /client/products", {}))),

    listPlans: () =>
      // Stopped plans are kept as records but not listed, as the screen expects.
      wrap(async () => (await api("GET /client/autoinvest/plans", {})).plans.map(toPlan)),

    getPlan: (planId) =>
      wrap(async () => toPlan(await api("GET /client/autoinvest/plans/{plan_id}", { params: { plan_id: planId } }))),

    createPlan: (plan) =>
      wrap(
        async () =>
          toPlan(
            await api("POST /client/autoinvest/plans", {
              // The number is the account's own. The API takes it from the session.
              body: {
                product_code: plan.productCode,
                amount_minor: plan.amount.minor,
                currency: plan.amount.currency,
                frequency: plan.frequency,
                day: plan.day,
                network: plan.network,
                at_maturity: plan.atMaturity,
              },
              idempotencyKey: plan.idempotencyKey,
            }),
          ),
        "create",
      ),

    pausePlan: (planId) => act(pause, planId),
    resumePlan: (planId) => act(resume, planId),
    stopPlan: (planId) => act(stop, planId),
  };
}
