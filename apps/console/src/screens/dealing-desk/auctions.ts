/**
 * The console's view of the Treasury bill auction calendar (backend/app/modules/dealing/auctions_api.py).
 *
 * Staff with `dealing_orders.change` keep the dates the Bank of Uganda publishes. Every change takes
 * two people: one proposes a date, or cancelling a standing one with a reason, and a different
 * person approves it or declines it with a note. The proposer may withdraw it while it waits. The
 * desk shows each bill's earliest standing date as its next auction.
 */

export type AuctionStep = "approve" | "decline" | "withdraw" | "cancel";
export type ChangeStatus = "proposed" | "approved" | "declined" | "withdrawn";
/** waiting: for a second person. standing: counts on the desk. cancelling: still counts while a cancellation waits. */
export type AuctionState =
  | "waiting"
  | "standing"
  | "cancelling"
  | "cancelled"
  | "declined"
  | "withdrawn";

export interface Cancellation {
  id: string;
  reason: string;
  status: ChangeStatus;
  /** A name where the directory knows one, otherwise the staff ID. */
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  allowed: readonly AuctionStep[];
}

export interface Auction {
  id: string;
  productCode: string;
  /** The day of the auction, as a calendar date. */
  auctionDate: string;
  state: AuctionState;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  /** The cancellation waiting or approved, else the latest one declined or withdrawn. */
  cancellation: Cancellation | null;
  allowed: readonly AuctionStep[];
}

export interface AuctionCalendar {
  /** From a month ago on, and any date still waiting, earliest first. */
  auctions: readonly Auction[];
  /** False for a reader who may not propose or decide a change. */
  mayChange: boolean;
}

export interface AuctionsApi {
  getCalendar(): Promise<AuctionCalendar>;
  /** Every command answers with the calendar as it now stands. The key makes a retry safe. */
  propose(
    key: string,
    productCode: string,
    auctionDate: string,
  ): Promise<AuctionCalendar>;
  proposeCancel(
    key: string,
    auctionId: string,
    reason: string,
  ): Promise<AuctionCalendar>;
  /** A change is a proposed date or a proposed cancellation, by its ID. */
  approve(key: string, changeId: string): Promise<AuctionCalendar>;
  decline(
    key: string,
    changeId: string,
    note: string,
  ): Promise<AuctionCalendar>;
  withdraw(key: string, changeId: string): Promise<AuctionCalendar>;
}

export type AuctionErrorCode =
  | "auction_date_passed"
  | "auction_already_entered"
  | "not_a_treasury_bill"
  | "unknown_product"
  | "reason_required"
  | "note_required"
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
  auction_date_passed: "That day has passed. Propose today or a later day.",
  auction_already_entered:
    "That auction is already in the calendar for this bill, or waiting for a second person.",
  not_a_treasury_bill: "Only Treasury bills are bought at auction.",
  unknown_product: "Finvest does not offer that product.",
  reason_required: "Say why the date is cancelled, such as the Bank moving it.",
  note_required:
    "Say why you are declining it, so the person who proposed it knows.",
  conflict: "Someone else dealt with this first. The calendar now shows it.",
  not_found: "This change is no longer in the calendar.",
  forbidden:
    "You cannot take this step. A change is approved by someone other than the person who proposed it.",
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
  propose: notConnected,
  proposeCancel: notConnected,
  approve: notConnected,
  decline: notConnected,
  withdraw: notConnected,
};
