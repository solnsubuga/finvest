/**
 * The dealing desk's position and orders over the real API (backend/app/modules/dealing/api.py).
 * Every command answers with the whole desk, so nothing is refetched after a write.
 *
 * The API names staff by their id. The screen shows names, so each id is looked up in the
 * directory once and the id is kept when the directory does not know it.
 */

import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import {
  DealingError,
  type Amount,
  type BufferLevel,
  type Cover,
  type DealingApi,
  type DealingErrorCode,
  type Desk,
  type Fill,
  type NewOrder,
  type OrderAction,
  type OrderKind,
  type OrderStatus,
} from "./api.ts";

interface FillJson {
  face: Amount;
  amount: Amount;
  settled_on: string;
  matures_on: string;
  custodian_reference: string;
}

interface OrderJson {
  id: string;
  kind: OrderKind;
  product_code: string;
  product_name: string;
  face: Amount;
  status: OrderStatus;
  prepared_by: string;
  prepared_at: string;
  note: string | null;
  result: FillJson | null;
  result_recorded_by: string | null;
  allowed: OrderAction[];
}

interface PositionJson {
  product_code: string;
  product_name: string;
  discount_only: boolean;
  lot_size: Amount;
  clients_owed: Amount;
  bills_held: Amount;
  to_buy: Amount;
  on_order: Amount;
  face_held: Amount;
  cover: Cover;
}

export interface DeskJson {
  money_to_place: Amount;
  bills_held: Amount;
  matches_custodian: boolean | null;
  buffer: {
    balance: Amount;
    target: Amount;
    floor: Amount;
    level: BufferLevel;
    from_buffer_up_to: Amount;
    pay_within_working_days: number;
    after_promised: Amount;
  };
  early_withdrawals: { asked: Amount; clients: number };
  positions: PositionJson[];
  orders: OrderJson[];
}

const fill = (json: FillJson): Fill => ({
  face: json.face,
  amount: json.amount,
  settledOn: json.settled_on,
  maturesOn: json.matures_on,
  custodianReference: json.custodian_reference,
});

export async function toDesk(json: DeskJson, directory: Directory): Promise<Desk> {
  const ids = new Set<string>();
  for (const order of json.orders) {
    ids.add(order.prepared_by);
    if (order.result_recorded_by) ids.add(order.result_recorded_by);
  }
  const names = new Map<string, string>();
  await Promise.all(
    [...ids].map(async (id) => {
      const name = await directory.staffName(id).catch(() => null);
      names.set(id, name ?? id);
    }),
  );
  const named = (id: string) => names.get(id) ?? id;
  return {
    moneyToPlace: json.money_to_place,
    billsHeld: json.bills_held,
    matchesCustodian: json.matches_custodian,
    buffer: {
      balance: json.buffer.balance,
      target: json.buffer.target,
      floor: json.buffer.floor,
      level: json.buffer.level,
      fromBufferUpTo: json.buffer.from_buffer_up_to,
      payWithinWorkingDays: json.buffer.pay_within_working_days,
      afterPromised: json.buffer.after_promised,
    },
    earlyWithdrawals: json.early_withdrawals,
    positions: json.positions.map((p) => ({
      productCode: p.product_code,
      productName: p.product_name,
      discountOnly: p.discount_only,
      lotSize: p.lot_size,
      clientsOwed: p.clients_owed,
      billsHeld: p.bills_held,
      toBuy: p.to_buy,
      onOrder: p.on_order,
      faceHeld: p.face_held,
      cover: p.cover,
      // The API does not send auction dates yet, so a Treasury bill shows "Not set" rather than a date.
      nextAuction: null,
    })),
    orders: json.orders.map((o) => ({
      id: o.id,
      kind: o.kind,
      productCode: o.product_code,
      productName: o.product_name,
      face: o.face,
      status: o.status,
      preparedBy: named(o.prepared_by),
      preparedAt: o.prepared_at,
      note: o.note,
      result: o.result ? fill(o.result) : null,
      resultRecordedBy: o.result_recorded_by ? named(o.result_recorded_by) : null,
      allowed: o.allowed,
    })),
  };
}

const FIELD_CODES = new Set<DealingErrorCode>([
  "not_a_whole_lot",
  "more_than_held",
  "over_the_order",
  "above_face",
  "settles_in_the_future",
  "longer_than_term",
  "not_enough_cash",
  "day_closed",
]);

/** The API's code, or the code of its first field problem, as the screen's own. */
export function toDealingError(error: unknown): DealingError {
  if (error instanceof DealingError) return error;
  if (!(error instanceof ApiError)) return new DealingError("unavailable");
  const field = error.fieldCode;
  if (field && FIELD_CODES.has(field as DealingErrorCode)) return new DealingError(field as DealingErrorCode);
  switch (error.code) {
    case "not_found":
    case "conflict":
      return new DealingError(error.code);
    // The backend refuses a second step by the person who took the first as forbidden.
    case "forbidden":
      return new DealingError("forbidden");
    case "validation_failed":
      return new DealingError("invalid");
    // The person closed the "confirm it is you" dialog, so nothing was done and they can press again.
    case CONFIRM_IT_IS_YOU:
    default:
      return new DealingError("unavailable");
  }
}

export function createHttpDealingApi(http: Http, directory: Directory): DealingApi {
  async function call(path: string, key: string, body?: unknown): Promise<Desk> {
    try {
      const json = await http.request<DeskJson>("POST", `/staff/dealing${path}`, body, { idempotencyKey: key });
      return await toDesk(json, directory);
    } catch (error) {
      throw toDealingError(error);
    }
  }
  const order = (id: string, step: string) => `/orders/${encodeURIComponent(id)}/${step}`;

  return {
    async getDesk() {
      try {
        return await toDesk(await http.request<DeskJson>("GET", "/staff/dealing/desk?currency=UGX"), directory);
      } catch (error) {
        throw toDealingError(error);
      }
    },
    prepareOrder: (key, given: NewOrder) =>
      call("/orders", key, {
        kind: given.kind,
        product_code: given.productCode,
        currency: given.face.currency,
        face_minor: given.face.minor,
      }),
    approve: (key, id) => call(order(id, "approve"), key),
    sendBack: (key, id, note) => call(order(id, "send-back"), key, { note }),
    withdraw: (key, id) => call(order(id, "withdraw"), key),
    recordResult: (key, id, given) =>
      call(order(id, "result"), key, {
        fill: given
          ? {
              face_minor: given.face.minor,
              amount_minor: given.amount.minor,
              settled_on: given.settledOn,
              matures_on: given.maturesOn,
              custodian_reference: given.custodianReference,
            }
          : null,
      }),
    confirmResult: (key, id) => call(order(id, "confirm-result"), key),
    rejectResult: (key, id, note) => call(order(id, "reject-result"), key, { note }),
  };
}
