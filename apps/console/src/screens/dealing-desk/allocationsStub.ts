import type { Amount } from "./api.ts";
import type { AllocatedLot, AllocationReport, AllocationsApi, InvestmentBills, ProductAllocation } from "./allocations.ts";

/**
 * A sample allocation report for `pnpm dev` and previews. Not real bills and not connected to
 * Finvest. The figures agree with each other and with the desk's sample (stub.ts): what each
 * product is owed, the bills held and what is still to buy.
 */

const M = 1_000_000;
const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

export const SAMPLE_INVESTMENT = "-NinvSample00000042";

function lot(lotId: string, settledOn: string, maturesOn: string, reference: string, face: number, cost: number, allocated: number, investments: number): AllocatedLot {
  return {
    lotId,
    settledOn,
    maturesOn,
    custodianReference: reference,
    face: ugx(face),
    cost: ugx(cost),
    allocated: ugx(allocated),
    unallocated: ugx(cost - allocated),
    investments,
  };
}

function product(productCode: string, lots: AllocatedLot[], investments: number, owed: number, waitingCount: number): ProductAllocation {
  const sum = (pick: (l: AllocatedLot) => Amount) => lots.reduce((total, l) => total + pick(l).minor, 0);
  const allocated = sum((l) => l.allocated);
  return {
    productCode,
    lots,
    costHeld: ugx(sum((l) => l.cost)),
    allocated: ugx(allocated),
    unallocated: ugx(sum((l) => l.unallocated)),
    investments,
    owed: ugx(owed),
    covered: ugx(allocated),
    investmentsWaiting: waitingCount,
    waiting: ugx(owed - allocated),
  };
}

const SAMPLE: AllocationReport = {
  currency: "UGX",
  on: "2026-11-13",
  products: [
    product(
      "TREA-91",
      [
        lot("-Nlot0000000000091A", "2026-10-15", "2027-01-14", "CSD-2026-0412", 3_600 * M, 3_514 * M, 3_514 * M, 1_204),
        lot("-Nlot0000000000091B", "2026-11-05", "2027-02-04", "CSD-2026-0466", 3_750 * M, 3_666 * M, 3_666 * M, 1_187),
      ],
      2_473,
      7_412 * M,
      82,
    ),
    product(
      "TREA-182",
      [
        lot("-Nlot000000000182A", "2026-09-17", "2027-03-18", "CSD-2026-0377", 5_100 * M, 4_860 * M, 4_860 * M, 1_602),
        lot("-Nlot000000000182B", "2026-11-12", "2027-05-13", "CSD-2026-0481", 5_150 * M, 4_856 * M, 4_856 * M, 1_571),
      ],
      3_224,
      9_870 * M,
      51,
    ),
  ],
};

const BILLS: InvestmentBills = {
  investmentId: SAMPLE_INVESTMENT,
  bills: [{ lotId: "-Nlot0000000000091A", productCode: "TREA-91", maturesOn: "2027-01-14", custodianReference: "CSD-2026-0412", cost: ugx(2_500_000) }],
  total: ugx(2_500_000),
};

export function createStubAllocationsApi(sample: "sample" | "empty" = "sample"): AllocationsApi {
  const empty: AllocationReport = {
    ...SAMPLE,
    products: SAMPLE.products.map((p) => ({
      ...p,
      lots: [],
      costHeld: ugx(0),
      allocated: ugx(0),
      unallocated: ugx(0),
      investments: 0,
      owed: ugx(0),
      covered: ugx(0),
      investmentsWaiting: 0,
      waiting: ugx(0),
    })),
  };
  return {
    getReport: (currency) => Promise.resolve({ ...(sample === "empty" ? empty : SAMPLE), currency }),
    billsBehind: (id) => Promise.resolve(id === SAMPLE_INVESTMENT && sample === "sample" ? BILLS : { investmentId: id, bills: [], total: null }),
  };
}
