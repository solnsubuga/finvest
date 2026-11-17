import type { Amount } from "./api.ts";
import { CouponError, type Coupon, type CouponDue, type CouponsApi, type CouponsScreen, type CouponStep, type NewCoupon } from "./coupons.ts";

/**
 * Sample coupons for `pnpm dev` and previews, on the desk's sample bond issues (stub.ts). Not real
 * money and not connected to Finvest. Commands change only this browser tab's copy. The rules
 * here stand in for the backend's. The screen itself applies none.
 */

const M = 1_000_000;
const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

export const COUPON_COLLEAGUES = {
  recorder: { id: "-Nstaffdaniel0000000", name: "Daniel Okello" },
  approver: { id: "-Nstaffgrace00000000", name: "Grace Atim" },
};

type Stored = Omit<Coupon, "allowed">;

const ISSUE_NAMES: Record<string, string> = {
  "BOND-2Y-2028": "2 Year Treasury Bond 2028",
  "BOND-5Y-2032": "5 Year Treasury Bond 2032",
  "BOND-10Y-2037": "10 Year Treasury Bond 2037",
};

function sampleDue(): CouponDue[] {
  return [
    // 150m face of a 15.25% bond: half the yearly rate, rounded down.
    { issueCode: "BOND-2Y-2028", name: "2 Year Treasury Bond 2028", couponDate: "2026-11-16", face: ugx(150 * M), expected: ugx(11_437_500) },
  ];
}

function sampleCoupons(): Stored[] {
  const { recorder, approver } = COUPON_COLLEAGUES;
  return [
    {
      id: "-Ncoupon000000000002",
      issueCode: "BOND-10Y-2037",
      couponDate: "2026-08-11",
      face: ugx(55 * M),
      expected: ugx(4_262_500),
      amount: ugx(4_262_500),
      valueDate: "2026-08-12",
      bankReference: "FT26224CPN37",
      note: null,
      status: "recorded",
      recordedBy: recorder.id,
      recordedAt: "2026-11-16T07:30:00Z",
      decidedBy: null,
      decidedAt: null,
      decisionReason: null,
    },
    {
      id: "-Ncoupon000000000001",
      issueCode: "BOND-5Y-2032",
      couponDate: "2026-05-20",
      face: ugx(80 * M),
      expected: ugx(6_000_000),
      amount: ugx(5_850_000),
      valueDate: "2026-05-21",
      bankReference: "FT26141CPN32",
      note: "The custodian took its fee of UGX 150,000 from the coupon, as its note for May shows.",
      status: "approved",
      recordedBy: recorder.id,
      recordedAt: "2026-05-21T09:10:00Z",
      decidedBy: approver.id,
      decidedAt: "2026-05-21T12:45:00Z",
      decisionReason: null,
    },
  ];
}

export function createStubCouponsApi(
  me: { id: string },
  options: { sample?: "sample" | "empty"; mayChange?: boolean; delayMs?: number; today?: () => string; now?: () => Date } = {},
): CouponsApi {
  const { sample = "sample", mayChange = true, delayMs = 150, today = () => new Date().toISOString().slice(0, 10), now = () => new Date() } = options;
  let due = sample === "sample" ? sampleDue() : [];
  const coupons = sample === "sample" ? sampleCoupons() : [];
  const replies = new Map<string, CouponsScreen>();
  let created = 0;

  function allowed(coupon: Stored): CouponStep[] {
    if (!mayChange || coupon.status !== "recorded") return [];
    return coupon.recordedBy === me.id ? ["decline"] : ["approve", "decline"];
  }

  function screen(): CouponsScreen {
    const latestFirst = [...coupons].sort((a, b) => b.couponDate.localeCompare(a.couponDate));
    return { currency: "UGX", due: [...due], coupons: latestFirst.map((c) => ({ ...c, allowed: allowed(c) })), mayChange };
  }

  function command(key: string, run: () => void): Promise<CouponsScreen> {
    return new Promise((resolve, reject) =>
      setTimeout(() => {
        const saved = replies.get(key);
        if (saved) return resolve(saved);
        try {
          run();
          const reply = screen();
          replies.set(key, reply);
          resolve(reply);
        } catch (error) {
          reject(error instanceof Error ? error : new CouponError("unavailable"));
        }
      }, delayMs),
    );
  }

  function find(id: string, step: CouponStep): Stored {
    const coupon = coupons.find((c) => c.id === id);
    if (!coupon) throw new CouponError("not_found");
    if (coupon.status !== "recorded") throw new CouponError("conflict");
    if (!allowed(coupon).includes(step)) throw new CouponError("forbidden");
    return coupon;
  }

  return {
    getCoupons: () => new Promise((resolve) => setTimeout(() => resolve(screen()), delayMs)),
    record: (key, given: NewCoupon) =>
      command(key, () => {
        if (!mayChange) throw new CouponError("forbidden");
        const line = due.find((d) => d.issueCode === given.issueCode && d.couponDate === given.couponDate);
        if (!line) throw new CouponError("already_recorded");
        if (given.valueDate < given.couponDate) throw new CouponError("value_date_before_coupon");
        if (given.valueDate > today()) throw new CouponError("value_date_in_future");
        if (given.amount.minor !== line.expected.minor && !given.note) throw new CouponError("note_required");
        if (coupons.some((c) => c.bankReference === given.bankReference && c.status !== "declined")) throw new CouponError("already_recorded");
        created += 1;
        coupons.push({
          id: `-NcouponSampleNew${String(created).padStart(3, "0")}`,
          issueCode: line.issueCode,
          couponDate: line.couponDate,
          face: line.face,
          expected: line.expected,
          amount: given.amount,
          valueDate: given.valueDate,
          bankReference: given.bankReference,
          note: given.note,
          status: "recorded",
          recordedBy: me.id,
          recordedAt: now().toISOString(),
          decidedBy: null,
          decidedAt: null,
          decisionReason: null,
        });
        due = due.filter((d) => d !== line);
      }),
    approve: (key, id) =>
      command(key, () => {
        const coupon = find(id, "approve");
        Object.assign(coupon, { status: "approved", decidedBy: me.id, decidedAt: now().toISOString() });
      }),
    decline: (key, id, reason) =>
      command(key, () => {
        const coupon = find(id, "decline");
        Object.assign(coupon, { status: "declined", decidedBy: me.id, decidedAt: now().toISOString(), decisionReason: reason });
        // A declined coupon can be recorded again.
        const { issueCode, couponDate, face, expected } = coupon;
        due = [...due, { issueCode, name: ISSUE_NAMES[issueCode] ?? issueCode, couponDate, face, expected }];
      }),
  };
}
