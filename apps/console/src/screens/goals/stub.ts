import { addDays } from "@/screens/automatic-investing/labels.ts";
import { GoalsError, type Goal, type GoalDetail, type GoalsApi, type GoalStatus, type Progress, type Saving } from "./api.ts";

/**
 * Sample goals for `pnpm dev` and previews. Not real clients and not connected
 * to Finvest. Days are counted from the day asked for, so the sample stays recent.
 */

interface Sample {
  goal: Omit<Goal, "id" | "createdOn" | "closedOn" | "targetDate"> & { createdDaysAgo: number; closedDaysAgo?: number; dueInDays: number | null };
  progress: Progress;
  savings: readonly (Omit<Saving, "startDate" | "maturityDate"> & { startedDaysAgo: number | null; termDays: number })[];
}

const SAMPLES: readonly Sample[] = [
  {
    goal: { clientId: "client-1", clientName: "Sarah Namubiru", accountNumber: "1004 2381 76", name: "School fees, Term 1", category: "school_fees", currency: "UGX", target: "2000000", status: "active", productName: "91 Day Treasury Bill", createdDaysAgo: 61, dueInDays: 151 },
    progress: { value: "1585080", interest: "25080", awaitingPayment: "0", percent: 79, standing: "on_track" },
    savings: [
      { investmentId: "inv-1", productName: "91 Day Treasury Bill", status: "active", amount: "200000", accruedNet: "931", startedDaysAgo: 16, termDays: 91, rolledFrom: null },
      { investmentId: "inv-2", productName: "91 Day Treasury Bill", status: "active", amount: "1360000", accruedNet: "24149", startedDaysAgo: 61, termDays: 91, rolledFrom: null },
    ],
  },
  {
    goal: { clientId: "client-2", clientName: "Brian Okello", accountNumber: "1007 5520 13", name: "Plot in Wakiso", category: "land_or_home", currency: "UGX", target: "20000000", status: "active", productName: "91 Day Treasury Bill", createdDaysAgo: 200, dueInDays: 530 },
    progress: { value: "3120400", interest: "120400", awaitingPayment: "819000", percent: 15, standing: "behind" },
    savings: [
      { investmentId: "inv-3", productName: "91 Day Treasury Bill", status: "awaiting_payment", amount: "819000", accruedNet: "0", startedDaysAgo: null, termDays: 91, rolledFrom: null },
      { investmentId: "inv-4", productName: "91 Day Treasury Bill", status: "active", amount: "3053200", accruedNet: "67200", startedDaysAgo: 18, termDays: 91, rolledFrom: "inv-5" },
      { investmentId: "inv-5", productName: "91 Day Treasury Bill", status: "matured", amount: "3000000", accruedNet: "53200", startedDaysAgo: 109, termDays: 91, rolledFrom: null },
    ],
  },
  {
    goal: { clientId: "client-3", clientName: "Grace Atim", accountNumber: "1002 9917 40", name: "Emergency fund", category: "emergency", currency: "UGX", target: "3000000", status: "active", productName: "91 Day Treasury Bill", createdDaysAgo: 30, dueInDays: null },
    progress: { value: "1009024", interest: "9024", awaitingPayment: "0", percent: 33, standing: "no_date" },
    savings: [{ investmentId: "inv-6", productName: "91 Day Treasury Bill", status: "active", amount: "1000000", accruedNet: "9024", startedDaysAgo: 30, termDays: 91, rolledFrom: null }],
  },
  {
    goal: { clientId: "client-4", clientName: "Peter Mugisha", accountNumber: "1005 7741 39", name: "Wedding in August", category: "wedding", currency: "UGX", target: "5000000", status: "closed", productName: "182 Day Treasury Bill", createdDaysAgo: 120, closedDaysAgo: 8, dueInDays: 270 },
    progress: { value: "0", interest: "0", awaitingPayment: "0", percent: 0, standing: "behind" },
    savings: [],
  },
];

function build(sample: Sample, index: number, today: string): GoalDetail {
  const { createdDaysAgo, closedDaysAgo, dueInDays, ...goal } = sample.goal;
  return {
    goal: {
      ...goal,
      id: `sample-goal-${index + 1}`,
      createdOn: addDays(today, -createdDaysAgo),
      closedOn: closedDaysAgo === undefined ? null : addDays(today, -closedDaysAgo),
      targetDate: dueInDays === null ? null : addDays(today, dueInDays),
    },
    progress: sample.progress,
    savings: sample.savings.map(({ startedDaysAgo, termDays, ...saving }) => ({
      ...saving,
      startDate: startedDaysAgo === null ? null : addDays(today, -startedDaysAgo),
      maturityDate: startedDaysAgo === null ? null : addDays(today, termDays - startedDaysAgo),
    })),
  };
}

export function sampleGoals(today: string): readonly GoalDetail[] {
  return SAMPLES.map((sample, index) => build(sample, index, today)).sort((a, b) => b.goal.createdOn.localeCompare(a.goal.createdOn));
}

export type StubCase = "sample" | "empty" | "error";

export function createStubGoalsApi(today: string, delayMs = 150, which: StubCase = "sample"): GoalsApi {
  const settle = <T,>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  const all = () => {
    if (which === "error") throw new GoalsError("unavailable");
    return which === "empty" ? [] : sampleGoals(today);
  };
  return {
    listGoals: (status: GoalStatus | null) =>
      settle(() => all().map((detail) => detail.goal).filter((goal) => status === null || goal.status === status)),
    getGoal: (goalId) =>
      settle(() => {
        const found = all().find((detail) => detail.goal.id === goalId);
        if (!found) throw new GoalsError("not_found");
        return found;
      }),
  };
}
