/**
 * Real Government of Uganda securities from the Bank of Uganda's most recent auctions. The
 * website's products page shows them so a visitor can see what the government is paying today,
 * and the console's Rates screen shows the bill auction as its Last auction tile.
 *
 * Every yield here is after withholding tax, because clients only ever see net figures
 * (docs/rules.md). Each was worked out from the Bank of Uganda's published result and the
 * withholding tax the Bank of Uganda states for that tenor in its invitation to tender: 20% on
 * bills and on bonds shorter than 10 years, 10% on bonds of 10 years or more. The workings sit
 * beside each figure so the next update can follow them.
 *
 * Bills also carry the before-tax yield and the tax as fields, for staff only: the console sets
 * Finvest's own rates from them. The website must never put a before-tax field on a page.
 *
 * Update this file after each auction, from the results the Bank of Uganda publishes at
 * bou.or.ug (Financial markets, Government securities). Update the website's `fallback.ts` in
 * the same change when Finvest's own rates move with it.
 */

export interface AuctionedBill {
  termDays: 91 | 182 | 364;
  isin: string;
  maturesOn: string;
  /** The auction's yield after withholding tax, two decimal places: "9.00". */
  netYieldPercent: string;
  /** Staff only, never on the website: the yield as the Bank of Uganda published it, "11.249". */
  grossYieldPercent: string;
  /** Staff only: the withholding tax the Bank of Uganda states for the tenor, "20". */
  taxPercent: string;
  /** What the Bank of Uganda accepted, in billions of shillings, as written: "23.1". */
  acceptedBillions: string;
}

export interface AuctionedBond {
  years: number;
  isin: string;
  maturesOn: string;
  /** The auction's cut-off yield to maturity after withholding tax: "12.80". */
  netYieldPercent: string;
  /**
   * The coupon after withholding tax, paid every six months, for each UGX 1,000,000 of face
   * value. `null` when the coupon could not be read from a published result, and the line is
   * left off the page.
   */
  netCouponPerMillion: number | null;
  acceptedBillions: string;
}

export interface Auction<T> {
  /** The auction date, as YYYY-MM-DD. */
  heldOn: string;
  /** How the Bank of Uganda names the result, quoted in the source line. */
  source: string;
  securities: T[];
}

export const billAuction: Auction<AuctionedBill> = {
  heldOn: "2026-10-21",
  source: "Bank of Uganda, Treasury bill auction results, issue 1236, 21 October 2026",
  securities: [
    // Money market yield 11.249%, less 20%.
    {
      termDays: 91,
      isin: "UG11B2101274",
      maturesOn: "2027-01-21",
      netYieldPercent: "9.00",
      grossYieldPercent: "11.249",
      taxPercent: "20",
      acceptedBillions: "23.1",
    },
    // 13.400%, less 20%.
    {
      termDays: 182,
      isin: "UG11C2204274",
      maturesOn: "2027-04-22",
      netYieldPercent: "10.72",
      grossYieldPercent: "13.400",
      taxPercent: "20",
      acceptedBillions: "37.7",
    },
    // 14.499%, less 20%.
    {
      termDays: 364,
      isin: "UG11E2110279",
      maturesOn: "2027-10-21",
      netYieldPercent: "11.60",
      grossYieldPercent: "14.499",
      taxPercent: "20",
      acceptedBillions: "311.7",
    },
  ],
};

export const bondAuction: Auction<AuctionedBond> = {
  heldOn: "2026-10-28",
  source: "Bank of Uganda, Treasury bond auction results, 28 October 2026",
  securities: [
    // Cut-off yield 16.000%, less 20%. Coupon 15.250%: 76,250 a half year, less 20%.
    {
      years: 2,
      isin: "UG0000001376",
      maturesOn: "2028-11-16",
      netYieldPercent: "12.80",
      netCouponPerMillion: 61_000,
      acceptedBillions: "193.3",
    },
    // 16.800%, less 20%. Coupon 15.000%: 75,000 a half year, less 20%.
    {
      years: 5,
      isin: "UG12J2005328",
      maturesOn: "2032-05-20",
      netYieldPercent: "13.44",
      netCouponPerMillion: 60_000,
      acceptedBillions: "202.4",
    },
    // 17.925%, less 10%. A new bond in September 2026; its coupon is not in the result.
    {
      years: 15,
      isin: "UG12K0509410",
      maturesOn: "2041-09-05",
      netYieldPercent: "16.13",
      netCouponPerMillion: null,
      acceptedBillions: "552.2",
    },
    // 18.475%, less 10%. Coupon 16.000%: 80,000 a half year, less 10%.
    {
      years: 25,
      isin: "UG12M0707507",
      maturesOn: "2050-07-07",
      netYieldPercent: "16.63",
      netCouponPerMillion: 72_000,
      acceptedBillions: "933.0",
    },
  ],
};

/** The bond tenors the Bank of Uganda auctions in the other round each month. */
export const otherBondTenors = [3, 10, 20] as const;
