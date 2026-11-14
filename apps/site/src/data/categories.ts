import type { IconName } from "../lib/icons.ts";

/**
 * The kinds of investment Finvest offers, in the order the site lists them. A category is open
 * only when the catalogue has a live product in it (`fallback.ts`, then the API). The rest say
 * Coming soon, and never show a rate.
 */
export interface Category {
  id: string;
  icon: IconName;
  name: string;
  text: string;
  /** The catalogue kind whose live products open this category. `null` until one exists. */
  kind: "treasury_bill" | "fixed_income" | null;
}

export const categories: Category[] = [
  {
    id: "treasury-bills",
    icon: "bank",
    name: "Treasury bills",
    text: "Short loans to the Government of Uganda for 91, 182 or 364 days, bought at the Bank of Uganda auction. You get the full amount back on the maturity date.",
    kind: "treasury_bill",
  },
  {
    id: "treasury-bonds",
    icon: "document",
    name: "Treasury bonds",
    text: "Government of Uganda bonds from 2 to 25 years. They pay interest every six months and the full amount back at the end.",
    kind: null,
  },
  {
    id: "unit-trusts",
    icon: "people",
    name: "Unit trusts",
    text: "Pooled funds run by licensed fund managers. Put in a little at a time and take money out when you need it.",
    kind: null,
  },
  {
    id: "fixed-deposits",
    icon: "shield",
    name: "Fixed income deposits",
    text: "Money placed for an agreed time at a fixed rate, in shillings or dollars, with the return known from day one.",
    kind: "fixed_income",
  },
  {
    id: "equity-portfolios",
    icon: "target",
    name: "Thematic equity portfolios",
    text: "Ready-made baskets of shares chosen around one theme, for money you can leave invested for years.",
    kind: null,
  },
];
