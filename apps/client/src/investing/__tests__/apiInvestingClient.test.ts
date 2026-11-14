import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";
import { forgetBanks } from "@/payouts/banks";

import { createApiInvestingClient } from "../apiInvestingClient";
import { resolveInvestingClient } from "../resolveInvesting";
import { InvestingError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ID = "IIIIIIIIIIIIIIIIIIII";
const ugx = (minor: number) => ({ currency: "UGX" as const, minor });

const product = {
  code: "TREA-91",
  name: "91 Day Treasury Bill",
  kind: "treasury_bill",
  currency: "UGX",
  term_days: 91,
  minimum_minor: 100000,
  interest_paid: "at_maturity",
  status: "live",
  client_rate_percent: "10.63",
};

const quote = {
  product: "TREA-91",
  amount_minor: 1000000,
  currency: "UGX",
  starts_on: "2026-11-13",
  matures_on: "2027-02-12",
  interest_minor: 26490,
  total_minor: 1026490,
};

const investment = {
  id: ID,
  product_code: "TREA-91",
  product_name: "91 Day Treasury Bill",
  status: "active",
  currency: "UGX",
  amount_minor: 1000000,
  client_rate_percent: "10.63",
  term_days: 91,
  at_maturity: "roll_over",
  can_change_maturity: true,
  pay_by: "2026-11-16T09:00:00Z",
  start_date: "2026-11-13",
  maturity_date: "2027-02-12",
  days_earned: 1,
  interest_so_far_minor: 291,
  interest_today_minor: 291,
  value_minor: 1000291,
  interest_at_maturity_minor: 26490,
  total_at_maturity_minor: 1026490,
  rolled_into_id: null,
  payout_id: null,
  payout_destination_id: null,
  waiting_for_destination: false,
  can_withdraw: false,
  withdrawal_requested_at: null,
};

type Seen = { method: string; path: string; key: string | undefined; body: unknown };

function serve(status: number, body: unknown): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    seen.push({
      method: String(init?.method),
      path: String(url).replace(API_URL, ""),
      key: headers["Idempotency-Key"],
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields } };
}

afterEach(() => {
  setFetch(null);
  forgetBanks();
});

