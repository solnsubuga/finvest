import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiBondsClient } from "../apiBondsClient";
import { resolveBondsClient } from "../resolveBonds";
import { BondsError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

const HOLDING = "H".repeat(20);
const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

const bond = {
  code: "BOND-10Y-2037",
  isin: "UG12K1405378",
  name: "10 Year Treasury Bond 2037",
  currency: "UGX",
  coupon_percent: "14.40",
  issue_date: "2026-11-14",
  maturity_date: "2037-05-14",
  years: 10,
  next_coupon_date: "2027-05-14",
  coupons_left: 21,
  minimum_face_minor: 100_000,
  face_step_minor: 100_000,
  price: 985_000,
  status: "live",
  can_buy: true,
};

const quote = {
  code: "BOND-10Y-2037",
  currency: "UGX",
  face_minor: 1_000_000,
  price: 985_000,
  accrued_minor: 883,
  cost_minor: 985_883,
  coupon_minor: 72_000,
  coupons_left: 21,
  next_coupon_date: "2027-05-14",
  total_back_minor: 2_512_000,
  gain_minor: 1_526_117,
};

const holding = {
  id: HOLDING,
  code: "BOND-10Y-2037",
  name: "10 Year Treasury Bond 2037",
  currency: "UGX",
  face_minor: 1_000_000,
  price: 985_000,
  accrued_minor: 883,
  cost_minor: 985_883,
  status: "unpaid",
  created_at: "2026-11-16T09:00:00Z",
  pay_by: "2026-11-17T09:00:00Z",
  start_date: null,
  maturity_date: "2037-05-14",
  coupon_minor: 72_000,
  coupons_left: 21,
  next_coupon_date: "2027-05-14",
  still_to_come_minor: 2_512_000,
  payout_destination_id: null,
};

type Seen = { method: string; url: string; headers: Record<string, string>; body: unknown };

function serve(routes: Record<string, () => Response>): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const method = init?.method ?? "GET";
    const path = String(url).slice(API_URL.length);
    seen.push({ method, url: path, headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body });
    const answer = routes[`${method} ${path}`];
    if (!answer) throw new Error(`unexpected ${method} ${path}`);
    return answer();
  });
  return seen;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });
const refused = (code: string, status: number, fields: unknown[] = []) =>
  json({ request_id: "r1", error: { code, message: code, fields } }, status);

describe("the bonds client for the real API", () => {
  it("reads the issues with the coupon after tax and today's price", async () => {
    serve({ "GET /client/bonds": json([bond]) });
    const [found] = await createApiBondsClient().listBonds();
    expect(found).toEqual({
      code: "BOND-10Y-2037",
      isin: "UG12K1405378",
      name: "10 Year Treasury Bond 2037",
      currency: "UGX",
      couponPercent: "14.40",
      issueDate: "2026-11-14",
      maturityDate: "2037-05-14",
      years: 10,
      nextCouponDate: "2027-05-14",
      couponsLeft: 21,
      minimumFace: ugx(100_000),
      faceStep: ugx(100_000),
      price: 985_000,
      status: "live",
      canBuy: true,
    });
  });

  it("quotes a face value with the backend's figures", async () => {
    const seen = serve({ "GET /client/bonds/BOND-10Y-2037/quote?face_minor=1000000": json(quote) });
    await expect(createApiBondsClient().quote("BOND-10Y-2037", ugx(1_000_000))).resolves.toEqual({
      code: "BOND-10Y-2037",
      face: ugx(1_000_000),
      price: 985_000,
      accrued: ugx(883),
      cost: ugx(985_883),
      coupon: ugx(72_000),
      couponsLeft: 21,
      nextCouponDate: "2027-05-14",
      totalBack: ugx(2_512_000),
      gain: ugx(1_526_117),
    });
    expect(seen).toHaveLength(1);
  });

  it("words the quote's refusals by the field code", async () => {
    const field = (code: string) => [{ field_path: ["face_minor"], code }];
    for (const [code, expected] of [
      ["below_minimum", "below_minimum"],
      ["not_a_step", "not_a_step"],
      ["above_maximum", "above_maximum"],
      ["too_close_to_maturity", "too_close_to_maturity"],
      ["no_price", "no_price"],
      ["product_not_open", "not_open"],
      ["something_new", "invalid"],
    ] as const) {
      serve({ "GET /client/bonds/BOND-10Y-2037/quote?face_minor=150000": refused("validation_failed", 422, field(code)) });
      await expect(createApiBondsClient().quote("BOND-10Y-2037", ugx(150_000))).rejects.toEqual(new BondsError(expected));
    }
  });

  it("buys with the review screen's key, and words a refusal", async () => {
    const seen = serve({ "POST /client/bond-holdings": json(holding, 201) });
    const bought = await createApiBondsClient().buy({ code: "BOND-10Y-2037", face: ugx(1_000_000), idempotencyKey: "key-1" });
    expect(bought).toMatchObject({ id: HOLDING, status: "unpaid", cost: ugx(985_883), couponDestinationId: null });
    expect(JSON.parse(String(seen[0]!.body))).toEqual({ code: "BOND-10Y-2037", face_minor: 1_000_000, currency: "UGX" });
    expect(seen[0]!.headers["Idempotency-Key"]).toBe("key-1");

    serve({ "POST /client/bond-holdings": refused("conflict", 409) });
    await expect(
      createApiBondsClient().buy({ code: "BOND-10Y-2037", face: ugx(1_000_000), idempotencyKey: "key-2" }),
    ).rejects.toEqual(new BondsError("too_many_unpaid"));
    serve({ "POST /client/bond-holdings": refused("forbidden", 403) });
    await expect(
      createApiBondsClient().buy({ code: "BOND-10Y-2037", face: ugx(1_000_000), idempotencyKey: "key-3" }),
    ).rejects.toEqual(new BondsError("not_verified"));
  });

  it("reads a holding", async () => {
    serve({ [`GET /client/bond-holdings/${HOLDING}`]: json({ ...holding, status: "active", pay_by: null, start_date: "2026-11-16" }) });
    await expect(createApiBondsClient().getHolding(HOLDING)).resolves.toMatchObject({
      status: "active",
      startDate: "2026-11-16",
      stillToCome: ugx(2_512_000),
    });
  });

  it("uses the demo only when it is loaded", () => {
    const demo = { createStubBondsClient: jest.fn(() => ({ demo: true })) };
    expect(resolveBondsClient(demo as never)).toEqual({ demo: true });
    expect(demo.createStubBondsClient).toHaveBeenCalledWith({ failList: false });
    expect(resolveBondsClient(null)).not.toEqual({ demo: true });
  });
});
