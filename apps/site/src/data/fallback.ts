import type { Catalogue } from "../lib/products.ts";

/**
 * The rates built into the pages. They show at once, and stay if the API cannot be reached.
 * The browser replaces them with the API's figures when it answers (src/scripts/live.ts).
 *
 * Keep this in step with the catalogue in docs/investing.md whenever an approved rate changes.
 */
export const fallback: Catalogue = {
  updatedOn: "2026-11-13",
  products: [
    {
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 91,
      minimumMinor: 100_000,
      interestPaid: "at_maturity",
      clientRatePercent: "10.63",
      status: "live",
    },
    {
      code: "TREA-182",
      name: "182 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 182,
      minimumMinor: 200_000,
      interestPaid: "at_maturity",
      clientRatePercent: "11.69",
      status: "live",
    },
    {
      code: "USD-BOND",
      name: "USD Fixed Income",
      kind: "fixed_income",
      currency: "USD",
      termDays: null,
      minimumMinor: 50_000,
      interestPaid: "monthly",
      clientRatePercent: "6.89",
      // Paused until paying in and out in dollars is built (docs/decisions.md).
      status: "paused",
    },
  ],
};
