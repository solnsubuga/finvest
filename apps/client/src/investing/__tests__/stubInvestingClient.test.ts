import { markDemoPaid } from "@/demo/payables";
import { formatAmount, groupDigits, investmentLabel, parseWholeAmount } from "@/investing/format";
import { createStubInvestingClient } from "@/investing/stubInvestingClient";
import { InvestingError } from "@/investing/types";

const DAY_MS = 24 * 60 * 60 * 1000;
const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

function client() {
  const clock = { ms: Date.parse("2026-11-13T09:00:00Z") };
  return { clock, api: createStubInvestingClient({ delayMs: 0, now: () => clock.ms, sample: "empty" }) };
}

describe("demo investing", () => {
  it("quotes the backend's example figure", async () => {
    const { api } = client();
    const quote = await api.quote("TREA-91", ugx(1_000_000));
    expect(quote).toMatchObject({
      startsOn: "2026-11-13",
      maturesOn: "2027-02-12",
      interest: ugx(26_490),
      total: ugx(1_026_490),
    });
  });

  it("never hands a tax rate to the screens", async () => {
    const { api } = client();
    const products = await api.listProducts();
    expect(JSON.stringify(products)).not.toMatch(/tax|Bps/i);
  });

  it("refuses an amount below the minimum and a paused product", async () => {
    const { api } = client();
    await expect(api.quote("TREA-182", ugx(50_000))).rejects.toEqual(new InvestingError("below_minimum"));
    await expect(api.quote("USD-BOND", { currency: "USD", minor: 1_000_00 })).rejects.toEqual(
      new InvestingError("product_not_open"),
    );
  });

  it("saves one investment per key", async () => {
    const { api } = client();
    const request = { productCode: "TREA-91", amount: ugx(1_000_000), atMaturity: null, idempotencyKey: "k1" };
    const first = await api.requestInvestment(request);
    const again = await api.requestInvestment(request);
    expect(again.id).toBe(first.id);
    expect(first).toMatchObject({ status: "awaiting_payment", atMaturity: "roll_over", interestSoFar: ugx(0) });
    expect(await api.listInvestments()).toHaveLength(1);
  });

  it("refuses a known key sent with different details, as the backend does", async () => {
    const { api } = client();
    const request = { productCode: "TREA-91", amount: ugx(1_000_000), atMaturity: "roll_over" as const, idempotencyKey: "k2" };
    await api.requestInvestment(request);
    for (const changed of [
      { ...request, atMaturity: "pay_out" as const },
      { ...request, amount: ugx(2_000_000) },
      { ...request, productCode: "TREA-182" },
    ]) {
      await expect(api.requestInvestment(changed)).rejects.toMatchObject({ code: "key_reused" });
    }
    expect(await api.listInvestments()).toHaveLength(1);
  });

  it("starts the term once the demo payment is confirmed and adds interest each night", async () => {
    const { api, clock } = client();
    const saved = await api.requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1_000_000),
      atMaturity: "pay_out",
      idempotencyKey: "k2",
    });
    markDemoPaid(saved.id, new Date(clock.ms).toISOString());
    expect(await api.getInvestment(saved.id)).toMatchObject({ status: "active", startDate: "2026-11-13", daysEarned: 0 });
    clock.ms += DAY_MS;
    const nextDay = await api.getInvestment(saved.id);
    expect(nextDay).toMatchObject({ daysEarned: 1, interestToday: ugx(291), interestSoFar: ugx(291), value: ugx(1_000_291) });
    clock.ms += 90 * DAY_MS;
    expect(await api.getInvestment(saved.id)).toMatchObject({ status: "matured", interestSoFar: ugx(26_490) });
  });

  it("lets an unpaid investment lapse after 72 hours", async () => {
    const { api, clock } = client();
    const saved = await api.requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1_000_000),
      atMaturity: null,
      idempotencyKey: "k3",
    });
    clock.ms += 73 * 60 * 60 * 1000;
    expect((await api.getInvestment(saved.id)).status).toBe("cancelled");
    expect(await api.listInvestments()).toHaveLength(0);
  });
});