describe("the investing client for the real API", () => {
  it("reads the Home and Portfolio totals as the API added them up", async () => {
    const seen = serve(200, {
      currency: "UGX",
      invested_minor: 8000000,
      worth_minor: 8164760,
      interest_so_far_minor: 164760,
      earning_per_day_minor: 2416,
      interest_today_minor: 2416,
      active_count: 3,
      awaiting_payment_count: 1,
      outside_goals: { worth_minor: 5604760, count: 2 },
      in_goals: { worth_minor: 2560000, count: 1 },
      open_goals: 2,
      products: [
        { product_code: "TREA-91", product_name: "91 Day Treasury Bill", worth_minor: 5049486, count: 2, percent: 62 },
        { product_code: "TREA-182", product_name: "182 Day Treasury Bill", worth_minor: 3115274, count: 1, percent: 38 },
      ],
      next_maturity: {
        investment_id: ID,
        product_name: "91 Day Treasury Bill",
        on: "2027-01-09",
        at_maturity: "roll_over",
        term_days: 91,
        days_earned: 34,
        days_left: 57,
      },
    });
    const portfolio = await createApiInvestingClient().getPortfolio();
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/portfolio?currency=UGX" });
    expect(portfolio).toMatchObject({
      worth: ugx(8164760),
      earningPerDay: ugx(2416),
      inGoals: { worth: ugx(2560000), count: 1 },
      openGoals: 2,
      nextMaturity: { investmentId: ID, on: "2027-01-09", daysLeft: 57, atMaturity: "roll_over" },
    });
    expect(portfolio.products.map((share) => share.percent)).toEqual([62, 38]);
  });

  it("refuses totals that came back in another currency rather than mislabel them", async () => {
    serve(200, {
      currency: "USD", invested_minor: 0, worth_minor: 0, interest_so_far_minor: 0, earning_per_day_minor: 0,
      interest_today_minor: 0, active_count: 0, awaiting_payment_count: 0, outside_goals: { worth_minor: 0, count: 0 },
      in_goals: { worth_minor: 0, count: 0 }, open_goals: 0, products: [], next_maturity: null,
    });
    await expect(createApiInvestingClient().getPortfolio("UGX")).rejects.toEqual(new InvestingError("unavailable"));
  });

  it("lists products with the rate after tax, as sent", async () => {
    const seen = serve(200, [product, { ...product, code: "USD-BOND", currency: "USD", status: "paused", term_days: null }]);
    const products = await createApiInvestingClient().listProducts();
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/products" });
    expect(products[0]).toEqual({
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 91,
      minimum: ugx(100000),
      interestPaid: "at_maturity",
      status: "live",
      ratePercent: "10.63",
    });
    expect(products[1]).toMatchObject({ status: "paused", termDays: null, minimum: { currency: "USD", minor: 100000 } });
  });

  it("never treats a draft product as open", async () => {
    serve(200, [{ ...product, status: "draft" }]);
    expect((await createApiInvestingClient().listProducts())[0]!.status).toBe("paused");
  });

  it("quotes an amount by product and passes the net figures through", async () => {
    const seen = serve(200, quote);
    const result = await createApiInvestingClient().quote("TREA-91", ugx(1000000));
    expect(seen[0]!.path).toBe("/client/products/TREA-91/quote?amount_minor=1000000");
    expect(result).toEqual({
      productCode: "TREA-91",
      amount: ugx(1000000),
      startsOn: "2026-11-13",
      maturesOn: "2027-02-12",
      interest: ugx(26490),
      total: ugx(1026490),
    });
  });

  it("quotes an open term with no maturity figures", async () => {
    serve(200, { ...quote, matures_on: null, interest_minor: null, total_minor: null });
    expect(await createApiInvestingClient().quote("TREA-91", ugx(1000000))).toMatchObject({
      maturesOn: null,
      interest: null,
      total: null,
    });
  });

  it("refuses a quote that came back in another currency rather than mislabel it", async () => {
    serve(200, { ...quote, currency: "USD" });
    await expect(createApiInvestingClient().quote("TREA-91", ugx(1000000))).rejects.toEqual(
      new InvestingError("unavailable"),
    );
  });

  it("saves an investment under the screen's idempotency key", async () => {
    const seen = serve(201, { ...investment, status: "awaiting_payment", start_date: null });
    const saved = await createApiInvestingClient().requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1000000),
      atMaturity: "pay_out",
      idempotencyKey: "key-1",
    });
    expect(seen[0]).toEqual({
      method: "POST",
      path: "/client/investments",
      key: "key-1",
      body: { product_code: "TREA-91", amount_minor: 1000000, currency: "UGX", at_maturity: "pay_out" },
    });
    expect(saved).toMatchObject({ id: ID, status: "awaiting_payment", startDate: null });
  });

  it("sends no maturity choice for an open term", async () => {
    const seen = serve(201, investment);
    await createApiInvestingClient().requestInvestment({
      productCode: "TREA-91",
      amount: ugx(1000000),
      atMaturity: null,
      idempotencyKey: "key-2",
    });
    expect(seen[0]!.body).toMatchObject({ at_maturity: null });
  });

  it("reads the portfolio and one investment with every figure as sent", async () => {
    const seen = serve(200, [investment]);
    const [first] = await createApiInvestingClient().listInvestments();
    expect(seen[0]!.path).toBe("/client/investments");
    expect(first).toEqual({
      id: ID,
      productCode: "TREA-91",
      productName: "91 Day Treasury Bill",
      status: "active",
      amount: ugx(1000000),
      ratePercent: "10.63",
      termDays: 91,
      atMaturity: "roll_over",
      payoutDestinationId: null,
      canChangeMaturity: true,
      payBy: "2026-11-16T09:00:00Z",
      startDate: "2026-11-13",
      maturityDate: "2027-02-12",
      daysEarned: 1,
      interestSoFar: ugx(291),
      interestToday: ugx(291),
      value: ugx(1000291),
      interestAtMaturity: ugx(26490),
      totalAtMaturity: ugx(1026490),
      rolledIntoId: null,
      payoutId: null,
      waitingForDestination: false,
      canWithdraw: false,
      withdrawalRequestedAt: null,
      canSend: false,
    });

    const one = serve(200, { ...investment, interest_at_maturity_minor: null, total_at_maturity_minor: null });
    expect(await createApiInvestingClient().getInvestment(ID)).toMatchObject({ interestAtMaturity: null, totalAtMaturity: null });
    expect(one[0]!.path).toBe(`/client/investments/${ID}`);
  });

  it("never passes on a tax rate", async () => {
    serve(200, [product]);
    expect(JSON.stringify(await createApiInvestingClient().listProducts())).not.toMatch(/tax|Bps|before/i);
  });

  it("turns refusals into the reasons the screens show", async () => {
    const client = createApiInvestingClient();
    const request = { productCode: "TREA-91", amount: ugx(1000000), atMaturity: null, idempotencyKey: "k" };

    serve(422, refusal("validation_failed", [{ field_path: ["amount"], code: "below_minimum" }]));
    await expect(client.quote("TREA-91", ugx(1))).rejects.toEqual(new InvestingError("below_minimum"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("below_minimum"));

    serve(422, refusal("validation_failed", [{ field_path: ["amount"], code: "product_not_open" }]));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("product_not_open"));

    // Any other field problem is still a definite refusal, never "could not reach Finvest".
    serve(422, refusal("validation_failed", [{ field_path: ["at_maturity"], code: "open_term_has_no_maturity" }]));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("invalid"));

    serve(403, refusal("forbidden"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("not_verified"));

    serve(409, refusal("conflict"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("too_many_unpaid"));

    serve(422, refusal("idempotency_key_reused"));
    await expect(client.requestInvestment(request)).rejects.toEqual(new InvestingError("key_reused"));

    serve(404, refusal("not_found"));
    await expect(client.getInvestment(ID)).rejects.toEqual(new InvestingError("not_found"));
  });

  it("treats an answer it cannot know as unavailable, so the review screen keeps its key", async () => {
    const request = { productCode: "TREA-91", amount: ugx(1000000), atMaturity: null, idempotencyKey: "k" };
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(createApiInvestingClient().requestInvestment(request)).rejects.toEqual(new InvestingError("unavailable"));

    serve(500, refusal("internal_error"));
    await expect(createApiInvestingClient().requestInvestment(request)).rejects.toEqual(new InvestingError("unavailable"));
  });
});

