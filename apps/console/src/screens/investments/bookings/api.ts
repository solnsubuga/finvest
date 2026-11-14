/**
 * Booking an investment for a client who asked: `/staff/investment-bookings` in
 * backend/app/modules/investments/api.py (see "Booking for a client" in docs/investing.md).
 *
 * One person books it with a reason, a different person approves or sends it back, and only
 * the booker withdraws it. Once approved, Finvest saves it unpaid on the day's terms and the
 * client pays for it in the app as usual. `http.ts` calls the routes; `stub.ts` is the sample
 * for `pnpm dev` and previews.
 */

import type { Amount, Currency } from "../../products/api.ts";
import type { AtMaturity } from "../api.ts";

export type BookingStatus = "proposed" | "approved" | "sent_back" | "withdrawn" | "done" | "failed";

export interface Booking {
  id: string;
  clientId: string;
  productCode: string;
  productName: string;
  amount: Amount;
  /** Null for a product without a fixed term. */
  atMaturity: AtMaturity | null;
  reason: string;
  status: BookingStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was sent back. */
  decisionNote: string | null;
  /** The investment Finvest saved, once done. */
  investmentId: string | null;
  /** Why it could not be saved, in the words of a client's own refusal. */
  failureCode: string | null;
  finishedAt: string | null;
}

export interface NewBooking {
  clientId: string;
  productCode: string;
  amount: Amount;
  atMaturity: AtMaturity | null;
  reason: string;
}

/** A product a client can invest in today, for the booking form. */
export interface BookableProduct {
  code: string;
  name: string;
  currency: Currency;
  termDays: number | null;
  minimum: Amount;
}

/** Each command takes an idempotency key, kept per attempt by the screen. */
export interface BookingsApi {
  /** Newest first. */
  list(): Promise<readonly Booking[]>;
  /** Products that take new money today. */
  products(): Promise<readonly BookableProduct[]>;
  propose(key: string, booking: NewBooking): Promise<Booking>;
  approve(key: string, bookingId: string): Promise<Booking>;
  sendBack(key: string, bookingId: string, note: string): Promise<Booking>;
  withdraw(key: string, bookingId: string): Promise<Booking>;
}

export type BookingField = "client_id" | "product_code" | "amount" | "at_maturity" | "reason" | "note";

export interface BookingProblem {
  field: BookingField;
  code: string;
}

export type BookingsErrorCode =
  | "invalid"
  | "conflict"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class BookingsError extends Error {
  constructor(
    readonly code: BookingsErrorCode,
    readonly problems: readonly BookingProblem[] = [],
  ) {
    super(code);
    this.name = "BookingsError";
  }
}

const MESSAGES: Record<BookingsErrorCode, string> = {
  invalid: "Finvest refused this. Fix what is marked and try again.",
  conflict: "This booking was decided already. Here is the list as it now stands.",
  same_person: "Someone else has to decide this. You booked it, so you can only withdraw it.",
  forbidden: "You are not allowed to do this. Ask an admin.",
  not_found: "Finvest could not find that client or product. The client must have passed their identity check, and the product must take new money.",
  not_connected: "Booking for a client is not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function bookingsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof BookingsError ? error.code : "unavailable"];
}

const PROBLEMS: Record<string, string> = {
  required: "Fill this in.",
  too_long: "Keep it to 500 characters.",
  below_minimum: "This is below the product's minimum.",
  amount_not_positive: "Write an amount above zero.",
  currency_not_supported: "This product takes another currency.",
  product_not_open: "This product is not taking new money.",
  open_term_has_no_maturity: "This product has no fixed term, so there is nothing to choose.",
};

export function bookingProblemText(code: string): string {
  return PROBLEMS[code] ?? "Finvest did not accept this. Change it and try again.";
}

const FAILURES: Record<string, string> = {
  too_many_unpaid: "The client already had five investments waiting for payment.",
  closing: "The client's account is being checked for closing.",
  client_not_able: "The client can no longer invest.",
  product_not_open: "The product stopped taking new money.",
  below_minimum: "The amount was below the product's minimum by then.",
  not_found: "The product had no rate for the day.",
};

/** Why a booking could not be saved, for the card. */
export function bookingFailureText(code: string | null): string {
  return (code && FAILURES[code]) ?? "Finvest could not save it.";
}

const notConnected = () => Promise.reject(new BookingsError("not_connected"));

export const notConnectedBookingsApi: BookingsApi = {
  list: notConnected,
  products: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
