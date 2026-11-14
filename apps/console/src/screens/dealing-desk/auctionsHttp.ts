/** The auction calendar over the real API (backend/app/modules/dealing/auctions_api.py). */

import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import type { CalendarOut } from "@finvest/api-client/staff";
import {
  AuctionError,
  type AuctionCalendar,
  type AuctionErrorCode,
  type AuctionsApi,
} from "./auctions.ts";

const PATH = "/staff/dealing/auctions";

export async function toCalendar(
  json: CalendarOut,
  directory: Directory,
): Promise<AuctionCalendar> {
  const ids = new Set<string>();
  for (const auction of json.auctions) {
    ids.add(auction.added_by);
    if (auction.cancelled_by) ids.add(auction.cancelled_by);
  }
  const names = new Map<string, string>();
  await Promise.all(
    [...ids].map(async (id) => {
      names.set(id, (await directory.staffName(id).catch(() => null)) ?? id);
    }),
  );
  const named = (id: string) => names.get(id) ?? id;
  return {
    auctions: json.auctions.map((a) => ({
      id: a.id,
      productCode: a.product_code,
      auctionDate: a.auction_date,
      addedBy: named(a.added_by),
      addedAt: a.added_at,
      cancelledBy: a.cancelled_by ? named(a.cancelled_by) : null,
      cancelledAt: a.cancelled_at,
      cancelReason: a.cancel_reason,
    })),
    mayChange: json.may_change,
  };
}

const FIELD_CODES = new Set<AuctionErrorCode>([
  "auction_date_passed",
  "auction_already_entered",
  "not_a_treasury_bill",
  "unknown_product",
  "reason_required",
]);

export function toAuctionError(error: unknown): AuctionError {
  if (error instanceof AuctionError) return error;
  if (!(error instanceof ApiError)) return new AuctionError("unavailable");
  const field = error.fieldCode;
  if (field && FIELD_CODES.has(field as AuctionErrorCode))
    return new AuctionError(field as AuctionErrorCode);
  switch (error.code) {
    case "conflict":
    case "not_found":
    case "forbidden":
      return new AuctionError(error.code);
    case "validation_failed":
      return new AuctionError("invalid");
    default:
      return new AuctionError("unavailable");
  }
}

export function createHttpAuctionsApi(
  http: Http,
  directory: Directory,
): AuctionsApi {
  async function call(
    method: "GET" | "POST",
    path: string,
    key?: string,
    body?: unknown,
  ): Promise<AuctionCalendar> {
    let json: CalendarOut;
    try {
      json =
        key === undefined
          ? await http.request<CalendarOut>(method, path)
          : await http.request<CalendarOut>(method, path, body, {
              idempotencyKey: key,
            });
    } catch (error) {
      throw toAuctionError(error);
    }
    return toCalendar(json, directory);
  }
  return {
    getCalendar: () => call("GET", PATH),
    add: (key, productCode, auctionDate) =>
      call("POST", PATH, key, {
        product_code: productCode,
        auction_date: auctionDate,
      }),
    cancel: (key, auctionId, reason) =>
      call("POST", `${PATH}/${encodeURIComponent(auctionId)}/cancel`, key, {
        reason,
      }),
  };
}