describe("choosing the investing client", () => {
  it("talks to the API when the demo is not switched on", async () => {
    const seen = serve(200, [product]);
    await resolveInvestingClient(null).listProducts();
    expect(seen[0]!.path).toBe("/client/products");
  });
});

describe("early withdrawal through the real API", () => {
  const account = {
    id: "DDDDDDDDDDDDDDDDDDDD",
    kind: "mobile_money",
    network: "mtn",
    bank_code: null,
    account: "0772 *** 456",
    holder_name: "Grace Namubiru",
    status: "approved",
    proof_kind: null,
    rejection_reason: null,
    payable_from: "2026-11-01T09:00:00Z",
    first_paid_at: null,
    created_at: "2026-10-31T09:00:00Z",
  };

  it("lists only approved accounts, named as the client knows them", async () => {
    const views = [
      account,
      { ...account, id: "BBBBBBBBBBBBBBBBBBBB", kind: "bank", network: null, bank_code: "stanbic", account: "****4821" },
      { ...account, id: "RRRRRRRRRRRRRRRRRRRR", status: "in_review" },
    ];
    const seen: string[] = [];
    setFetch(async (url) => {
      const path = String(url).replace(API_URL, "");
      seen.push(path);
      return new Response(JSON.stringify(path === "/client/banks" ? [{ code: "stanbic", name: "Stanbic Bank" }] : views), { status: 200 });
    });
    const accounts = await createApiInvestingClient().listPayoutAccounts();
    expect(seen.sort()).toEqual(["/client/banks", "/client/payout-destinations"]);
    expect(accounts).toEqual([
      { id: account.id, kind: "mobile_money", label: "MTN 0772 *** 456", payableFrom: "2026-11-01T09:00:00Z", ready: true },
      { id: "BBBBBBBBBBBBBBBBBBBB", kind: "bank", label: "Stanbic Bank ****4821", payableFrom: "2026-11-01T09:00:00Z", ready: true },
    ]);
  });

  it("marks an account still in its first day's wait as not ready", async () => {
    const later = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    serve(200, [{ ...account, payable_from: later }, { ...account, id: "NNNNNNNNNNNNNNNNNNNN", payable_from: null }]);
    const accounts = await createApiInvestingClient().listPayoutAccounts();
    expect(accounts.map((one) => one.ready)).toEqual([false, false]);
  });

  it("asks once under the screen's key and reads back the stopped investment", async () => {
    const seen = serve(200, { ...investment, withdrawal_requested_at: "2026-11-14T10:00:00Z" });
    const after = await createApiInvestingClient().withdrawEarly({
      investmentId: ID,
      destinationId: account.id,
      idempotencyKey: "w-1",
    });
    expect(seen[0]).toEqual({
      method: "POST",
      path: `/client/investments/${ID}/withdraw`,
      key: "w-1",
      body: { destination_id: account.id },
    });
    expect(after).toMatchObject({ withdrawalRequestedAt: "2026-11-14T10:00:00Z", value: ugx(1000291) });
  });

  it("sends the PIN confirmation from a phone with a PIN, and says when it is needed again", async () => {
    const client = createApiInvestingClient();
    const seen = serve(200, { ...investment, withdrawal_requested_at: "2026-11-14T10:00:00Z" });
    await client.withdrawEarly({ investmentId: ID, destinationId: account.id, idempotencyKey: "w-2", pinConfirmation: "token" });
    expect(seen[0]!.body).toEqual({ destination_id: account.id, pin_confirmation: "token" });
    serve(428, refusal("pin_required"));
    await expect(
      client.payOutMatured({ investmentId: ID, destinationId: account.id, pinConfirmation: "old" }),
    ).rejects.toMatchObject({ code: "pin_required" });
  });

  it("says why a withdrawal was refused", async () => {
    const client = createApiInvestingClient();
    const request = { investmentId: ID, destinationId: account.id, idempotencyKey: "w" };

    // Not running, already asked for, too close to maturity, or switched off.
    serve(409, refusal("conflict"));
    await expect(client.withdrawEarly(request)).rejects.toEqual(new InvestingError("cannot_withdraw"));

    serve(422, refusal("validation_failed", [{ field_path: ["destination_id"], code: "destination_in_safety_wait" }]));
    await expect(client.withdrawEarly(request)).rejects.toEqual(new InvestingError("account_not_ready"));
    serve(422, refusal("validation_failed", [{ field_path: ["destination_id"], code: "not_usable" }]));
    await expect(client.withdrawEarly(request)).rejects.toEqual(new InvestingError("account_not_ready"));

    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(client.withdrawEarly(request)).rejects.toEqual(new InvestingError("unavailable"));
  });

  it("passes on whether the investment can be withdrawn now", async () => {
    serve(200, { ...investment, can_withdraw: true });
    expect((await createApiInvestingClient().getInvestment(ID)).canWithdraw).toBe(true);
  });

  it("saves the maturity choice with the account to pay, and says why it was refused", async () => {
    const client = createApiInvestingClient();
    const seen = serve(200, { ...investment, at_maturity: "pay_out", payout_destination_id: account.id });
    const after = await client.chooseAtMaturity({ investmentId: ID, atMaturity: "pay_out", destinationId: account.id });
    expect(seen[0]).toEqual({
      method: "PUT",
      path: `/client/investments/${ID}/at-maturity`,
      key: undefined,
      body: { at_maturity: "pay_out", destination_id: account.id },
    });
    expect(after).toMatchObject({ atMaturity: "pay_out", payoutDestinationId: account.id });

    // Too close to maturity, or already ended.
    serve(409, refusal("conflict"));
    await expect(client.chooseAtMaturity({ investmentId: ID, atMaturity: "roll_over", destinationId: null })).rejects.toEqual(
      new InvestingError("cannot_change_maturity"),
    );
    serve(422, refusal("validation_failed", [{ field_path: ["body", "destination_id"], code: "not_usable" }]));
    await expect(client.chooseAtMaturity({ investmentId: ID, atMaturity: "pay_out", destinationId: account.id })).rejects.toEqual(
      new InvestingError("account_not_ready"),
    );
  });

  it("pays out matured money to the chosen account, and says why payouts refused it", async () => {
    const client = createApiInvestingClient();
    const request = { investmentId: ID, destinationId: account.id };
    const seen = serve(200, { ...investment, status: "matured", payout_id: "PPPPPPPPPPPPPPPPPPPP" });
    expect((await client.payOutMatured(request)).payoutId).toBe("PPPPPPPPPPPPPPPPPPPP");
    expect(seen[0]).toEqual({
      method: "POST",
      path: `/client/investments/${ID}/pay-out`,
      key: undefined,
      body: { destination_id: account.id },
    });

    serve(409, refusal("conflict"));
    await expect(client.payOutMatured(request)).rejects.toEqual(new InvestingError("already_sent"));
    serve(422, refusal("validation_failed", [{ field_path: ["destination_id"], code: "over_mobile_money_limit" }]));
    await expect(client.payOutMatured(request)).rejects.toEqual(new InvestingError("over_mobile_money_limit"));
    serve(422, refusal("validation_failed", [{ field_path: ["destination_id"], code: "over_daily_limit" }]));
    await expect(client.payOutMatured(request)).rejects.toEqual(new InvestingError("over_limit"));
    serve(422, refusal("validation_failed", [{ field_path: ["destination_id"], code: "destination_in_safety_wait" }]));
    await expect(client.payOutMatured(request)).rejects.toEqual(new InvestingError("account_not_ready"));
  });
});

