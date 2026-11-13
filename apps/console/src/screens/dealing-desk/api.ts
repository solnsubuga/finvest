/**
 * The console's view of the dealing desk.
 *
 * A stand-in for the generated client in packages/api-client, like AuthApi.
 * The shapes follow backend/app/modules/dealing (see docs/dealing-desk.md).
 * When the backend publishes its routes, an implementation that calls them
 * replaces the stub in index.tsx and the screen does not change.
 *
 * The console never works out a money figure. Every amount, status and
 * allowed action comes from the backend. Forms check what was typed only to
 * save a round trip, and the backend checks it again.
 */

export type Currency = "UGX" | "USD";

/** An integer in the smallest unit: whole shillings for UGX, cents for USD. */
export interface Amount {
  minor: number;
  currency: Currency;
}

export type Cover = "covered" | "bid_needed" | "on_order" | "surplus";
export type BufferLevel = "on_target" | "under_target" | "below_floor";
export type OrderKind = "auction_bid" | "secondary_purchase" | "secondary_sale";
export type OrderStatus = "prepared" | "approved" | "sent_back" | "withdrawn" | "result_recorded" | "settled" | "unsuccessful";

/** What the signed-in person may do to an order as it stands. The backend decides. */
export type OrderAction = "approve" | "send_back" | "withdraw" | "record_result" | "confirm_result" | "reject_result";

export interface ProductPosition {
  productCode: string;
  productName: string;
  /** True for Treasury bills, which never trade above their face value. */
  discountOnly: boolean;
  /** The smallest step an order accepts. */
  lotSize: Amount;
  clientsOwed: Amount;
  billsHeld: Amount;
  toBuy: Amount;
  onOrder: Amount;
  /** Face value held that is not already in an open sale. */
  faceHeld: Amount;
  cover: Cover;
  /** A calendar date, or null when the product is bought on demand. */
  nextAuction: string | null;
}

export interface Fill {
  face: Amount;
  /** Paid for a purchase, received for a sale. */
  amount: Amount;
  settledOn: string;
  maturesOn: string;
  custodianReference: string;
}

export interface Order {
  id: string;
  kind: OrderKind;
  productCode: string;
  productName: string;
  face: Amount;
  status: OrderStatus;
  preparedBy: string;
  preparedAt: string;
  /** Why it was sent back, or why its result was rejected. */
  note: string | null;
  result: Fill | null;
  resultRecordedBy: string | null;
  allowed: readonly OrderAction[];
}

export interface Buffer {
  balance: Amount;
  target: Amount;
  floor: Amount;
  level: BufferLevel;
  fromBufferUpTo: Amount;
  payWithinWorkingDays: number;
  /** The balance once the early withdrawals already asked for are paid. */
  afterPromised: Amount;
}

export interface Desk {
  moneyToPlace: Amount;
  billsHeld: Amount;
  /** Null until the holdings have been compared with a custodian statement. */
  matchesCustodian: boolean | null;
  buffer: Buffer;
  earlyWithdrawals: { asked: Amount; clients: number };
  positions: readonly ProductPosition[];
  /** Orders that are not finished, oldest first. */
  orders: readonly Order[];
}

export interface NewOrder {
  kind: OrderKind;
  productCode: string;
  face: Amount;
}

/**
 * Every command takes an idempotency key made once per attempt by the screen,
 * so a repeat from a weak connection is replayed and never applied twice.
 */
export interface DealingApi {
  getDesk(): Promise<Desk>;
  prepareOrder(key: string, order: NewOrder): Promise<Desk>;
  approve(key: string, orderId: string): Promise<Desk>;
  sendBack(key: string, orderId: string, note: string): Promise<Desk>;
  withdraw(key: string, orderId: string): Promise<Desk>;
  /** A fill of null means nothing was dealt. */
  recordResult(key: string, orderId: string, fill: Fill | null): Promise<Desk>;
  confirmResult(key: string, orderId: string): Promise<Desk>;
  rejectResult(key: string, orderId: string, note: string): Promise<Desk>;
}

export type DealingErrorCode =
  | "not_found"
  | "conflict"
  | "same_person"
  | "not_a_whole_lot"
  | "more_than_held"
  | "over_the_order"
  | "above_face"
  | "settles_in_the_future"
  | "forbidden"
  | "not_connected"
  | "unavailable";

export class DealingError extends Error {
  readonly code: DealingErrorCode;

  constructor(code: DealingErrorCode) {
    super(code);
    this.name = "DealingError";
    this.code = code;
  }
}

const MESSAGES: Record<DealingErrorCode, string> = {
  not_found: "This order no longer exists.",
  conflict: "Someone else has already acted on this order. The desk has been reloaded.",
  same_person: "A second person has to do this. You prepared or recorded it yourself.",
  not_a_whole_lot: "The face value must be a whole number of lots.",
  more_than_held: "That is more than the desk holds in this product.",
  over_the_order: "The result is larger than the order that was approved.",
  above_face: "A Treasury bill never costs or fetches more than its face value. Check the cash amount.",
  settles_in_the_future: "A result is recorded on or after its settlement date.",
  forbidden: "You are not allowed to work on the dealing desk.",
  not_connected: "The dealing desk is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function dealingErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof DealingError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new DealingError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedDealingApi: DealingApi = {
  getDesk: notConnected,
  prepareOrder: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
  recordResult: notConnected,
  confirmResult: notConnected,
  rejectResult: notConnected,
};
