import { addDemoPayable, demoPaidAt } from "@/demo/payables";
import { isAboveLargest } from "@/investing/limits";

import { Amount, Bond, BondHolding, BondQuote, BondsClient, BondsError, bondPayableId } from "./types";

/**
 * An in-memory stand-in for the bond routes, so the screens can be built and tried without the
 * backend. Nothing here is real: no money, and everything is forgotten when the app reloads.
 * `resolveBonds.ts` keeps it out of production builds.
 *
 * It works the figures out the way docs/bonds.md says the backend does, so the demo matches the
 * worked example there: UGX 1,000,000 of the 2037 bond bought on 16 November 2026 at 98.5 costs
 * UGX 985,883 and pays UGX 72,000 a coupon after tax. The screens never do these sums.
 */

type Terms = { couponBps: number; taxBps: number };
type Issue = Omit<Bond, "nextCouponDate" | "couponsLeft" | "canBuy"> & Terms;

const UGX = (minor: number): Amount => ({ currency: "UGX", minor });

const ISSUES: Issue[] = [
  {
    code: "BOND-10Y-2037",
    isin: "UG12K1405378",
    name: "10 Year Treasury Bond 2037",
    currency: "UGX",
    couponPercent: "14.40",
    issueDate: "2026-11-14",
    maturityDate: "2037-05-14",
    years: 10,
    minimumFace: UGX(100_000),
    faceStep: UGX(100_000),
    price: 985_000,
    status: "live",
    couponBps: 1600,
    taxBps: 1000,
  },
  {
    code: "BOND-5Y-2031",
    isin: "UG12J2208319",
    name: "5 Year Treasury Bond 2031",
    currency: "UGX",
    couponPercent: "12.00",
    issueDate: "2026-08-22",
    maturityDate: "2031-08-22",
    years: 5,
    minimumFace: UGX(100_000),
    faceStep: UGX(100_000),
    price: 1_012_500,
    status: "live",
    couponBps: 1500,
    taxBps: 2000,
  },
  {
    code: "BOND-2Y-2028",
    isin: "UG12H0903286",
    name: "2 Year Treasury Bond 2028",
    currency: "UGX",
    couponPercent: "11.20",
    issueDate: "2026-03-09",
    maturityDate: "2028-03-09",
    years: 2,
    minimumFace: UGX(100_000),
    faceStep: UGX(100_000),
    price: 998_750,
    status: "paused",
    couponBps: 1400,
    taxBps: 2000,
  },
];

const DAY_MS = 24 * 60 * 60 * 1000;
/** Uganda keeps UTC+3 all year. */
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;
/** A bond's price moves with the market, so a purchase holds for 24 hours, not a bill's 72. */
const PAY_WINDOW_MS = 24 * 60 * 60 * 1000;
const MAX_UNPAID = 5;
/** Par, per 100 of face value to four decimal places. */
const PAR = 1_000_000;

function kampalaDay(ms: number): string {
  return new Date(ms + KAMPALA_OFFSET_MS).toISOString().slice(0, 10);
}

function dayNumber(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1) / DAY_MS;
}

/** `months` before or after `iso`, on `day` of the month or the month's last day when it is shorter. */
function shiftMonths(iso: string, months: number, day: number): string {
  const [year, month] = iso.split("-").map(Number);
  const first = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1 + months, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  first.setUTCDate(Math.min(day, last));
  return first.toISOString().slice(0, 10);
}

/** Every six months back from maturity, after the issue date, earliest first. */
function couponDates(issue: Issue): string[] {
  const day = Number(issue.maturityDate.slice(8, 10));
  const dates: string[] = [];
  for (let back = 0; ; back += 6) {
    const date = shiftMonths(issue.maturityDate, -back, day);
    if (date <= issue.issueDate) break;
    dates.push(date);
  }
  return dates.reverse();
}

type Period = { previous: string; next: string | null; left: number };

function periodOn(issue: Issue, today: string): Period {
  const dates = couponDates(issue);
  const coming = dates.filter((date) => date > today);
  const passed = dates.filter((date) => date <= today);
  return { previous: passed[passed.length - 1] ?? issue.issueDate, next: coming[0] ?? null, left: coming.length };
}

function grossCoupon(issue: Issue, face: number): number {
  return Math.floor((face * issue.couponBps) / 10_000 / 2);
}

function netCoupon(issue: Issue, face: number): number {
  const gross = grossCoupon(issue, face);
  return gross - Math.floor((gross * issue.taxBps) / 10_000);
}

function bondOn(issue: Issue, today: string): Bond {
  const { couponBps: _coupon, taxBps: _tax, ...shown } = issue;
  const period = periodOn(issue, today);
  return {
    ...shown,
    nextCouponDate: period.next,
    couponsLeft: period.left,
    // Not once the last coupon period has begun: the next coupon would be the one at maturity.
    canBuy: issue.status === "live" && issue.price !== null && period.left >= 2,
  };
}

type Stored = {
  id: string;
  issue: Issue;
  face: number;
  price: number;
  accrued: number;
  cost: number;
  createdAtMs: number;
};

/** Shared by every demo client, because the demo payments remember payables by ID across them. */
let nextId = 0;

