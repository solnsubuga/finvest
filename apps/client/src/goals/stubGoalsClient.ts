import { demoClosing } from "@/demo/closing";
import { addDemoPayable } from "@/payments/stubPaymentsClient";
import { Money } from "@/payments/types";

import { addMonths, daysBetween } from "./dates";
import { bestPlan, DEMO_PRODUCTS, DemoProduct, netInterest } from "./plan";
import {
  Goal,
  GoalDetails,
  GoalError,
  GoalsClient,
  Progress,
  Saving,
  SavingStatus,
  Standing,
} from "./types";

/**
 * An in-memory stand-in for the goals API, so the goal screens can be built
 * and tried before the client routes are wired in. Nothing is real and
 * everything is forgotten when the app reloads. `resolveGoals.ts` keeps it out
 * of production builds.
 *
 * The demo account with a passkey starts with the two goals on the design
 * canvas. Any other account starts with none. A saving made here becomes
 * something the demo pay screens can pay for, and it counts as running as soon
 * as it has been confirmed, where the real one waits for the payment.
 */

type StoredSaving = {
  investmentId: string;
  product: DemoProduct;
  amount: number;
  createdOn: string;
  /** Null until paid. In the demo a saving is paid the moment it is confirmed. */
  startDate: string | null;
};

type StoredGoal = {
  id: string;
  details: GoalDetails;
  productCode: string;
  createdOn: string;
  createdAt: string;
  status: "active" | "closed";
  savings: StoredSaving[];
};

type StubOptions = {
  delayMs?: number;
  now?: () => number;
  /** Start with the canvas's two goals. */
  seeded?: boolean;
};

const MAX_ACTIVE = 20;
const KAMPALA_MS = 3 * 60 * 60 * 1000;
const ugx = (minor: number): Money => ({ currency: "UGX", minor });

function kampalaDay(ms: number): string {
  return new Date(ms + KAMPALA_MS).toISOString().slice(0, 10);
}

function product(code: string): DemoProduct {
  return DEMO_PRODUCTS.find((p) => p.code === code) ?? (DEMO_PRODUCTS[0] as DemoProduct);
}

