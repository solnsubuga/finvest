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
  for (const a of json.auctions) {
    for (const id of [
      a.proposed_by,
      a.decided_by,
      a.cancellation?.proposed_by,
      a.cancellation?.decided_by,
    ])
      if (id) ids.add(id);
  }
  const names = new Map<string, string>();
  await Promise.all(
    [...ids].map(async (id) => {
      names.set(id, (await directory.staffName(id).catch(() => null)) ?? id);
    }),
  );
  const named = (id: string) => names.get(id) ?? id;
  const maybe = (id: string | null) => (id ? named(id) : null);
  return {
    auctions: json.auctions.map((a) => ({
      id: a.id,
      productCode: a.product_code,
      auctionDate: a.auction_date,
      state: a.state,
      proposedBy: named(a.proposed_by),
      proposedAt: a.proposed_at,
      decidedBy: maybe(a.decided_by),
      decidedAt: a.decided_at,
      decisionNote: a.decision_note,
      cancellation: a.cancellation
        ? {
            id: a.cancellation.id,
            reason: a.cancellation.reason,
            status: a.cancellation.status,
            proposedBy: named(a.cancellation.proposed_by),
            proposedAt: a.cancellation.proposed_at,
            decidedBy: maybe(a.cancellation.decided_by),
            decidedAt: a.cancellation.decided_at,
            decisionNote: a.cancellation.decision_note,
            allowed: a.cancellation.allowed,
          }
        : null,
      allowed: a.allowed,
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
  "note_required",
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
    // Includes the person closing the "confirm it is you" dialog: nothing was done, and they can press again.
    default:
      return new AuctionError("unavailable");
  }
}

export function createHttpAuctionsApi(
  http: Http,
  directory: Directory,
): AuctionsApi {
  async function call(
    path: string,
    key?: string,
    body?: unknown,
  ): Promise<AuctionCalendar> {
    let json: CalendarOut;
    try {
      json =
        key === undefined
          ? await http.request<CalendarOut>("GET", path)
          : await http.request<CalendarOut>("POST", path, body, {
              idempotencyKey: key,
            });
    } catch (error) {
      throw toAuctionError(error);
    }
    return toCalendar(json, directory);
  }
  const change = (id: string, step: string) =>
    `${PATH}/changes/${encodeURIComponent(id)}/${step}`;
  return {
    getCalendar: () => call(PATH),
    propose: (key, productCode, auctionDate) =>
      call(PATH, key, { product_code: productCode, auction_date: auctionDate }),
    proposeCancel: (key, auctionId, reason) =>
      call(`${PATH}/${encodeURIComponent(auctionId)}/cancel`, key, { reason }),
    approve: (key, id) => call(change(id, "approve"), key),
    decline: (key, id, note) => call(change(id, "decline"), key, { note }),
    withdraw: (key, id) => call(change(id, "withdraw"), key),
  };
}
