import type { Investment } from "../investments/api.ts";
import type { Amount } from "../products/api.ts";
import { MaturitiesError, type Maturities, type MaturitiesApi, type MaturityDay } from "./api.ts";

/**
 * Sample maturities for `pnpm dev` and previews. Not real clients or money. Today is
 * 14 November 2026: one term from 12 November is still running because the run failed it, and
 * one from 11 November waits for its client to say where to be paid.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const usd = (minor: number): Amount => ({ minor, currency: "USD" });

const TODAY = "2026-11-14";

function day(date: string, currency: "UGX" | "USD", counts: Partial<Omit<MaturityDay, "day" | "currency" | "principal">> & { principal: number }): MaturityDay {
  const done = { rolledOver: 0, paidOut: 0, held: 0, openRollOver: 0, openPayOut: 0, ...counts };
  return {
    day: date,
    currency,
    investments: done.rolledOver + done.paidOut + done.held + done.openRollOver + done.openPayOut,
    principal: currency === "UGX" ? ugx(counts.principal) : usd(counts.principal),
    rolledOver: done.rolledOver,
    paidOut: done.paidOut,
    held: done.held,
    openRollOver: done.openRollOver,
    openPayOut: done.openPayOut,
  };
}

const DAYS: MaturityDay[] = [
  day("2026-11-09", "UGX", { principal: 41_500_000, rolledOver: 9, paidOut: 3 }),
  day("2026-11-10", "UGX", { principal: 18_250_000, rolledOver: 4, paidOut: 2 }),
  day("2026-11-11", "UGX", { principal: 27_000_000, rolledOver: 6, paidOut: 1, held: 1 }),
  day("2026-11-11", "USD", { principal: 1_250_000, paidOut: 2 }),
  day("2026-11-12", "UGX", { principal: 33_700_000, rolledOver: 7, paidOut: 2, openPayOut: 1 }),
  day("2026-11-13", "UGX", { principal: 12_400_000, rolledOver: 3, paidOut: 1 }),
  day("2026-11-14", "UGX", { principal: 22_800_000, openRollOver: 5, openPayOut: 2 }),
  day("2026-11-16", "UGX", { principal: 9_600_000, openRollOver: 2, openPayOut: 1 }),
  day("2026-11-19", "UGX", { principal: 51_300_000, openRollOver: 11, openPayOut: 4 }),
  day("2026-11-19", "USD", { principal: 3_000_000, openPayOut: 1, openRollOver: 2 }),
  day("2026-11-22", "UGX", { principal: 14_000_000, openRollOver: 2, openPayOut: 1 }),
  day("2026-11-26", "UGX", { principal: 30_100_000, openRollOver: 6, openPayOut: 2 }),
];

function investment(fields: Pick<Investment, "id" | "clientId" | "amount" | "maturityDate" | "status" | "atMaturity"> & Partial<Investment>): Investment {
  const { currency } = fields.amount;
  return {
    productCode: "TREA-91",
    productName: "91 Day Treasury Bill",
    annualRateBps: 1250,
    taxBps: 1500,
    termDays: 91,
    createdAt: "2026-08-12T08:00:00Z",
    payBy: "2026-08-12T08:00:00Z",
    startDate: "2026-08-12",
    accruedThrough: fields.maturityDate,
    interestGross: { minor: 31_164, currency },
    tax: { minor: 4_674, currency },
    interestNet: { minor: 26_490, currency },
    rolledIntoId: null,
    rolledFromId: null,
    payoutId: null,
    payoutRefusal: null,
    ...fields,
  };
}

const NOT_CLOSED: Investment[] = [
  investment({ id: "-InvSample00000000N1", clientId: "sample-client-peter", amount: ugx(1_000_000), maturityDate: "2026-11-12", status: "active", atMaturity: "pay_out" }),
];

const HELD: Investment[] = [
  investment({
    id: "-InvSample00000000H1",
    clientId: "sample-client-joan",
    amount: ugx(1_000_000),
    maturityDate: "2026-11-11",
    status: "matured",
    atMaturity: "pay_out",
    payoutRefusal: "no_destination",
  }),
];

export function createStubMaturitiesApi(which: StubCase = "sample", delayMs = 150): MaturitiesApi {
  const found: Maturities =
    which === "empty" ? { today: TODAY, days: [], notClosed: [], held: [] } : { today: TODAY, days: DAYS, notClosed: NOT_CLOSED, held: HELD };
  return {
    getMaturities: () =>
      new Promise((resolve, reject) =>
        setTimeout(() => (which === "error" ? reject(new MaturitiesError("unavailable")) : resolve(found)), delayMs),
      ),
  };
}
