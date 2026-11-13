import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiPaymentsClient } from "../apiPaymentsClient";
import { openPaymentHref, PaymentError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

const INVESTMENT = "I".repeat(20);
const PAYMENT = "P".repeat(20);

const investment = (status = "awaiting_payment", currency = "UGX") => ({
  amount_minor: 1_000_000, at_maturity: null, can_change_maturity: false, client_rate_percent: "10.00", currency,
  days_earned: 0, id: INVESTMENT, interest_at_maturity_minor: null, interest_so_far_minor: 0,
  interest_today_minor: 0, maturity_date: null, pay_by: "2026-11-14T12:00:00Z", payout_destination_id: null,
  payout_id: null, product_code: "TB91", product_name: "91 Day Treasury Bill", rolled_into_id: null,
  start_date: null, status, term_days: null, total_at_maturity_minor: null, value_minor: 0,
  waiting_for_destination: false,
});

const payment = (overrides: Record<string, unknown> = {}) => ({
  id: PAYMENT,
  investment_id: INVESTMENT,
  method: "mtn",
  status: "created",
  amount: { minor: 1_000_000, currency: "UGX" },
  failure: null,
  payer: "+256 77• ••• •56",
  receipt: null,
  created_at: "2026-11-13T12:30:00Z",
  confirmed_at: null,
  ...overrides,
});

type Seen = { method: string; url: string; headers: Record<string, string>; body: unknown };

/** Answers each path with what `routes` says, and records what was asked. */
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

const request = { payableId: INVESTMENT, network: "mtn" as const, payer: "+256772123456", idempotencyKey: "key-1" };

describe("the payments client for the real API", () => {
  it("reads what is owed from the investment itself", async () => {
    serve({ [`GET /client/investments/${INVESTMENT}`]: json(investment()) });
    await expect(createApiPaymentsClient().getPayable(INVESTMENT)).resolves.toEqual({
      id: INVESTMENT,
      kind: "investment",
      title: "91 Day Treasury Bill",
      amount: { minor: 1_000_000, currency: "UGX" },
    });
  });

  it("refuses to pay for an investment that is not waiting for payment, or is not there", async () => {
    serve({ [`GET /client/investments/${INVESTMENT}`]: json(investment("active")) });
    await expect(createApiPaymentsClient().getPayable(INVESTMENT)).rejects.toEqual(new PaymentError("not_payable"));
    serve({ [`GET /client/investments/${INVESTMENT}`]: refused("not_found", 404) });
    await expect(createApiPaymentsClient().getPayable(INVESTMENT)).rejects.toEqual(new PaymentError("not_payable"));
  });

  it("asks for a prompt with the network and the key only, never an amount or a number", async () => {
    const seen = serve({
      [`POST /client/investments/${INVESTMENT}/payments/mobile-money`]: json(payment(), 201),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
    });
    const deposit = await createApiPaymentsClient().payWithMobileMoney(request);
    expect(seen[0]!.headers["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(String(seen[0]!.body))).toEqual({ network: "mtn" });
    expect(deposit).toMatchObject({
      id: PAYMENT,
      network: "mtn",
      payer: "+256 77• ••• •56",
      status: "sending",
      failure: null,
      reference: PAYMENT,
      payable: { title: "91 Day Treasury Bill", amount: { minor: 1_000_000, currency: "UGX" } },
    });
  });

  it.each([
    ["sending", "sending"],
    ["awaiting_approval", "awaiting_approval"],
    ["uncertain", "checking"],
    ["confirmed", "confirmed"],
  ])("reads a payment that is %s as %s", async (status, shown) => {
    serve({
      [`GET /client/payments/${PAYMENT}`]: json(payment({ status })),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
    });
    await expect(createApiPaymentsClient().getDeposit(PAYMENT)).resolves.toMatchObject({ status: shown });
  });

  it("says why a payment failed in the network's words, and our own codes as other", async () => {
    serve({
      [`GET /client/payments/${PAYMENT}`]: json(payment({ status: "failed", failure: "insufficient_funds" })),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
    });
    const client = createApiPaymentsClient();
    await expect(client.getDeposit(PAYMENT)).resolves.toMatchObject({ status: "failed", failure: "insufficient_funds" });
    serve({ [`GET /client/payments/${PAYMENT}`]: json(payment({ status: "failed", failure: "amount_mismatch" })) });
    await expect(client.getDeposit(PAYMENT)).resolves.toMatchObject({ status: "failed", failure: "other" });
  });

  it("reads the investment once for every check of its payment", async () => {
    const seen = serve({
      [`GET /client/payments/${PAYMENT}`]: json(payment({ status: "sending" })),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
    });
    const client = createApiPaymentsClient();
    await client.getDeposit(PAYMENT);
    await client.getDeposit(PAYMENT);
    expect(seen.filter((call) => call.url.startsWith("/client/investments"))).toHaveLength(1);
  });

  it("tells a payment already under way from one already paid when the API says conflict", async () => {
    const path = `POST /client/investments/${INVESTMENT}/payments/mobile-money`;
    const live = `GET /client/investments/${INVESTMENT}/payment`;
    const client = createApiPaymentsClient();
    serve({ [path]: refused("conflict", 409), [live]: json(payment({ status: "awaiting_approval" })) });
    const underWay = await client.payWithMobileMoney(request).catch((error: unknown) => error);
    expect(underWay).toEqual(new PaymentError("already_paying"));
    expect((underWay as PaymentError).openPayment).toEqual({ id: PAYMENT, method: "mobile_money" });
    expect(openPaymentHref(underWay)).toBe(`/pay/deposit/${PAYMENT}`);
    serve({ [path]: refused("conflict", 409), [live]: json(payment({ method: "bank_transfer", status: "in_review", payer: null })) });
    const checking = await client.payWithMobileMoney(request).catch((error: unknown) => error);
    expect(openPaymentHref(checking)).toBe(`/pay/bank/transfer/${PAYMENT}`);
    serve({ [path]: refused("conflict", 409), [live]: json(payment({ status: "confirmed" })) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("not_payable"));
    serve({ [path]: refused("conflict", 409), [live]: refused("not_found", 404) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("not_payable"));
  });

  it("maps the other refusals to reasons the pay screens can show", async () => {
    const path = `POST /client/investments/${INVESTMENT}/payments/mobile-money`;
    const client = createApiPaymentsClient();
    serve({ [path]: refused("idempotency_key_reused", 422) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("key_reused"));
    serve({ [path]: refused("validation_failed", 422, [{ field_path: ["amount"], code: "unsupported_currency" }]) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("amount_not_allowed"));
    serve({ [path]: refused("not_found", 404) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("not_payable"));
    // Any other field problem is a definite refusal, never "could not reach Finvest".
    serve({ [path]: refused("validation_failed", 422, [{ field_path: ["network"], code: "invalid" }]) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("not_accepted"));
  });

  it("fails as unavailable when offline or when the answer does not match the contract", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    const client = createApiPaymentsClient();
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("unavailable"));
    serve({ [`POST /client/investments/${INVESTMENT}/payments/mobile-money`]: json({ id: PAYMENT }, 201) });
    await expect(client.payWithMobileMoney(request)).rejects.toEqual(new PaymentError("unavailable"));
  });

  it("sends a bank receipt as a form under the screen's key, and follows it", async () => {
    const seen = serve({
      [`POST /client/investments/${INVESTMENT}/payments/bank-transfer`]: json(
        payment({ method: "bank_transfer", status: "in_review", payer: null }),
        201,
      ),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
      [`GET /client/payments/${PAYMENT}`]: json(
        payment({ method: "bank_transfer", status: "failed", failure: "not_on_statement", payer: null }),
      ),
    });
    const client = createApiPaymentsClient();
    const transfer = await client.submitBankReceipt({
      payableId: INVESTMENT,
      file: { uri: "file:///receipt.pdf", name: "receipt.pdf", mimeType: "application/pdf", sizeBytes: 2048 },
      amountSent: { minor: 1_000_000, currency: "UGX" },
      sentOn: "2026-11-13",
      bankName: "Stanbic",
      transactionNumber: "TX123",
      idempotencyKey: "key-2",
    });
    expect(seen[0]!.headers["Idempotency-Key"]).toBe("key-2");
    const form = seen[0]!.body as FormData;
    expect(form.get("amount_minor")).toBe("1000000");
    expect(form.get("sent_on")).toBe("2026-11-13");
    expect(form.get("bank_name")).toBe("Stanbic");
    expect(form.get("transaction_number")).toBe("TX123");
    expect(transfer).toMatchObject({ id: PAYMENT, status: "checking", reason: null });
    await expect(client.getBankTransfer(PAYMENT)).resolves.toMatchObject({ status: "not_matched", reason: "not_on_statement" });
  });

  it("refuses a receipt for another amount than the investment's, or with a refused detail", async () => {
    const receipt = {
      payableId: INVESTMENT,
      file: { uri: "file:///r.png", name: "r.png", mimeType: "image/png" as const, sizeBytes: 1 },
      amountSent: { minor: 900_000, currency: "UGX" as const },
      sentOn: "2026-11-13",
      bankName: "Stanbic",
      transactionNumber: "TX1",
      idempotencyKey: "key-4",
    };
    const path = `POST /client/investments/${INVESTMENT}/payments/bank-transfer`;
    const client = createApiPaymentsClient();
    serve({ [path]: refused("validation_failed", 422, [{ field_path: ["body", "amount_minor"], code: "amount_differs_from_investment" }]) });
    await expect(client.submitBankReceipt(receipt)).rejects.toEqual(new PaymentError("amount_differs"));
    serve({ [path]: refused("validation_failed", 422, [{ field_path: ["body", "sent_on"], code: "date_parsing" }]) });
    await expect(client.submitBankReceipt(receipt)).rejects.toEqual(new PaymentError("not_accepted"));
  });

  it("refuses a receipt the API would not take", async () => {
    serve({
      [`POST /client/investments/${INVESTMENT}/payments/bank-transfer`]: refused("validation_failed", 422, [
        { field_path: ["body", "receipt"], code: "too_large" },
      ]),
    });
    await expect(
      createApiPaymentsClient().submitBankReceipt({
        payableId: INVESTMENT,
        file: { uri: "file:///r.png", name: "r.png", mimeType: "image/png", sizeBytes: 1 },
        amountSent: { minor: 1_000_000, currency: "UGX" },
        sentOn: "2026-11-13",
        bankName: "Stanbic",
        transactionNumber: "TX1",
        idempotencyKey: "key-3",
      }),
    ).rejects.toEqual(new PaymentError("receipt_not_accepted"));
  });

  it("does not take a bank transfer for a mobile money payment, or the other way round", async () => {
    serve({
      [`GET /client/payments/${PAYMENT}`]: json(payment()),
      [`GET /client/investments/${INVESTMENT}`]: json(investment()),
    });
    await expect(createApiPaymentsClient().getBankTransfer(PAYMENT)).rejects.toEqual(new PaymentError("not_found"));
  });

  it("says the bank details are not in the app yet, without asking the API", async () => {
    const seen = serve({});
    await expect(createApiPaymentsClient().getBankInstructions(INVESTMENT)).rejects.toEqual(
      new PaymentError("bank_details_missing"),
    );
    expect(seen).toEqual([]);
  });
});
