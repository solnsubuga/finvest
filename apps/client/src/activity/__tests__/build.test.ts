import { buildActivity, byMonth, InvestmentRow, monthHeading, PaymentRow, PayoutRow, shortDay } from "../build";

const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

const investment = (over: Partial<InvestmentRow> = {}): InvestmentRow => ({
  id: "inv-1",
  product_name: "91 day Treasury bill",
  currency: "UGX",
  status: "active",
  maturity_date: "2026-11-20",
  interest_at_maturity_minor: 15_400,
  ...over,
});

const payment = (over: Partial<PaymentRow> = {}): PaymentRow => ({
  id: "pay-1",
  investment_id: "inv-1",
  method: "mtn",
  status: "confirmed",
  amount: ugx(1_000_000),
  created_at: "2026-11-10T08:00:00Z",
  confirmed_at: "2026-11-10T08:01:00Z",
  ...over,
});

const payout = (over: Partial<PayoutRow> = {}): PayoutRow => ({
  id: "out-1",
  amount: ugx(1_015_400),
  kind: "mobile_money",
  status: "paid",
  created_at: "2026-11-12T10:00:00Z",
  ...over,
});

describe("the Activity list", () => {
  it("names the investment a payment was for, and falls back when it is not known", () => {
    const [known] = buildActivity([payment()], [investment()], []);
    expect(known).toMatchObject({ kind: "payment", title: "Paid for 91 day Treasury bill", detail: "MTN Mobile Money", direction: "out", status: "done", statusLabel: "Paid", investmentId: "inv-1" });
    const [unknown] = buildActivity([payment({ investment_id: "other" })], [], []);
    expect(unknown?.title).toBe("Paid for an investment");
  });

  it("dates a payment by when it was confirmed, else when it started", () => {
    expect(buildActivity([payment()], [], [])[0]?.at).toBe("2026-11-10T08:01:00Z");
    expect(buildActivity([payment({ status: "in_review", confirmed_at: null })], [], [])[0]?.at).toBe("2026-11-10T08:00:00Z");
  });

  it("says where every payment and payout stands", () => {
    const statuses = (["created", "sending", "awaiting_approval", "uncertain", "in_review", "confirmed", "failed"] as const).map(
      (status) => buildActivity([payment({ status })], [], [])[0]?.statusLabel,
    );
    expect(statuses).toEqual(["Starting", "Starting", "Waiting for you to approve", "Checking with your network", "Being checked", "Paid", "Did not go through"]);
    const payouts = (["in_review", "processing", "paid", "failed", "cancelled"] as const).map((status) => buildActivity([], [], [payout({ status })])[0]);
    expect(payouts.map((item) => [item?.status, item?.statusLabel])).toEqual([
      ["under_way", "Being checked"],
      ["under_way", "On the way"],
      ["done", "Paid to you"],
      ["failed", "Did not go through"],
      ["failed", "Cancelled"],
    ]);
    expect(buildActivity([], [], [payout({ kind: "bank" })])[0]?.detail).toBe("To your bank account");
  });

  it("lists interest only for a matured investment that earned some, on its maturity day", () => {
    expect(buildActivity([], [investment()], [])).toEqual([]);
    expect(buildActivity([], [investment({ status: "matured", interest_at_maturity_minor: 0 })], [])).toEqual([]);
    expect(buildActivity([], [investment({ status: "matured", interest_at_maturity_minor: null })], [])).toEqual([]);
    expect(buildActivity([], [investment({ status: "withdrawn" })], [])).toEqual([]);
    const [interest] = buildActivity([], [investment({ status: "matured" })], []);
    expect(interest).toMatchObject({ kind: "interest", title: "Interest on 91 day Treasury bill", amount: ugx(15_400), direction: "in", statusLabel: "Earned" });
    expect(shortDay(interest!.at)).toBe("20 Nov");
  });

  it("keeps amounts as whole units and never mixes currencies", () => {
    const [interest] = buildActivity([], [investment({ status: "matured", currency: "USD", interest_at_maturity_minor: 1_250 })], []);
    expect(interest?.amount).toEqual({ currency: "USD", minor: 1_250 });
  });

  it("puts the newest first across kinds, and groups by Kampala month", () => {
    const items = buildActivity(
      [payment({ id: "old", created_at: "2026-10-31T20:30:00Z", confirmed_at: "2026-10-31T22:30:00Z" }), payment()],
      [investment({ status: "matured" })],
      [payout()],
    );
    expect(items.map((item) => item.key)).toEqual(["interest:inv-1", "payout:out-1", "payment:pay-1", "payment:old"]);
    // 22:30 UTC on 31 Oct is 01:30 on 1 Nov in Kampala.
    expect(monthHeading("2026-10-31T22:30:00Z")).toBe("November 2026");
    expect(byMonth(items).map((group) => [group.month, group.items.length])).toEqual([["November 2026", 4]]);
    expect(byMonth(buildActivity([payment({ confirmed_at: "2026-10-01T09:00:00Z" })], [], [payout()])).map((g) => g.month)).toEqual([
      "November 2026",
      "October 2026",
    ]);
  });
});