export function createStubGoalsClient(options: StubOptions = {}): GoalsClient {
  const delayMs = options.delayMs ?? 400;
  const now = options.now ?? Date.now;
  const goals = new Map<string, StoredGoal>();
  const byKey = new Map<string, { id: string; body: string }>();
  let counter = 0;

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();
  const today = () => kampalaDay(now());

  function nextId(prefix: string): string {
    counter += 1;
    return `demo-${prefix}-${counter}`;
  }

  function valueOf(saving: StoredSaving): { value: number; interest: number } {
    if (!saving.startDate) {
      return { value: 0, interest: 0 };
    }
    const days = Math.min(
      Math.max(daysBetween(saving.startDate, today()), 0),
      saving.product.termDays,
    );
    const interest = netInterest(saving.amount, saving.product, days);
    return { value: saving.amount + interest, interest };
  }

  function progress(goal: StoredGoal): Progress {
    let value = 0;
    let interest = 0;
    let waiting = 0;
    for (const saving of goal.savings) {
      if (saving.startDate) {
        const worth = valueOf(saving);
        value += worth.value;
        interest += worth.interest;
      } else {
        waiting += saving.amount;
      }
    }
    const target = goal.details.target.minor;
    const percent = Math.min(100, Math.floor((value * 100) / target));
    let standing: Standing;
    if (value >= target) {
      standing = "reached";
    } else if (!goal.details.targetDate) {
      standing = "no_date";
    } else {
      const span = Math.max(daysBetween(goal.createdOn, goal.details.targetDate), 1);
      const elapsed = Math.min(Math.max(daysBetween(goal.createdOn, today()), 0), span);
      standing = value * span >= target * elapsed ? "on_track" : "behind";
    }
    return { value: ugx(value), interest: ugx(interest), awaitingPayment: ugx(waiting), percent, standing };
  }

  function view(goal: StoredGoal): Goal {
    const standing = progress(goal);
    const needed =
      goal.details.target.minor - standing.value.minor - standing.awaitingPayment.minor;
    const date = goal.details.targetDate;
    const plan =
      goal.status === "active" && date && date > today() && needed > 0
        ? bestPlan(needed, today(), date, [product(goal.productCode)])
        : null;
    const savings: Saving[] = goal.savings
      .slice()
      .reverse()
      .map((saving) => {
        const status: SavingStatus = saving.startDate ? "active" : "awaiting_payment";
        return {
          investmentId: saving.investmentId,
          productName: saving.product.name,
          status,
          amount: ugx(saving.amount),
          value: ugx(valueOf(saving).value),
          payBy: new Date(now() + 72 * 3_600_000).toISOString(),
          startDate: saving.startDate,
          maturityDate: saving.startDate
            ? addDays(saving.startDate, saving.product.termDays)
            : null,
        };
      });
    return {
      id: goal.id,
      name: goal.details.name,
      category: goal.details.category,
      target: goal.details.target,
      targetDate: goal.details.targetDate,
      status: goal.status,
      productCode: goal.productCode,
      createdAt: goal.createdAt,
      progress: standing,
      plan,
      savings,
    };
  }

  function productFor(details: GoalDetails): string {
    if (!details.targetDate) {
      return "TREA-91";
    }
    const plan = bestPlan(details.target.minor, today(), details.targetDate);
    if (!plan) {
      throw new GoalError("sooner_than_every_term");
    }
    return plan.productCode;
  }

  function check(details: GoalDetails) {
    const name = details.name.trim().replace(/\s+/g, " ");
    if (!name || name.length > 60 || details.target.minor <= 0) {
      throw new GoalError("invalid");
    }
    if (details.targetDate && details.targetDate <= today()) {
      throw new GoalError("invalid");
    }
    return { ...details, name };
  }

  function own(goalId: string): StoredGoal {
    const goal = goals.get(goalId);
    if (!goal) {
      throw new GoalError("not_found");
    }
    return goal;
  }

  if (options.seeded) {
    const start = today();
    const school: StoredGoal = {
      id: nextId("goal"),
      details: {
        name: "School fees, Term 1",
        category: "school_fees",
        target: ugx(2_000_000),
        targetDate: addMonths(start, 5),
      },
      productCode: "TREA-91",
      createdOn: addMonths(start, -2),
      createdAt: new Date(now() - 61 * 86_400_000).toISOString(),
      status: "active",
      savings: [
        {
          investmentId: nextId("inv"),
          product: product("TREA-91"),
          amount: 1_360_000,
          createdOn: addMonths(start, -2),
          startDate: addMonths(start, -2),
        },
        {
          investmentId: nextId("inv"),
          product: product("TREA-91"),
          amount: 200_000,
          createdOn: addDays(start, -16),
          startDate: addDays(start, -16),
        },
      ],
    };
    const emergency: StoredGoal = {
      id: nextId("goal"),
      details: {
        name: "Emergency fund",
        category: "emergency",
        target: ugx(3_000_000),
        targetDate: null,
      },
      productCode: "TREA-91",
      createdOn: addMonths(start, -1),
      createdAt: new Date(now() - 30 * 86_400_000).toISOString(),
      status: "active",
      savings: [
        {
          investmentId: nextId("inv"),
          product: product("TREA-91"),
          amount: 1_000_000,
          createdOn: addMonths(start, -1),
          startDate: addMonths(start, -1),
        },
      ],
    };
    goals.set(school.id, school);
    goals.set(emergency.id, emergency);
  }

  return {
    async getPlan(target, targetDate) {
      await pause();
      if (target.minor <= 0 || targetDate <= today()) {
        throw new GoalError("invalid");
      }
      return bestPlan(target.minor, today(), targetDate);
    },

    async createGoal(details, idempotencyKey) {
      await pause();
      if (demoClosing()) throw new GoalError("account_closing");
      const body = JSON.stringify(details);
      const known = byKey.get(idempotencyKey);
      if (known) {
        if (known.body !== body) {
          throw new GoalError("key_reused");
        }
        return view(own(known.id));
      }
      const cleaned = check(details);
      const open = [...goals.values()].filter((goal) => goal.status === "active").length;
      if (open >= MAX_ACTIVE) {
        throw new GoalError("too_many_goals");
      }
      const goal: StoredGoal = {
        id: nextId("goal"),
        details: cleaned,
        productCode: productFor(cleaned),
        createdOn: today(),
        createdAt: new Date(now()).toISOString(),
        status: "active",
        savings: [],
      };
      goals.set(goal.id, goal);
      byKey.set(idempotencyKey, { id: goal.id, body });
      return view(goal);
    },

    async listGoals() {
      await pause();
      return [...goals.values()]
        .sort((a, b) =>
          a.status === b.status ? b.createdAt.localeCompare(a.createdAt) : a.status === "active" ? -1 : 1,
        )
        .map(view);
    },

    async getGoal(goalId) {
      await pause();
      return view(own(goalId));
    },

    async updateGoal(goalId, details) {
      await pause();
      const goal = own(goalId);
      if (goal.status !== "active") {
        throw new GoalError("closed");
      }
      if (details.target.currency !== goal.details.target.currency) {
        throw new GoalError("invalid");
      }
      const cleaned = check(details);
      const moved =
        cleaned.target.minor !== goal.details.target.minor || cleaned.targetDate !== goal.details.targetDate;
      const productCode = moved ? productFor(cleaned) : goal.productCode;
      goal.details = cleaned;
      goal.productCode = productCode;
      return view(goal);
    },

    async closeGoal(goalId) {
      await pause();
      const goal = own(goalId);
      goal.status = "closed";
      return view(goal);
    },

    async saveToGoal(goalId, amount, idempotencyKey) {
      await pause();
      const goal = own(goalId);
      const body = `${goalId}:${amount.minor}`;
      const known = byKey.get(idempotencyKey);
      if (known) {
        if (known.body !== body) {
          throw new GoalError("key_reused");
        }
        const found = goal.savings.find((s) => s.investmentId === known.id);
        if (found) {
          return view(goal).savings.find((s) => s.investmentId === found.investmentId) as Saving;
        }
      }
      if (goal.status !== "active") {
        throw new GoalError("closed");
      }
      const chosen = product(goal.productCode);
      if (amount.minor < chosen.minimum) {
        throw new GoalError("below_minimum");
      }
      const saving: StoredSaving = {
        investmentId: nextId("inv"),
        product: chosen,
        amount: amount.minor,
        createdOn: today(),
        startDate: null,
      };
      goal.savings.push(saving);
      byKey.set(idempotencyKey, { id: saving.investmentId, body });
      addDemoPayable({
        id: saving.investmentId,
        kind: "investment",
        title: `${goal.details.name}: ${chosen.name}`,
        amount,
      });
      return view(goal).savings[0] as Saving;
    },
  };
}

function addDays(day: string, days: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + days)).toISOString().slice(0, 10);
}
