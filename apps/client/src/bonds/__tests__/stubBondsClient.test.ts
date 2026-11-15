import { demoPaidAt, markDemoPaid } from "@/demo/payables";
import { demoPayable } from "@/demo/payables";

import { createStubBondsClient } from "../stubBondsClient";
import { bondPayableId, BondsError, holdingIdOf } from "../types";

const ugx = (minor: number) => ({ currency: "UGX" as const, minor });
/** 16 November 2026, 12:00 in Kampala: the day of the worked example in docs/bonds.md. */
const ON_16_NOV = Date.parse("2026-11-16T09:00:00Z");

function client(now = ON_16_NOV) {
  const clock = { ms: now };
  return { clock, bonds: createStubBondsClient({ delayMs: 0, now: () => clock.ms }) };
}

describe("the demo bonds", () => {
  it("quotes the worked example in docs/bonds.md", async () => {
    const { bonds } = client();
    await expect(bonds.quote("BOND-10Y-2037", ugx(1_000_000))).resolves.toEqual({
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
  });

  it("lists live and paused issues, and says which can be bought", async () => {
    const { bonds } = client();
    const listed = await bonds.listBonds();
    expect(listed.map((bond) => [bond.code, bond.canBuy])).toEqual([
      ["BOND-10Y-2037", true],
      ["BOND-5Y-2031", true],
      ["BOND-2Y-2028", false],
    ]);
    expect(listed.find((bond) => bond.code === "BOND-5Y-2031")).toMatchObject({ nextCouponDate: "2027-02-22", couponsLeft: 10 });
    expect(JSON.stringify(listed)).not.toMatch(/taxBps|couponBps/);
  });

  it("refuses face values the backend refuses", async () => {
    const { bonds } = client();
    await expect(bonds.quote("BOND-10Y-2037", ugx(50_000))).rejects.toEqual(new BondsError("below_minimum"));
    await expect(bonds.quote("BOND-10Y-2037", ugx(150_000))).rejects.toEqual(new BondsError("not_a_step"));
    await expect(bonds.quote("BOND-2Y-2028", ugx(100_000))).rejects.toEqual(new BondsError("not_open"));
    await expect(bonds.quote("NOPE", ugx(100_000))).rejects.toEqual(new BondsError("not_found"));
  });

  it("is too close to maturity to buy once the last coupon period has begun", async () => {
    const { bonds } = client(Date.parse("2037-01-10T09:00:00Z"));
    await expect(bonds.getBond("BOND-10Y-2037")).resolves.toMatchObject({ canBuy: false, couponsLeft: 1 });
    await expect(bonds.quote("BOND-10Y-2037", ugx(100_000))).rejects.toEqual(new BondsError("too_close_to_maturity"));
  });

  it("buys once per key, waits for its payment, and starts once paid", async () => {
    const { bonds, clock } = client();
    const request = { code: "BOND-10Y-2037", face: ugx(1_000_000), idempotencyKey: "key-1" };
    const bought = await bonds.buy(request);
    expect(bought).toMatchObject({ status: "unpaid", cost: ugx(985_883), payBy: "2026-11-17T09:00:00.000Z" });
    await expect(bonds.buy(request)).resolves.toEqual(bought);
    await expect(bonds.buy({ ...request, face: ugx(200_000) })).rejects.toEqual(new BondsError("key_reused"));
    expect(demoPayable(bondPayableId(bought.id))).toEqual({
      id: `bond~${bought.id}`,
      kind: "bond",
      title: "10 Year Treasury Bond 2037",
      amount: ugx(985_883),
    });

    markDemoPaid(bondPayableId(bought.id), new Date(clock.ms + 60_000).toISOString());
    expect(demoPaidAt(bondPayableId(bought.id))).not.toBeNull();
    await expect(bonds.getHolding(bought.id)).resolves.toMatchObject({ status: "active", startDate: "2026-11-16", payBy: null });
  });

  it("lets an unpaid purchase lapse after 24 hours, and holds at most five waiting", async () => {
    const { bonds, clock } = client();
    for (let index = 0; index < 5; index += 1) {
      await bonds.buy({ code: "BOND-5Y-2031", face: ugx(100_000), idempotencyKey: `wait-${index}` });
    }
    await expect(
      bonds.buy({ code: "BOND-5Y-2031", face: ugx(100_000), idempotencyKey: "wait-6" }),
    ).rejects.toEqual(new BondsError("too_many_unpaid"));
    clock.ms += 25 * 60 * 60 * 1000;
    await expect(bonds.listHoldings()).resolves.toEqual([]);
  });
});

describe("pay screen IDs for bonds", () => {
  it("tell a holding from an investment", () => {
    expect(bondPayableId("abc")).toBe("bond~abc");
    expect(holdingIdOf("bond~abc")).toBe("abc");
    expect(holdingIdOf("abc")).toBeNull();
  });
});