describe("sending an investment through the real API", () => {
  const transfer = {
    id: "TTTTTTTTTTTTTTTTTTTT",
    direction: "sent",
    other_name: "Brian K.",
    status: "waiting",
    amount_minor: 1000000,
    currency: "UGX",
    whole: false,
    message: "For school fees",
    requested_at: "2026-11-14T10:00:00Z",
    finished_at: null,
    investment_id: ID,
    kept_investment_id: null,
    failure_code: null,
  };

  it("finds the recipient by what the client typed and shows only their short name", async () => {
    const seen = serve(200, { account_number: "1001005170", name: "Brian K." });
    const found = await createApiInvestingClient().findRecipient("0772 555 170");
    expect(seen[0]?.path).toBe("/client/transfer-recipients?to=0772%20555%20170");
    expect(found).toEqual({ accountNumber: "1001005170", name: "Brian K." });
  });

  it("says why nobody was found, and when there were too many look-ups", async () => {
    const client = createApiInvestingClient();
    serve(404, refusal("not_found"));
    await expect(client.findRecipient("1001005171")).rejects.toEqual(new InvestingError("no_such_client"));
    serve(422, refusal("validation_failed", [{ field_path: ["query", "to"], code: "to_yourself" }]));
    await expect(client.findRecipient("1004417263")).rejects.toEqual(new InvestingError("to_yourself"));
    serve(429, refusal("rate_limited"));
    await expect(client.findRecipient("1001005170")).rejects.toEqual(new InvestingError("too_many_lookups"));
  });

  it("sends once under the screen's key and reads the transfer back", async () => {
    const seen = serve(201, transfer);
    const sent = await createApiInvestingClient().sendInvestment({
      investmentId: ID,
      toAccountNumber: "1001005170",
      amount: ugx(1_000_000),
      message: "For school fees",
      whole: false,
      idempotencyKey: "s-1",
    });
    expect(seen[0]).toEqual({
      method: "POST",
      path: `/client/investments/${ID}/send`,
      key: "s-1",
      body: { to_account_number: "1001005170", amount_minor: 1000000, currency: "UGX", message: "For school fees", whole: false },
    });
    expect(sent).toMatchObject({ id: transfer.id, direction: "sent", otherName: "Brian K.", amount: ugx(1_000_000) });
  });

  it("says why a send was refused", async () => {
    const client = createApiInvestingClient();
    const request = { investmentId: ID, toAccountNumber: "1001005170", amount: ugx(50_000), message: null, whole: false, idempotencyKey: "s" };
    const cases: [string, InvestingError["code"]][] = [
      ["below_product_minimum", "below_product_minimum"],
      ["more_than_investment_value", "more_than_value"],
      ["remainder_below_product_minimum", "remainder_below_minimum"],
      ["matures_today", "cannot_send"],
    ];
    for (const [code, expected] of cases) {
      serve(422, refusal("validation_failed", [{ field_path: ["body", "amount_minor"], code }]));
      await expect(client.sendInvestment(request)).rejects.toEqual(new InvestingError(expected));
    }
    serve(409, refusal("conflict"));
    await expect(client.sendInvestment(request)).rejects.toEqual(new InvestingError("cannot_send"));
    serve(403, refusal("forbidden", [{ field_path: ["body"], code: "restricted_after_code_sign_in" }]));
    await expect(client.sendInvestment(request)).rejects.toEqual(new InvestingError("restricted"));
  });

  it("lists transfers and cancels a held one", async () => {
    serve(200, [{ ...transfer, direction: "received", status: "done", other_name: "Amara N." }]);
    expect(await createApiInvestingClient().listTransfers()).toMatchObject([{ direction: "received", otherName: "Amara N." }]);
    const seen = serve(200, { ...transfer, status: "cancelled" });
    expect(await createApiInvestingClient().cancelTransfer(transfer.id)).toMatchObject({ status: "cancelled" });
    expect(seen[0]).toMatchObject({ method: "POST", path: `/client/transfers/${transfer.id}/cancel` });
    serve(409, refusal("conflict"));
    await expect(createApiInvestingClient().cancelTransfer(transfer.id)).rejects.toEqual(new InvestingError("cannot_cancel"));
  });

  it("passes on whether the investment can be sent now", async () => {
    serve(200, { ...investment, can_send: true });
    expect((await createApiInvestingClient().getInvestment(ID)).canSend).toBe(true);
  });
});
