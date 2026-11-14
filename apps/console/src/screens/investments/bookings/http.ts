/** Bookings over the real API: `/staff/investment-bookings`, with products from `/staff/products`. */

import type { BookingOut } from "@finvest/api-client/staff";
import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import type { CatalogueApi } from "../../products/api.ts";
import { BookingsError, type Booking, type BookingField, type BookingProblem, type BookingsApi } from "./api.ts";

export function toBooking(json: BookingOut): Booking {
  return {
    id: json.id,
    clientId: json.client_id,
    productCode: json.product_code,
    productName: json.product_name,
    amount: { minor: json.amount_minor, currency: json.currency },
    atMaturity: json.at_maturity,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
    investmentId: json.investment_id,
    failureCode: json.failure_code,
    finishedAt: json.finished_at,
  };
}

const FIELDS: readonly string[] = ["client_id", "product_code", "amount", "at_maturity", "reason", "note"] satisfies BookingField[];

function problems(error: ApiError): BookingProblem[] {
  return error.fields.flatMap((problem) => {
    const last = problem.field_path[problem.field_path.length - 1];
    const field = last === "amount_minor" ? "amount" : last;
    const code =
      problem.code === "string_too_long" ? "too_long" : problem.code === "missing" || problem.code === "string_too_short" ? "required" : problem.code;
    return typeof field === "string" && FIELDS.includes(field) ? [{ field: field as BookingField, code }] : [];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the booker tried to decide. */
export function toBookingsError(error: unknown, deciding = false): BookingsError {
  if (error instanceof BookingsError) return error;
  if (!(error instanceof ApiError)) return new BookingsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new BookingsError("invalid", problems(error));
    case "conflict":
      return new BookingsError("conflict");
    case CONFIRM_IT_IS_YOU:
      return new BookingsError("not_confirmed");
    case "forbidden":
      return new BookingsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new BookingsError("not_found");
    default:
      return new BookingsError("unavailable");
  }
}

const PATH = "/staff/investment-bookings";

export function createHttpBookingsApi(http: Http, catalogue: CatalogueApi): BookingsApi {
  async function command(key: string, path: string, body: unknown, deciding: boolean): Promise<Booking> {
    try {
      return toBooking(await http.request<BookingOut>("POST", path, body, { idempotencyKey: key }));
    } catch (error) {
      throw toBookingsError(error, deciding);
    }
  }
  const one = (id: string, step: string) => `${PATH}/${encodeURIComponent(id)}/${step}`;
  return {
    async list() {
      try {
        return (await http.request<BookingOut[]>("GET", `${PATH}?limit=50`)).map(toBooking);
      } catch (error) {
        throw toBookingsError(error);
      }
    },
    async products() {
      try {
        return (await catalogue.listProducts())
          .filter((product) => product.status === "live" && product.rate !== null)
          .map(({ code, name, currency, termDays, minimum }) => ({ code, name, currency, termDays, minimum }));
      } catch {
        throw new BookingsError("unavailable");
      }
    },
    propose: (key, booking) =>
      command(
        key,
        PATH,
        {
          client_id: booking.clientId,
          product_code: booking.productCode,
          amount_minor: booking.amount.minor,
          currency: booking.amount.currency,
          at_maturity: booking.atMaturity,
          reason: booking.reason,
        },
        false,
      ),
    approve: (key, id) => command(key, one(id, "approve"), undefined, true),
    sendBack: (key, id, note) => command(key, one(id, "send-back"), { note }, true),
    withdraw: (key, id) => command(key, one(id, "withdraw"), undefined, false),
  };
}
