import { AutoinvestError, type AutoinvestApi, type Overview, type Run } from "./api.ts";
import { addDays } from "./labels.ts";

/**
 * Sample plans and runs for `pnpm dev` and previews. Not real clients and not
 * connected to Finvest. Days are counted back from the day asked for, so the
 * screen always shows a recent week.
 */

type Sample = Omit<Run, "id" | "dueOn" | "retryOn"> & { daysAgo: number; retryAfter?: number };

const SAMPLES: readonly Sample[] = [
  { daysAgo: 0, planId: "plan-1", status: "requested", attempt: 1, code: null, clientName: "Sarah Namubiru", accountNumber: "1004 2381 76", productName: "91 Day Treasury Bill", amount: "250000", frequency: "weekly", day: 5, network: "mtn" },
  { daysAgo: 0, planId: "plan-2", status: "due", attempt: 1, code: null, clientName: "Brian Okello", accountNumber: "1007 5520 13", productName: "182 Day Treasury Bill", amount: "500000", frequency: "monthly", day: 13, network: "airtel" },
  { daysAgo: 1, planId: "plan-3", status: "paid", attempt: 2, code: null, clientName: "Grace Atim", accountNumber: "1002 9917 40", productName: "91 Day Treasury Bill", amount: "100000", frequency: "monthly", day: 12, network: "mtn" },
  { daysAgo: 0, planId: "plan-7", status: "failed", attempt: 1, code: "insufficient_funds", retryAfter: 1, clientName: "Esther Akello", accountNumber: "1008 3305 21", productName: "182 Day Treasury Bill", amount: "200000", frequency: "monthly", day: 13, network: "airtel" },
  { daysAgo: 2, planId: "plan-4", status: "paid", attempt: 1, code: null, clientName: "Daniel Ssemwogerere", accountNumber: "1009 0042 68", productName: "91 Day Treasury Bill", amount: "1000000", frequency: "weekly", day: 3, network: "mtn" },
  { daysAgo: 3, planId: "plan-5", status: "missed", attempt: 1, code: "late", clientName: "Ruth Nambi", accountNumber: "1003 6634 05", productName: "182 Day Treasury Bill", amount: "300000", frequency: "weekly", day: 2, network: "airtel" },
  { daysAgo: 5, planId: "plan-6", status: "failed", attempt: 2, code: "approval_timed_out", clientName: "Joseph Kato", accountNumber: "1006 1178 92", productName: "91 Day Treasury Bill", amount: "150000", frequency: "monthly", day: 8, network: "mtn" },
  { daysAgo: 6, planId: "plan-8", status: "paid", attempt: 1, code: null, clientName: "Peter Mugisha", accountNumber: "1005 7741 39", productName: "91 Day Treasury Bill", amount: "400000", frequency: "weekly", day: 6, network: "mtn" },
];

export function sampleOverview(today: string): Overview {
  return {
    plans: { active: 42, paused: 5 },
    from: addDays(today, -6),
    to: today,
    runs: [...SAMPLES].sort((a, b) => a.daysAgo - b.daysAgo).map(({ daysAgo, retryAfter, ...run }, index) => {
      const dueOn = addDays(today, -daysAgo);
      return {
        ...run,
        id: `sample-run-${index + 1}`,
        dueOn,
        retryOn: retryAfter === undefined ? null : addDays(dueOn, retryAfter),
      };
    }),
  };
}

export type StubCase = "sample" | "empty" | "error";

export function createStubAutoinvestApi(delayMs = 150, which: StubCase = "sample"): AutoinvestApi {
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
  return {
    getOverview: (today) =>
      settle(() => {
        if (which === "error") throw new AutoinvestError("unavailable");
        if (which === "empty") return { plans: { active: 0, paused: 0 }, from: addDays(today, -6), to: today, runs: [] };
        return sampleOverview(today);
      }),
  };
}