export type StubBondsOptions = {
  delayMs?: number;
  now?: () => number;
  /** The bond list fails, as when the network drops, to show that state. */
  failList?: boolean;
};

export function createStubBondsClient(options: StubBondsOptions = {}): BondsClient {
  const delayMs = options.delayMs ?? 400;
  const now = options.now ?? (() => Date.now());
  const stored = new Map<string, Stored>();
  const byKey = new Map<string, { id: string; request: string }>();

  // No timer at all without a delay, so a test's press that waits for the answer is not left
  // waiting on a timer nothing advances.
  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();
  const today = () => kampalaDay(now());

  function find(code: string): Issue {
    const issue = ISSUES.find((item) => item.code === code);
    if (!issue || issue.maturityDate <= today()) throw new BondsError("not_found");
    return issue;
  }

  function quoteFor(issue: Issue, face: Amount): BondQuote {
    const on = today();
    const period = periodOn(issue, on);
    if (issue.price === null) throw new BondsError("no_price");
    if (face.currency !== issue.currency) throw new BondsError("not_open");
    if (issue.status !== "live") throw new BondsError("not_open");
    if (period.left < 2 || period.next === null) throw new BondsError("too_close_to_maturity");
    if (face.minor < issue.minimumFace.minor) throw new BondsError("below_minimum");
    if (face.minor % issue.faceStep.minor !== 0) throw new BondsError("not_a_step");
    if (isAboveLargest(face)) throw new BondsError("above_maximum");
    // Paid rounds up, and accrued interest rounds down, as docs/bonds.md has it.
    const clean = Math.ceil((face.minor * issue.price) / PAR);
    const days = dayNumber(on) - dayNumber(period.previous);
    const length = dayNumber(period.next) - dayNumber(period.previous);
    const accrued = Math.floor((grossCoupon(issue, face.minor) * days) / length);
    const coupon = netCoupon(issue, face.minor);
    const totalBack = face.minor + coupon * period.left;
    const cost = clean + accrued;
    return {
      code: issue.code,
      face,
      price: issue.price,
      accrued: UGX(accrued),
      cost: UGX(cost),
      coupon: UGX(coupon),
      couponsLeft: period.left,
      nextCouponDate: period.next,
      totalBack: UGX(totalBack),
      gain: UGX(totalBack - cost),
    };
  }

  function view(entry: Stored): BondHolding {
    const paidAt = demoPaidAt(bondPayableId(entry.id));
    const payByMs = entry.createdAtMs + PAY_WINDOW_MS;
    const status = paidAt ? "active" : now() > payByMs ? "cancelled" : "unpaid";
    const period = periodOn(entry.issue, today());
    const coupon = netCoupon(entry.issue, entry.face);
    return {
      id: entry.id,
      code: entry.issue.code,
      name: entry.issue.name,
      face: UGX(entry.face),
      price: entry.price,
      accrued: UGX(entry.accrued),
      cost: UGX(entry.cost),
      status,
      createdAt: new Date(entry.createdAtMs).toISOString(),
      payBy: status === "unpaid" ? new Date(payByMs).toISOString() : null,
      startDate: paidAt ? kampalaDay(Date.parse(paidAt)) : null,
      maturityDate: entry.issue.maturityDate,
      coupon: UGX(coupon),
      couponsLeft: period.left,
      nextCouponDate: period.next,
      stillToCome: UGX(entry.face + coupon * period.left),
      couponDestinationId: null,
    };
  }

  return {
    async listBonds() {
      await pause();
      if (options.failList) throw new BondsError("unavailable");
      const on = today();
      return ISSUES.filter((issue) => issue.maturityDate > on).map((issue) => bondOn(issue, on));
    },

    async getBond(code) {
      await pause();
      return bondOn(find(code), today());
    },

    async quote(code, face) {
      await pause();
      return quoteFor(find(code), face);
    },

    async buy(request) {
      await pause();
      // Like the backend: a known key returns its purchase, and the same key for another one is refused.
      const details = JSON.stringify([request.code, request.face]);
      const known = byKey.get(request.idempotencyKey);
      if (known) {
        if (known.request !== details) throw new BondsError("key_reused");
        return view(stored.get(known.id)!);
      }
      const issue = find(request.code);
      const quote = quoteFor(issue, request.face);
      const unpaid = [...stored.values()].filter((entry) => view(entry).status === "unpaid");
      if (unpaid.length >= MAX_UNPAID) throw new BondsError("too_many_unpaid");
      nextId += 1;
      const entry: Stored = {
        id: `demo-holding-${nextId}`,
        issue,
        face: request.face.minor,
        price: quote.price,
        accrued: quote.accrued.minor,
        cost: quote.cost.minor,
        createdAtMs: now(),
      };
      stored.set(entry.id, entry);
      byKey.set(request.idempotencyKey, { id: entry.id, request: details });
      addDemoPayable({ id: bondPayableId(entry.id), kind: "bond", title: issue.name, amount: quote.cost });
      return view(entry);
    },

    async listHoldings() {
      await pause();
      return [...stored.values()]
        .map(view)
        .filter((holding) => holding.status !== "cancelled")
        .reverse();
    },

    async getHolding(holdingId) {
      await pause();
      const entry = stored.get(holdingId);
      if (!entry) throw new BondsError("not_found");
      return view(entry);
    },
  };
}
