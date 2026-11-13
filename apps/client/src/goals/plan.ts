/**
 * The goal plan, worked out the way the backend works it out
 * (backend/app/modules/goals/domain/plans.py), for the demo only.
 *
 * The real app always shows the server's plan. This copy exists so the
 * in-memory demo shows believable figures. Integers throughout: amounts and
 * the products of rates go through BigInt, so nothing passes a fraction.
 */

import { Money } from "@/payments/types";

import { Plan } from "./types";

/** A demo product with its rate before tax, which only this arithmetic ever sees. */
export type DemoProduct = {
  code: string;
  name: string;
  termDays: number;
  minimum: number;
  annualRateBps: number;
  taxBps: number;
  ratePercent: string;
};

export const DEMO_PRODUCTS: DemoProduct[] = [
  {
    code: "TREA-91",
    name: "91 Day Treasury Bill",
    termDays: 91,
    minimum: 100_000,
    annualRateBps: 1250,
    taxBps: 1500,
    ratePercent: "10.63",
  },
  {
    code: "TREA-182",
    name: "182 Day Treasury Bill",
    termDays: 182,
    minimum: 200_000,
    annualRateBps: 1375,
    taxBps: 1500,
    ratePercent: "11.69",
  },
];

const STEP = 1_000;
const MAX_SAVINGS = 120;
const DAY_MS = 86_400_000;

function toUtc(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function iso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((toUtc(to) - toUtc(from)) / DAY_MS);
}

/** The same day of the month `months` later, on the 28th at the latest. */
export function addMonths(day: string, months: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const index = (m ?? 1) - 1 + months;
  return iso(Date.UTC((y ?? 1970) + Math.floor(index / 12), index % 12, Math.min(d ?? 1, 28)));
}

/** Interest after tax on `principal` for `days` days. Rounded down twice, as the backend does. */
export function netInterest(principal: number, product: DemoProduct, days: number): number {
  const gross =
    (BigInt(principal) * BigInt(product.annualRateBps) * BigInt(days)) / BigInt(10_000 * 365);
  const tax = (gross * BigInt(product.taxBps)) / BigInt(10_000);
  return Number(gross - tax);
}

function savingDates(today: string, targetDate: string, termDays: number): string[] {
  const dates: string[] = [];
  while (dates.length < MAX_SAVINGS) {
    const day = dates.length === 0 ? today : addMonths(today, dates.length);
    if (daysBetween(day, targetDate) < termDays) {
      break;
    }
    dates.push(day);
  }
  return dates;
}

function reached(saving: number, product: DemoProduct, dates: string[], targetDate: string) {
  return dates.reduce((total, day) => {
    const terms = Math.floor(daysBetween(day, targetDate) / product.termDays);
    return total + saving + netInterest(saving, product, terms * product.termDays);
  }, 0);
}

function planFor(product: DemoProduct, needed: number, today: string, targetDate: string) {
  const dates = savingDates(today, targetDate, product.termDays);
  if (dates.length === 0 || needed <= 0) {
    return null;
  }
  const without = Math.ceil(Math.ceil(needed / dates.length) / STEP) * STEP;
  let low = 1;
  let high = without / STEP;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (reached(middle * STEP, product, dates, targetDate) >= needed) {
      high = middle;
    } else {
      low = middle + 1;
    }
  }
  let saving = high * STEP;
  const raised = saving < product.minimum;
  if (raised) {
    saving = product.minimum;
  }
  const putIn = saving * dates.length;
  const ugx = (minor: number): Money => ({ currency: "UGX", minor });
  return {
    product,
    plan: {
      productCode: product.code,
      productName: product.name,
      ratePercent: product.ratePercent,
      termDays: product.termDays,
      saving: ugx(saving),
      savings: dates.length,
      firstOn: dates[0] as string,
      lastOn: dates[dates.length - 1] as string,
      putIn: ugx(putIn),
      interest: ugx(reached(saving, product, dates, targetDate) - putIn),
      withoutInvesting: ugx(without),
      raisedToMinimum: raised,
    } satisfies Plan,
  };
}

/** The plan asking for the smallest monthly saving, as on the server. */
export function bestPlan(
  needed: number,
  today: string,
  targetDate: string,
  products: DemoProduct[] = DEMO_PRODUCTS,
): Plan | null {
  const plans = products
    .map((product) => planFor(product, needed, today, targetDate))
    .filter((found): found is NonNullable<typeof found> => found !== null);
  plans.sort(
    (a, b) =>
      a.plan.saving.minor - b.plan.saving.minor ||
      b.plan.interest.minor - a.plan.interest.minor ||
      b.plan.termDays - a.plan.termDays,
  );
  return plans[0]?.plan ?? null;
}
