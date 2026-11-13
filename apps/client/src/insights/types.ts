/**
 * The contract between the Insights screen and the backend's
 * `GET /client/insights` (docs/insights.md). Every amount is after tax, and no
 * tax rate or tax amount is ever part of it.
 */

import { Money } from "@/payments/types";

/** Insights cover one currency at a time. The two are never added together. */
export type Currency = Money["currency"];

export type InsightsMonth = {
  /** YYYY-MM-01. */
  month: string;
  /** New money that started earning this month. Rollovers are not counted. */
  invested: Money;
  /** Interest earned this month, after tax. */
  earned: Money;
  /** Money that reached the client's mobile money or bank account. */
  paidOut: Money;
};

export type Upcoming = {
  /** YYYY-MM-DD. */
  on: string;
  investmentId: string;
  productName: string;
  atMaturity: "roll_over" | "pay_out" | null;
  /** What comes back at maturity, after tax. */
  amount: Money;
};

export type Insights = {
  /** Every amount below is in this currency. */
  currency: Currency;
  /** Oldest first. The last is this month, which is still under way. */
  months: InsightsMonth[];
  investedNow: Money;
  earnedSoFar: Money;
  /** Soonest first, at most five. */
  upcoming: Upcoming[];
};

export class InsightsError extends Error {
  constructor() {
    super("unavailable");
    this.name = "InsightsError";
  }
}

export interface InsightsClient {
  getInsights(months: number, currency: Currency): Promise<Insights>;
  /**
   * The currencies the client has invested in, shillings first. Shillings are always
   * listed, so a client with no investments yet still sees their (empty) insights.
   */
  heldCurrencies(): Promise<Currency[]>;
}
