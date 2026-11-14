/**
 * The console's view of the Treasury bill auction calendar (backend/app/modules/dealing/auctions_api.py).
 *
 * Staff with `dealing_orders.change` enter each auction date the Bank of Uganda publishes, and
 * cancel one with a reason if the Bank moves it. One person does either, and both are audited.
 * The desk shows each bill's earliest standing date as its next auction.
 */

export interface Auction {
  id: string;
  productCode: string;
  /** The day of the auction, as a calendar date. */
  auctionDate: string;
  /** A name where the directory knows one, otherwise the staff ID. */
  addedBy: string;
  addedAt: string;
  cancelledBy: string | null;
  cancelledAt: string | null;
  cancelReason: string | null;
}

export interface AuctionCalendar {
  /** From a month ago on, cancelled dates included, earliest first. */
  auctions: readonly Auction[];
  /** False for a reader who may not enter or cancel a date. */
  mayChange: boolean;
}

export interface AuctionsApi {
  getCalendar(): Promise<AuctionCalendar>;
  /** Every command answers with the calendar as it now stands. The key makes a retry safe. */
  add(
    key: string,
    productCode: string,
    auctionDate: string,
  ): Promise<AuctionCalendar>;
  cancel(
    key: string,
    auctionId: string,
    reason: string,
  ): Promise<AuctionCalendar>;
}

export type AuctionErrorCode =
  | "auction_date_passed"
  | "auction_already_entered"
  | "not_a_treasury_bill"
  | "unknown_product"
  | "reason_required"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "invalid"
  | "not_connected"
  | "unavailable";

export class AuctionError extends Error {
  readonly code: AuctionErrorCode;

  constructor(code: AuctionErrorCode) {
    super(code);
    this.name = "AuctionError";
    this.code = code;
  }
}

const MESSAGES: Record<AuctionErrorCode, string> = {
  auction_date_passed: "That day has passed. Enter today or a later day.",
  auction_already_entered:
    "That auction is already in the calendar for this bill.",
  not_a_treasury_bill: "Only Treasury bills are bought at auction.",
  unknown_product: "Finvest does not offer that product.",
  reason_required: "Say why the date is cancelled, such as the Bank moving it.",
  conflict:
    "Someone else cancelled this date first. The calendar now shows it.",
  not_found: "This date is no longer in the calendar.",
  forbidden: "Your role cannot change the auction calendar.",
  invalid: "Check the bill and the day, then try again.",
  not_connected:
    "The auction calendar is not connected to the Finvest service yet.",
  unavailable:
    "We could not reach Finvest. Check your connection and try again.",
};

export function auctionErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof AuctionError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new AuctionError("not_connected"));

/** Used in builds with no backend and the stub turned off. */
export const notConnectedAuctionsApi: AuctionsApi = {
  getCalendar: notConnected,
  add: notConnected,
  cancel: notConnected,
};
