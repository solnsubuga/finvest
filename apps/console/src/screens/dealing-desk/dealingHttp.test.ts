import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDealingApi, toDealingError, type DeskJson } from "./dealingHttp.ts";

const ugx = (minor: number) => ({ minor, currency: "UGX" as const });
const PREPARER = "-Nstaffprep000000000";
const names: Directory = { ...emptyDirectory, staffName: (id) => Promise.resolve(id === PREPARER ? "Olga Ops" : null) };

const json: DeskJson = {
  money_to_place: ugx(5_000_000),
  bills_held: ugx(20_000_000),
  matches_custodian: null,
  buffer: {
    balance: ugx(3_000_000),
    target: ugx(5_000_000),
    floor: ugx(1_000_000),
    level: "under_target",
    from_buffer_up_to: ugx(500_000),
    pay_within_working_days: 3,
    after_promised: ugx(2_500_000),
    short: ugx(0),
    to_raise: ugx(0),
  },
  early_withdrawals: { asked: ugx(500_000), clients: 1 },
  positions: [
    {
      product_code: "TREA-91",
      product_name: "91 Day Treasury Bill",
      discount_only: true,
      lot_size: ugx(100_000),
      clients_owed: ugx(25_000_000),
      bills_held: ugx(20_000_000),
      to_buy: ugx(5_000_000),
      on_order: ugx(0),
      face_held: ugx(20_000_000),
      cover: "bid_needed",
    },
  ],
  orders: [
    {
      id: "-Norder0000000000000",
      kind: "auction_bid",
      product_code: "TREA-91",
      product_name: "91 Day Treasury Bill",
      face: ugx(5_000_000),
      status: "prepared",
      prepared_by: PREPARER,
      prepared_at: "2026-11-14T08:00:00Z",
      note: null,
      result: null,
      result_recorded_by: "-Nstaffunknown000000",
      allowed: ["approve", "send_back"],
    },
  ],
};

function api() {
  const request = vi.fn().mockResolvedValue(json);
  return { request, api: createHttpDealingApi({ request, download: vi.fn() } as unknown as Http, names) };
}

describe("the dealing desk over the API", () => {
  it("reads the desk in shillings and names the staff", async () => {
    const { request, api: desk } = api();
    const answer = await desk.getDesk();
    expect(request).toHaveBeenCalledWith("GET", "/staff/dealing/desk?currency=UGX");
    expect(answer.moneyToPlace).toEqual(ugx(5_000_000));
    expect(answer.buffer.fromBufferUpTo).toEqual(ugx(500_000));
    expect(answer.positions[0]).toMatchObject({ productCode: "TREA-91", toBuy: ugx(5_000_000), cover: "bid_needed", nextAuction: null });
    expect(answer.orders[0]).toMatchObject({ preparedBy: "Olga Ops", resultRecordedBy: "-Nstaffunknown000000", allowed: ["approve", "send_back"] });
  });

  it("sends an order with its key", async () => {
    const { request, api: desk } = api();
    await desk.prepareOrder("k-1", { kind: "auction_bid", productCode: "TREA-91", face: ugx(5_000_000) });
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/staff/dealing/orders",
      { kind: "auction_bid", product_code: "TREA-91", currency: "UGX", face_minor: 5_000_000 },
      { idempotencyKey: "k-1" },
    );
  });

  it("sends each step to its own route", async () => {
    const { request, api: desk } = api();
    await desk.approve("k-2", "o 1");
    await desk.sendBack("k-3", "o1", "Wrong lot");
    await desk.recordResult("k-4", "o1", {
      face: ugx(5_000_000),
      amount: ugx(4_850_000),
      settledOn: "2026-11-18",
      maturesOn: "2027-02-17",
      custodianReference: "BOU-1",
    });
    await desk.recordResult("k-5", "o1", null);
    await desk.rejectResult("k-6", "o1", "Check the amount");
    expect(request.mock.calls.map((call) => [call[1], call[2], call[3]])).toEqual([
      ["/staff/dealing/orders/o%201/approve", undefined, { idempotencyKey: "k-2" }],
      ["/staff/dealing/orders/o1/send-back", { note: "Wrong lot" }, { idempotencyKey: "k-3" }],
      [
        "/staff/dealing/orders/o1/result",
        {
          fill: {
            face_minor: 5_000_000,
            amount_minor: 4_850_000,
            settled_on: "2026-11-18",
            matures_on: "2027-02-17",
            custodian_reference: "BOU-1",
          },
        },
        { idempotencyKey: "k-4" },
      ],
      ["/staff/dealing/orders/o1/result", { fill: null }, { idempotencyKey: "k-5" }],
      ["/staff/dealing/orders/o1/reject-result", { note: "Check the amount" }, { idempotencyKey: "k-6" }],
    ]);
  });

  it("reads the backend's refusals as the screen's own", () => {
    const field = (code: string) => new ApiError("validation_failed", 422, [{ field_path: ["face"], code }]);
    expect(toDealingError(field("not_a_whole_lot")).code).toBe("not_a_whole_lot");
    expect(toDealingError(field("above_face")).code).toBe("above_face");
    expect(toDealingError(field("not_enough_cash")).code).toBe("not_enough_cash");
    expect(toDealingError(field("day_closed")).code).toBe("day_closed");
    expect(toDealingError(field("note_required")).code).toBe("invalid");
    expect(toDealingError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toDealingError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    expect(toDealingError(new TypeError("x")).code).toBe("unavailable");
  });
});
