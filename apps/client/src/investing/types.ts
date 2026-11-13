/**
 * The contract between the investing screens and whatever serves them.
 *
 * The shapes follow the client routes in backend/app/modules/investments/api.py
 * (`/client/products`, `/client/products/{code}/quote`, `/client/investments`).
 * `apiInvestingClient.ts` answers it from the API through `packages/api-client`;
 * `stubInvestingClient.ts` is the in-memory demo.
 *
 * A client never sees a tax rate or a figure before tax. Every rate here is
 * the rate after tax, and every amount is the net amount the backend worked
 * out. The app adds nothing up itself.
 */

export type Currency = "UGX" | "USD";

/** An amount in the smallest unit of its currency: whole shillings, or cents. Always an integer. */
export type Amount = { currency: Currency; minor: number };

export type ProductKind = "treasury_bill" | "fixed_income";
export type ProductStatus = "live" | "paused";
export type InterestPaid = "at_maturity" | "monthly";
export type AtMaturity = "roll_over" | "pay_out";
export type InvestmentStatus = "awaiting_payment" | "active" | "matured" | "cancelled";

export type Product = {
  code: string;
  name: string;
  kind: ProductKind;
  currency: Currency;
  /** Null for an open term. */
  termDays: number | null;
  minimum: Amount;
  interestPaid: InterestPaid;
  /** A paused product is shown but takes no new investments. */
  status: ProductStatus;
  /** The yearly rate after tax, as a label: "10.63". Nothing is worked out from it. */
  ratePercent: string;
};

/** What an amount would earn if invested today. */
export type Quote = {
  productCode: string;
  amount: Amount;
  /** A calendar date. The term starts on the day the payment is confirmed; this assumes today. */
  startsOn: string;
  /** Null for an open term, which has no maturity and no fixed interest to quote. */
  maturesOn: string | null;
  interest: Amount | null;
  total: Amount | null;
};

export type Investment = {
  id: string;
  productCode: string;
  productName: string;
  status: InvestmentStatus;
  amount: Amount;
  ratePercent: string;
  termDays: number | null;
  atMaturity: AtMaturity | null;
  canChangeMaturity: boolean;
  /** Pay by this moment, or the quoted rate lapses. ISO 8601, UTC. */
  payBy: string;
  startDate: string | null;
  maturityDate: string | null;
  daysEarned: number;
  interestSoFar: Amount;
  /** Added by the run today. */
  interestToday: Amount;
  /** What it is worth now: amount plus interest so far. */
  value: Amount;
  interestAtMaturity: Amount | null;
  totalAtMaturity: Amount | null;
  rolledIntoId: string | null;
  payoutId: string | null;
  /** Matured, and waiting for the client to say where to send the money. */
  waitingForDestination: boolean;
};

export type NewInvestment = {
  productCode: string;
  amount: Amount;
  /** Fixed terms only. Null means roll over, the default. */
  atMaturity: AtMaturity | null;
  /** Made once when the review screen opens and sent with every try, so a double tap saves one investment. */
  idempotencyKey: string;
};

export interface InvestingClient {
  listProducts(): Promise<Product[]>;
  quote(productCode: string, amount: Amount): Promise<Quote>;
  /** Review and confirm: saves the investment at today's rate, waiting for its payment. */
  requestInvestment(request: NewInvestment): Promise<Investment>;
  /** Newest first, without cancelled ones. */
  listInvestments(): Promise<Investment[]>;
  getInvestment(investmentId: string): Promise<Investment>;
}

export type InvestingErrorCode =
  | "below_minimum"
  | "product_not_open"
  | "not_verified"
  | "too_many_unpaid"
  | "not_found"
  | "key_reused"
  | "unavailable";

export class InvestingError extends Error {
  readonly code: InvestingErrorCode;

  constructor(code: InvestingErrorCode) {
    super(code);
    this.name = "InvestingError";
    this.code = code;
  }
}

const messages: Record<InvestingErrorCode, string> = {
  below_minimum: "That is below the smallest amount for this product.",
  product_not_open: "This product is not taking new investments right now.",
  not_verified: "You can invest once your ID check is approved.",
  too_many_unpaid: "You have investments waiting to be paid for. Pay for one of those, or let it lapse, before starting another.",
  not_found: "We could not find that.",
  key_reused: "Something changed since you last tried. Go back and review your investment again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function describeInvestingError(error: unknown): string {
  return messages[error instanceof InvestingError ? error.code : "unavailable"];
}
