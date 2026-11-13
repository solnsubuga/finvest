import { firstRun, kampalaToday, MAX_RUN_MINOR } from "./schedule";
import { AutoinvestClient, AutoinvestError, NewPlan, Plan, PlanProduct } from "./types";

/**
 * An in-memory stand-in for the automatic investing API, so the screens can be
 * built and tested before the backend routes exist. Nothing here is real: no
 * network, no money, and everything is forgotten when the app reloads.
 * `resolveAutoinvest.ts` keeps it out of production builds.
 *
 * Each signed-in number has its own plans. The two demo accounts start with
 * one plan, with a paid run and a missed one, so its screens have something
 * to show. An account made by signing up starts with none. A number ending in
 * 99 cannot reach the service, to show the error state.
 */

export const STUB_PRODUCTS: PlanProduct[] = [
  {
    code: "TREA-91",
    name: "91 Day Treasury Bill",
    minimum: { currency: "UGX", minor: 100_000 },
    ratePercent: "10.63",
    termDays: 91,
  },
  {
    code: "TREA-182",
    name: "182 Day Treasury Bill",
    minimum: { currency: "UGX", minor: 200_000 },
    ratePercent: "11.69",
    termDays: 182,
  },
];

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const MAX_OPEN_PLANS = 10;

function seededPlan(phone: string, today: string): Plan {
  return {
    id: `demo-plan-${phone.slice(-3)}`,
    product: { code: "TREA-91", name: "91 Day Treasury Bill" },
    amount: { currency: "UGX", minor: 250_000 },
    frequency: "monthly",
    day: 28,
    network: "mtn",
    payer: phone,
    atMaturity: "roll_over",
    status: "active",
    nextRunOn: firstRun("monthly", 28, today),
    runs: [
      {
        id: "demo-run-2",
        dueOn: "2026-10-28",
        status: "paid",
        attempt: 1,
        code: null,
      },
      {
        id: "demo-run-1",
        dueOn: "2026-09-28",
        status: "missed",
        attempt: 1,
        code: "late",
      },
      {
        id: "demo-run-0",
        dueOn: "2026-08-28",
        status: "paid",
        attempt: 2,
        code: null,
      },
    ],
  };
}

export type StubStore = {
  plansByPhone: Map<string, Plan[]>;
  createdByKey: Map<string, string>;
  nextId: number;
};

export function newStubStore(): StubStore {
  return { plansByPhone: new Map(), createdByKey: new Map(), nextId: 1 };
}

// One store for the whole demo session, so plans survive signing out and back in until reload.
const sessionStore = newStubStore();

export function createStubAutoinvestClient(
  phone: string | null,
  store: StubStore = sessionStore,
  now: () => Date = () => new Date(),
  delayMs = 150,
): AutoinvestClient {
  const { plansByPhone, createdByKey } = store;

  const plansFor = (): Plan[] => {
    if (!phone || phone.endsWith("99")) {
      throw new AutoinvestError("unavailable");
    }
    let plans = plansByPhone.get(phone);
    if (!plans) {
      plans = SEEDED.has(phone) ? [seededPlan(phone, kampalaToday(now()))] : [];
      plansByPhone.set(phone, plans);
    }
    return plans;
  };

  const find = (planId: string): Plan => {
    const plan = plansFor().find((candidate) => candidate.id === planId);
    if (!plan) {
      throw new AutoinvestError("not_found");
    }
    return plan;
  };

  const replace = (planId: string, change: Partial<Plan>): Plan => {
    const plans = plansFor();
    const index = plans.findIndex((candidate) => candidate.id === planId);
    if (index < 0) {
      throw new AutoinvestError("not_found");
    }
    const changed: Plan = { ...(plans[index] as Plan), ...change };
    plans[index] = changed;
    return changed;
  };

  // No timer at all when there is no delay, so tests under fake timers are not held up.
  const settle = <T>(value: T): Promise<T> =>
    delayMs > 0
      ? new Promise((resolve) => setTimeout(() => resolve(value), delayMs))
      : Promise.resolve(value);

  return {
    async listProducts() {
      return settle(STUB_PRODUCTS);
    },
    async listPlans() {
      return settle(plansFor().filter((plan) => plan.status !== "stopped"));
    },
    async getPlan(planId) {
      return settle(find(planId));
    },
    async createPlan(request: NewPlan) {
      const plans = plansFor();
      const existing = createdByKey.get(request.idempotencyKey);
      if (existing) {
        return settle(find(existing));
      }
      const product = STUB_PRODUCTS.find((candidate) => candidate.code === request.productCode);
      if (!product) {
        throw new AutoinvestError("not_found");
      }
      if (request.amount.minor > MAX_RUN_MINOR) {
        throw new AutoinvestError("amount_too_large");
      }
      if (request.amount.minor < product.minimum.minor) {
        throw new AutoinvestError("below_minimum");
      }
      if (plans.filter((plan) => plan.status !== "stopped").length >= MAX_OPEN_PLANS) {
        throw new AutoinvestError("too_many_plans");
      }
      const plan: Plan = {
        id: `stub-plan-${store.nextId++}`,
        product: { code: product.code, name: product.name },
        amount: request.amount,
        frequency: request.frequency,
        day: request.day,
        network: request.network,
        payer: request.payer,
        atMaturity: request.atMaturity,
        status: "active",
        nextRunOn: firstRun(request.frequency, request.day, kampalaToday(now())),
        runs: [],
      };
      plans.unshift(plan);
      createdByKey.set(request.idempotencyKey, plan.id);
      return settle(plan);
    },
    async pausePlan(planId) {
      return settle(replace(planId, { status: "paused", nextRunOn: null }));
    },
    async resumePlan(planId) {
      const plan = find(planId);
      return settle(
        replace(planId, {
          status: "active",
          nextRunOn: firstRun(plan.frequency, plan.day, kampalaToday(now())),
        }),
      );
    },
    async stopPlan(planId) {
      return settle(replace(planId, { status: "stopped", nextRunOn: null }));
    },
  };
}
