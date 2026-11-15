import { nothingToPayOut } from "@/investing/payOut";
import { Investment } from "@/investing/types";

const ugx = (minor: number) => ({ minor, currency: "UGX" as const });

const running: Investment = {
  id: "inv1",
  productCode: "TREA-91",
  productName: "91 Day Treasury Bill",
  status: "active",
  amount: ugx(1_000_000),
  ratePercent: "10.63",
  termDays: 91,
  atMaturity: "pay_out",
  payoutDestinationId: "dest1",
  interestGoal: null,
  canChangeMaturity: true,
  payBy: "2026-11-18T09:00:00Z",
  startDate: "2026-11-15",
  maturityDate: "2027-02-14",
  daysEarned: 0,
  interestSoFar: ugx(0),
  interestToday: ugx(0),
  value: ugx(1_000_000),
  interestAtMaturity: ugx(26_500),
  totalAtMaturity: ugx(1_026_500),
  rolledIntoId: null,
  payoutId: null,
  waitingForDestination: false,
  waiting: null,
  canWithdraw: true,
  withdrawalRequestedAt: null,
  canSend: true,
  lockedUntil: null,
};

describe("the pay out screen for an investment with nothing waiting", () => {
  it("says a running investment is still running, when it matures, and offers early withdrawal", () => {
    const found = nothingToPayOut(running);
    expect(found.title).toBe("Nothing to pay out yet");
    expect(found.message).toContain("still running");
    expect(found.message).toContain("14 Feb 2027");
    expect(found.message).not.toContain("on its way");
    expect(found.action).toEqual({ label: "Withdraw early", href: "/investment/withdraw/inv1", testID: "pay-out-withdraw" });
  });

  it("says one that rolls over starts another term, and offers nothing when it cannot be withdrawn", () => {
    const found = nothingToPayOut({ ...running, atMaturity: "roll_over", canWithdraw: false });
    expect(found.message).toContain("starts another term");
    expect(found.message).not.toContain("withdraw early");
    expect(found.action).toBeNull();
  });

  it("says an early withdrawal is under way", () => {
    const found = nothingToPayOut({ ...running, canWithdraw: false, withdrawalRequestedAt: "2026-11-15T10:00:00Z" });
    expect(found.title).toBe("Being withdrawn");
    expect(found.action).toBeNull();
  });

  it("asks for payment when it has not started", () => {
    const found = nothingToPayOut({ ...running, status: "awaiting_payment", startDate: null, canWithdraw: false });
    expect(found.message).toContain("has not started yet");
    expect(found.action?.href).toBe("/pay/inv1");
  });

  it("says the money is on its way only once it is paid out", () => {
    const found = nothingToPayOut({ ...running, status: "matured", canWithdraw: false, payoutId: "p1" });
    expect(found.title).toBe("Already paid out");
    expect(found.message).toContain("already on its way");
  });

  it("gives a cancelled, sent or reversed investment its own words, never on its way", () => {
    const cancelled = nothingToPayOut({ ...running, status: "cancelled", startDate: null, canWithdraw: false });
    expect(cancelled.message).toContain("not paid for in time");
    expect(cancelled.action?.href).toBe("/invest/TREA-91");
    const sent = nothingToPayOut({ ...running, status: "transferred", canWithdraw: false, canSend: false });
    expect(sent.message).toContain("sent this investment to another Finvest client");
    expect(sent.action?.href).toBe("/portfolio");
    const reversed = nothingToPayOut({ ...running, status: "unwound", canWithdraw: false });
    expect(reversed.message).toContain("reversed");
    expect(reversed.action).toBeNull();
    for (const found of [cancelled, sent, reversed]) expect(found.message).not.toContain("on its way");
  });

  it("names an early withdrawal that has been paid", () => {
    expect(nothingToPayOut({ ...running, status: "withdrawn", canWithdraw: false }).title).toBe("Withdrawn early");
  });
});