describe("demo early withdrawal", () => {
  async function running() {
    const { api, clock } = client();
    const saved = await api.requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1_000_000),
      atMaturity: null,
      idempotencyKey: "kw",
    });
    markDemoPaid(saved.id, new Date(clock.ms).toISOString());
    clock.ms += 2 * DAY_MS;
    return { api, clock, id: saved.id };
  }

  it("pays what was shown and stops interest at the request", async () => {
    const { api, clock, id } = await running();
    expect(await api.getInvestment(id)).toMatchObject({ canWithdraw: true, value: ugx(1_000_582) });
    const after = await api.withdrawEarly({ investmentId: id, destinationId: "demo-account-mtn", idempotencyKey: "w1" });
    // As the API answers: still active, with the request recorded, until the money has moved.
    expect(after).toMatchObject({ status: "active", canWithdraw: false, value: ugx(1_000_582) });
    expect(after.withdrawalRequestedAt).toBe(new Date(clock.ms).toISOString());
    expect(investmentLabel(after)).toBe("Withdrawn early");
    clock.ms += 5 * DAY_MS;
    expect((await api.getInvestment(id)).value).toEqual(ugx(1_000_582));
  });

  it("asks once per key and refuses the same key for another account", async () => {
    const { api, id } = await running();
    const request = { investmentId: id, destinationId: "demo-account-mtn", idempotencyKey: "w2" };
    await api.withdrawEarly(request);
    await expect(api.withdrawEarly(request)).resolves.toMatchObject({ canWithdraw: false });
    await expect(api.withdrawEarly({ ...request, destinationId: "demo-account-bank" })).rejects.toMatchObject({
      code: "key_reused",
    });
    await expect(api.withdrawEarly({ ...request, idempotencyKey: "w3" })).rejects.toMatchObject({
      code: "cannot_withdraw",
    });
  });

  it("refuses an account still in its safety wait", async () => {
    const { api, id } = await running();
    const accounts = await api.listPayoutAccounts();
    expect(accounts.map((account) => account.id)).toEqual(["demo-account-mtn", "demo-account-bank"]);
    await expect(
      api.withdrawEarly({ investmentId: id, destinationId: "demo-account-bank", idempotencyKey: "w4" }),
    ).rejects.toMatchObject({ code: "account_not_ready" });
    expect((await api.getInvestment(id)).status).toBe("active");
  });
});

describe("the demo's withdrawal states", () => {
  it("can have no approved accounts", async () => {
    const api = createStubInvestingClient({ delayMs: 0, sample: "empty", accounts: "none" });
    expect(await api.listPayoutAccounts()).toEqual([]);
  });

  it("can lose the first answer, which a retry under the same key then gets", async () => {
    const clock = { ms: Date.parse("2026-11-13T09:00:00Z") };
    const api = createStubInvestingClient({ delayMs: 0, now: () => clock.ms, sample: "empty", loseFirstWithdrawal: true });
    const saved = await api.requestInvestment({ productCode: "TREA-91", amount: ugx(1_000_000), atMaturity: null, idempotencyKey: "kl" });
    markDemoPaid(saved.id, new Date(clock.ms).toISOString());
    clock.ms += DAY_MS;
    const request = { investmentId: saved.id, destinationId: "demo-account-mtn", idempotencyKey: "wl" };
    await expect(api.withdrawEarly(request)).rejects.toMatchObject({ code: "unavailable" });
    await expect(api.withdrawEarly(request)).resolves.toMatchObject({ withdrawalRequestedAt: expect.any(String) });
  });
});

describe("investment labels", () => {
  it("reads as withdrawn early from the request, whatever the status says", () => {
    expect(investmentLabel({ status: "active", withdrawalRequestedAt: null })).toBe("Running");
    expect(investmentLabel({ status: "active", withdrawalRequestedAt: "2026-11-14T10:00:00Z" })).toBe("Withdrawn early");
    expect(investmentLabel({ status: "withdrawn", withdrawalRequestedAt: "2026-11-14T10:00:00Z" })).toBe("Withdrawn early");
  });
});

