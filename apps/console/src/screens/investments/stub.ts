import type { Amount, InterestRun, Investment, InvestmentsApi } from "./api.ts";

/**
 * Sample investments and interest runs for `pnpm dev` and previews. Not real
 * clients or money. The figures follow the backend's own example: UGX 1,000,000
 * in the 91 day bill at 12.5% earns 342 a day before tax, 291 after.
 */

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const usd = (minor: number): Amount => ({ minor, currency: "USD" });

interface Sample {
  id: string;
  client: string;
  product: "TREA-91" | "TREA-182" | "USD-BOND";
  amount: Amount;
  status: Investment["status"];
  start: string | null;
  maturity: string | null;
  through: string | null;
  gross: number;
  tax: number;
  atMaturity: Investment["atMaturity"];
  refusal?: string;
  rolledInto?: string;
}

const PRODUCTS = {
  "TREA-91": { name: "91 Day Treasury Bill", rate: 1250, tax: 1500, term: 91 },
  "TREA-182": { name: "182 Day Treasury Bill", rate: 1375, tax: 1500, term: 182 },
  "USD-BOND": { name: "USD Fixed Income", rate: 725, tax: 500, term: null },
} as const;

const SAMPLES: Sample[] = [
  { id: "-InvSample00000000A1", client: "-ClientSample0000001", product: "TREA-91", amount: ugx(5_000_000), status: "active", start: "2026-08-20", maturity: "2026-11-19", through: "2026-11-13", gross: 145_521, tax: 21_825, atMaturity: "roll_over" },
  { id: "-InvSample00000000A2", client: "-ClientSample0000002", product: "TREA-182", amount: ugx(12_000_000), status: "active", start: "2026-05-24", maturity: "2026-11-22", through: "2026-11-13", gross: 769_644, tax: 115_440, atMaturity: "pay_out" },
  { id: "-InvSample00000000A3", client: "-ClientSample0000003", product: "TREA-91", amount: ugx(1_000_000), status: "active", start: "2026-10-02", maturity: "2027-01-01", through: "2026-11-13", gross: 14_022, tax: 2_091, atMaturity: "pay_out" },
  { id: "-InvSample00000000A4", client: "-ClientSample0000004", product: "USD-BOND", amount: usd(2_500_00), status: "active", start: "2026-09-01", maturity: null, through: "2026-11-13", gross: 3_626, tax: 181, atMaturity: null },
  { id: "-InvSample00000000A5", client: "-ClientSample0000005", product: "TREA-182", amount: ugx(3_000_000), status: "active", start: "2026-11-02", maturity: "2027-05-03", through: "2026-11-13", gross: 11_300, tax: 1_690, atMaturity: "roll_over" },
  { id: "-InvSample00000000P1", client: "-ClientSample0000006", product: "TREA-91", amount: ugx(2_000_000), status: "awaiting_payment", start: null, maturity: null, through: null, gross: 0, tax: 0, atMaturity: "pay_out" },
  { id: "-InvSample00000000M1", client: "-ClientSample0000007", product: "TREA-91", amount: ugx(1_000_000), status: "matured", start: "2026-08-13", maturity: "2026-11-12", through: "2026-11-12", gross: 31_164, tax: 4_674, atMaturity: "pay_out", refusal: "no_destination" },
  { id: "-InvSample00000000M2", client: "-ClientSample0000001", product: "TREA-91", amount: ugx(4_000_000), status: "matured", start: "2026-08-12", maturity: "2026-11-11", through: "2026-11-11", gross: 124_656, tax: 18_696, atMaturity: "roll_over", rolledInto: "-InvSample00000000R1" },
  { id: "-InvSample00000000C1", client: "-ClientSample0000008", product: "TREA-182", amount: ugx(500_000), status: "cancelled", start: null, maturity: null, through: null, gross: 0, tax: 0, atMaturity: "roll_over" },
];

function investment(sample: Sample): Investment {
  const product = PRODUCTS[sample.product];
  const { currency } = sample.amount;
  return {
    id: sample.id,
    clientId: sample.client,
    productCode: sample.product,
    productName: product.name,
    status: sample.status,
    amount: sample.amount,
    annualRateBps: product.rate,
    taxBps: product.tax,
    termDays: product.term,
    atMaturity: sample.atMaturity,
    createdAt: `${sample.start ?? "2026-11-12"}T08:00:00Z`,
    payBy: `${sample.start ?? "2026-11-15"}T08:00:00Z`,
    startDate: sample.start,
    maturityDate: sample.maturity,
    accruedThrough: sample.through,
    interestGross: { minor: sample.gross, currency },
    tax: { minor: sample.tax, currency },
    interestNet: { minor: sample.gross - sample.tax, currency },
    rolledIntoId: sample.rolledInto ?? null,
    rolledFromId: null,
    payoutId: null,
    payoutRefusal: sample.refusal ?? null,
  };
}

function runs(): InterestRun[] {
  const days = ["2026-11-13", "2026-11-12", "2026-11-11", "2026-11-10", "2026-11-09"];
  // The run for a day starts at 00:30 that day in Kampala, which is 21:30 UTC the day before.
  const eve = (day: string) => {
    const date = new Date(`${day}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - 1);
    return date.toISOString().slice(0, 10);
  };
  return days.flatMap((day, index) => [
    {
      runDate: day,
      currency: "UGX" as const,
      investments: 1_284 - index * 3,
      gross: ugx(4_118_400 - index * 9_300),
      tax: ugx(617_760 - index * 1_395),
      net: ugx(3_500_640 - index * 7_905),
      behind: day === "2026-11-11" ? 1 : 0,
      startedAt: `${eve(day)}T21:30:00Z`,
      finishedAt: `${eve(day)}T21:31:12Z`,
    },
    {
      runDate: day,
      currency: "USD" as const,
      investments: 41,
      gross: usd(7_180),
      tax: usd(359),
      net: usd(6_821),
      behind: 0,
      startedAt: `${eve(day)}T21:30:00Z`,
      finishedAt: `${eve(day)}T21:31:14Z`,
    },
  ]);
}

export function createStubInvestmentsApi(sample: "default" | "empty" = "default"): InvestmentsApi {
  const all = sample === "empty" ? [] : SAMPLES.map(investment);
  const history = sample === "empty" ? [] : runs();
  return {
    listInvestments: (status) => Promise.resolve(all.filter((i) => !status || i.status === status)),
    interestRuns: () => Promise.resolve(history),
  };
}
