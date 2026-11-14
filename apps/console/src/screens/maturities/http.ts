/**
 * Maturities over the real API: `GET /staff/maturities` in backend/app/modules/investments/api.py.
 * The investments in it have the staff investment shape, so they are read the way the
 * Investments screen reads them.
 */

import type { MaturitiesOut, MaturityDayOut } from "@finvest/api-client/staff";
import { ApiError, type Http } from "@/api/http.ts";
import { toInvestment } from "../investments/http.ts";
import { MaturitiesError, type Maturities, type MaturitiesApi, type MaturityDay } from "./api.ts";

export function toMaturityDay(json: MaturityDayOut): MaturityDay {
  return {
    day: json.day,
    currency: json.currency,
    investments: json.investments,
    principal: { minor: json.principal_minor, currency: json.currency },
    rolledOver: json.rolled_over,
    paidOut: json.paid_out,
    held: json.held,
    openRollOver: json.open_roll_over,
    openPayOut: json.open_pay_out,
  };
}

export function toMaturities(json: MaturitiesOut): Maturities {
  return {
    today: json.today,
    days: json.days.map(toMaturityDay),
    notClosed: json.not_closed.map(toInvestment),
    held: json.held.map(toInvestment),
  };
}

export function toMaturitiesError(error: unknown): MaturitiesError {
  if (error instanceof MaturitiesError) return error;
  if (error instanceof ApiError && error.code === "forbidden") return new MaturitiesError("forbidden");
  return new MaturitiesError("unavailable");
}

export function createHttpMaturitiesApi(http: Http): MaturitiesApi {
  return {
    async getMaturities() {
      try {
        return toMaturities(await http.request<MaturitiesOut>("GET", "/staff/maturities"));
      } catch (error) {
        throw toMaturitiesError(error);
      }
    },
  };
}