describe("amount formats", () => {
  it("reads whole amounts only", () => {
    expect(parseWholeAmount("1,000,000", "UGX")).toEqual(ugx(1_000_000));
    expect(parseWholeAmount("500", "USD")).toEqual({ currency: "USD", minor: 50_000 });
    expect(parseWholeAmount("1000.50", "UGX")).toBeNull();
    expect(parseWholeAmount("0", "UGX")).toBeNull();
    expect(parseWholeAmount("", "UGX")).toBeNull();
  });

  it("groups digits as they are typed", () => {
    expect(groupDigits("1000000")).toBe("1,000,000");
    expect(groupDigits("00120a")).toBe("120");
  });

  it("writes amounts in both currencies", () => {
    expect(formatAmount(ugx(26_490))).toBe("UGX 26,490");
    expect(formatAmount({ currency: "USD", minor: 50_000 })).toBe("USD 500.00");
  });
});

describe("demo maturity choice and pay-out", () => {
  const demo = (maturedWaiting = false) =>
    createStubInvestingClient({ delayMs: 0, now: () => Date.parse("2026-11-13T09:00:00Z"), maturedWaiting });

  it("changes the choice, needing an approved account only to pay out", async () => {
    const api = demo();
    const running = (await api.listInvestments()).find((one) => one.atMaturity === "roll_over")!;
    await expect(api.chooseAtMaturity({ investmentId: running.id, atMaturity: "pay_out", destinationId: "nope" })).rejects.toEqual(
      new InvestingError("account_not_ready"),
    );
    // An account still in its first day may be chosen for later.
    const after = await api.chooseAtMaturity({ investmentId: running.id, atMaturity: "pay_out", destinationId: "demo-account-bank" });
    expect(after).toMatchObject({ atMaturity: "pay_out", payoutDestinationId: "demo-account-bank" });
    await expect(
      api.chooseAtMaturity({ investmentId: running.id, atMaturity: "roll_over", destinationId: "demo-account-bank" }),
    ).rejects.toEqual(new InvestingError("invalid"));
    expect((await api.chooseAtMaturity({ investmentId: running.id, atMaturity: "roll_over", destinationId: null })).payoutDestinationId).toBeNull();
  });

  it("pays matured money once, to a ready account within the mobile money limit", async () => {
    const api = demo(true);
    const matured = (await api.listInvestments()).find((one) => one.waitingForDestination)!;
    const request = { investmentId: matured.id, destinationId: "demo-account-mtn" };
    await expect(api.payOutMatured({ ...request, destinationId: "demo-account-bank" })).rejects.toEqual(
      new InvestingError("account_not_ready"),
    );
    const paid = await api.payOutMatured(request);
    expect(paid.payoutId).not.toBeNull();
    expect(paid.waitingForDestination).toBe(false);
    // The same account asked again is the same payout.
    expect((await api.payOutMatured(request)).payoutId).toBe(paid.payoutId);
    await expect(api.chooseAtMaturity({ investmentId: matured.id, atMaturity: "roll_over", destinationId: null })).rejects.toEqual(
      new InvestingError("cannot_change_maturity"),
    );
  });
});

