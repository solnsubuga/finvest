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
export type InvestmentStatus = "awaiting_payment" | "active" | "matured" | "cancelled" | "withdrawn" | "unwound" | "transferred";

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
  /** The approved account chosen to be paid at maturity. Null when rolling over. */
  payoutDestinationId: string | null;
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
  /** Running, and the client may ask to withdraw it early now. */
  canWithdraw: boolean;
  /** When the client asked to withdraw early. ISO 8601, UTC. Interest stopped then. */
  withdrawalRequestedAt: string | null;
  /** Running, and part or all of it may be sent to another client now. */
  canSend: boolean;
};

/** Another client, found by their Finvest account number or mobile number. */
export type Recipient = {
  accountNumber: string;
  /** Their first given name and the initial of their surname, "Brian K.". Never a full name. */
  name: string;
};

export type TransferStatus = "waiting" | "held" | "release_proposed" | "done" | "failed" | "declined" | "cancelled";

/** Part or all of an investment sent to another client, or received from one. */
export type Transfer = {
  id: string;
  direction: "sent" | "received";
  /** The other client, shown as "Brian K.". */
  otherName: string;
  status: TransferStatus;
  amount: Amount;
  /** Everything was sent, including interest earned after the request. */
  whole: boolean;
  message: string | null;
  /** ISO 8601, UTC. */
  requestedAt: string;
  finishedAt: string | null;
  /** Sent: the investment it came from. Received: the new investment. */
  investmentId: string | null;
  /** Sent: the new investment holding what the sender kept, if any. */
  keptInvestmentId: string | null;
  /** Why it could not be made, for the sender. */
  failureCode: string | null;
};

export type SendInvestment = {
  investmentId: string;
  /** As `findRecipient` returned it. */
  toAccountNumber: string;
  amount: Amount;
  message: string | null;
  /**
   * Send everything. The server sends the value when it saves the request, so interest added
   * after the screen loaded `amount` is included rather than refused.
   */
  whole: boolean;
  /** Made once when the screen opens and sent with every try, so a double tap sends once. */
  idempotencyKey: string;
};

/** One of the client's approved accounts that money can be paid to. */
export type PayoutAccount = {
  id: string;
  kind: "mobile_money" | "bank";
  /** Named the way the client knows it, the number already masked: "MTN +256 77• ••• 456", "Stanbic Bank •••• 4821". */
  label: string;
  /** Money can be sent here from this moment. Null or in the future: still in its first day's safety wait. */
  payableFrom: string | null;
  /** Past its safety wait when it was read, so it can be chosen. The server judges again on confirm. */
  ready: boolean;
};

export type EarlyWithdrawal = {
  investmentId: string;
  destinationId: string;
  /** Made for this request and sent with every try, so a double tap asks once. */
  idempotencyKey: string;
};

export type MaturityChoice = {
  investmentId: string;
  atMaturity: AtMaturity;
  /** Required to be paid out, and only then: one of the client's approved accounts. */
  destinationId: string | null;
};

export type MaturedPayOut = {
  investmentId: string;
  /** One of the client's approved accounts past its first day. */
  destinationId: string;
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
  /** The client's approved payout accounts, for choosing where an early withdrawal goes. */
  listPayoutAccounts(): Promise<PayoutAccount[]>;
  /** Ask to withdraw a running investment early. It pays `value` as shown: interest stops now. */
  withdrawEarly(request: EarlyWithdrawal): Promise<Investment>;
  /** Change what happens when the term ends. Allowed up to the day before maturity. */
  chooseAtMaturity(choice: MaturityChoice): Promise<Investment>;
  /**
   * Send the money of a matured investment that is waiting for an account. Safe to repeat:
   * the same account asked twice is the same payout, so it needs no idempotency key.
   */
  payOutMatured(request: MaturedPayOut): Promise<Investment>;
  /** Who a Finvest account number or a mobile number belongs to. Only approved clients. */
  findRecipient(to: string): Promise<Recipient>;
  /** Send part or all of a running investment to another client. Nothing is cashed out. */
  sendInvestment(request: SendInvestment): Promise<Transfer>;
  /** What the client sent, and what they received once it arrived. Newest first. */
  listTransfers(): Promise<Transfer[]>;
  getTransfer(transferId: string): Promise<Transfer>;
  /** Cancel a transfer that is waiting for Finvest to check it. */
  cancelTransfer(transferId: string): Promise<Transfer>;
}

export type InvestingErrorCode =
  | "below_minimum"
  | "product_not_open"
  | "not_verified"
  | "too_many_unpaid"
  | "not_found"
  | "key_reused"
  | "invalid"
  | "cannot_withdraw"
  | "account_not_ready"
  | "cannot_change_maturity"
  | "already_sent"
  | "over_mobile_money_limit"
  | "over_limit"
  | "no_such_client"
  | "not_an_account_number"
  | "to_yourself"
  | "below_product_minimum"
  | "more_than_value"
  | "remainder_below_minimum"
  | "cannot_send"
  | "cannot_cancel"
  | "restricted"
  | "too_many_lookups"
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
  invalid: "We could not accept that. Go back, check the details and try again.",
  cannot_withdraw: "This investment cannot be withdrawn early now. It may already be on its way to you, or it matures too soon.",
  account_not_ready: "That account cannot be paid yet. A new account can be paid one day after it is approved.",
  cannot_change_maturity: "What happens at the end can no longer be changed. It matures too soon, or has already ended.",
  already_sent: "This money is already on its way to you. Open your payouts to follow it.",
  over_mobile_money_limit: "That is more than mobile money can take in one payout. Choose a bank account instead.",
  over_limit: "That is more than your withdrawal limit allows today. Choose a bank account, or try again tomorrow.",
  no_such_client: "We could not find a Finvest client with that number. Check it with them and try again.",
  not_an_account_number: "Enter a 10 digit Finvest account number or a Ugandan mobile number.",
  to_yourself: "That is your own account. Send to someone else.",
  below_product_minimum: "That is below the smallest amount for this product, so they could not hold it.",
  more_than_value: "That is more than this investment is worth today.",
  remainder_below_minimum: "What you would keep is below the smallest amount for this product. Send everything, or a little less.",
  cannot_send: "This investment cannot be sent now. It may be maturing, being withdrawn, or already on its way to someone.",
  cannot_cancel: "This can no longer be cancelled. Finvest has already let it through or turned it down.",
  restricted: "For your safety, you can send an investment one day after signing in with a code or resetting your password.",
  too_many_lookups: "You have looked up a lot of numbers. Wait a few minutes and try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function describeInvestingError(error: unknown): string {
  return messages[error instanceof InvestingError ? error.code : "unavailable"];
}