describe("demo sending", () => {
  function sample() {
    const clock = { ms: Date.parse("2026-11-13T09:00:00Z") };
    return createStubInvestingClient({ delayMs: 0, now: () => clock.ms, transfers: "none" });
  }

  it("finds the demo recipients by number or phone and refuses the client's own number", async () => {
    const api = sample();
    expect(await api.findRecipient("1001 005 170")).toEqual({ accountNumber: "1001005170", name: "Brian K." });
    expect((await api.findRecipient("0772 555 170")).name).toBe("Brian K.");
    await expect(api.findRecipient("1004417263")).rejects.toEqual(new InvestingError("to_yourself"));
    await expect(api.findRecipient("1009999999")).rejects.toEqual(new InvestingError("no_such_client"));
    await expect(api.findRecipient("hello")).rejects.toEqual(new InvestingError("not_an_account_number"));
  });

  it("sends part of an investment, keeps the rest on the same maturity date, and sends once per key", async () => {
    const api = sample();
    const source = (await api.listInvestments()).find((one) => one.canSend && one.productCode === "TREA-91")!;
    const request = { investmentId: source.id, toAccountNumber: "1001005170", amount: ugx(1_000_000), message: null, whole: false, idempotencyKey: "k" };
    const sent = await api.sendInvestment(request);
    expect(sent).toMatchObject({ status: "done", otherName: "Brian K.", whole: false });
    expect(await api.sendInvestment(request)).toEqual(sent);
    expect((await api.getInvestment(source.id)).status).toBe("transferred");
    const kept = await api.getInvestment(sent.keptInvestmentId as string);
    expect(kept.amount.minor).toBe(source.value.minor - 1_000_000);
    expect(kept.maturityDate).toBe(source.maturityDate);
  });

  it("holds a send that takes the day's total over the limit, and lets the client cancel it", async () => {
    const clock = { ms: Date.parse("2026-11-13T09:00:00Z") };
    const api = createStubInvestingClient({ delayMs: 0, now: () => clock.ms, transfers: "none", reviewAbove: 1_500_000 });
    const source = (await api.listInvestments()).find((one) => one.canSend && one.productCode === "TREA-91")!;
    const first = await api.sendInvestment({ investmentId: source.id, toAccountNumber: "1001005170", amount: ugx(1_000_000), message: null, whole: false, idempotencyKey: "a" });
    expect(first.status).toBe("done");
    // 1,000,000 already sent today, so another 1,000,000 is over the limit.
    const second = await api.sendInvestment({ investmentId: first.keptInvestmentId as string, toAccountNumber: "1003382914", amount: ugx(1_000_000), message: null, whole: false, idempotencyKey: "b" });
    expect(second.status).toBe("held");
    expect((await api.getInvestment(first.keptInvestmentId as string)).canSend).toBe(false);
    expect((await api.cancelTransfer(second.id)).status).toBe("cancelled");
    await expect(api.cancelTransfer(first.id)).rejects.toEqual(new InvestingError("cannot_cancel"));
    expect((await api.getInvestment(first.keptInvestmentId as string)).canSend).toBe(true);
  });

  it("sends everything at its value when asked, even if interest was added since the screen loaded", async () => {
    const clock = { ms: Date.parse("2026-11-13T09:00:00Z") };
    const api = createStubInvestingClient({ delayMs: 0, now: () => clock.ms, transfers: "none" });
    const source = (await api.listInvestments()).find((one) => one.canSend && one.productCode === "TREA-91")!;
    // A day passes: the value grows, but the screen still holds yesterday's.
    clock.ms += 24 * 60 * 60 * 1000;
    const now = await api.getInvestment(source.id);
    expect(now.value.minor).toBeGreaterThan(source.value.minor);
    const sent = await api.sendInvestment({ investmentId: source.id, toAccountNumber: "1001005170", amount: source.value, message: null, whole: true, idempotencyKey: "w" });
    expect(sent).toMatchObject({ whole: true, amount: now.value, keptInvestmentId: null });
  });

  it("refuses a remainder below the minimum and more than the value", async () => {
    const api = sample();
    const source = (await api.listInvestments()).find((one) => one.canSend && one.productCode === "TREA-91")!;
    const send = (minor: number) =>
      api.sendInvestment({ investmentId: source.id, toAccountNumber: "1001005170", amount: ugx(minor), message: null, whole: false, idempotencyKey: `x${minor}` });
    await expect(send(source.value.minor - 50_000)).rejects.toEqual(new InvestingError("remainder_below_minimum"));
    await expect(send(source.value.minor + 1)).rejects.toEqual(new InvestingError("more_than_value"));
  });
});
