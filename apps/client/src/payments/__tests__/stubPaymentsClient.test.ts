import { createStubPaymentsClient, DEMO_PAYABLE_ID } from "../stubPaymentsClient";
import { MobileMoneyRequest, PaymentError } from "../types";

function setup() {
  const clock = { ms: Date.parse("2026-11-13T12:31:00Z") };
  const client = createStubPaymentsClient({ delayMs: 0, now: () => clock.ms });
  const request = (payer: string, idempotencyKey = "key-1"): MobileMoneyRequest => ({
    payableId: DEMO_PAYABLE_ID,
    network: "mtn",
    payer,
    idempotencyKey,
  });
  return { clock, client, request };
}

describe("the demo payments client", () => {
  it("has one thing to pay for, with a whole-shilling amount", async () => {
    const { client } = setup();
    const payable = await client.getPayable(DEMO_PAYABLE_ID);
    expect(payable.amount).toEqual({ currency: "UGX", minor: 1_000_000 });
    await expect(client.getPayable("something-else")).rejects.toEqual(
      new PaymentError("not_payable"),
    );
  });

  it("walks an approved payment from sending to confirmed", async () => {
    const { clock, client, request } = setup();
    const started = await client.payWithMobileMoney(request("+256772123456"));
    expect(started.status).toBe("sending");
    expect(started.confirmedAt).toBeNull();

    clock.ms += 2_000;
    expect((await client.getDeposit(started.id)).status).toBe("awaiting_approval");

    clock.ms += 6_000;
    const confirmed = await client.getDeposit(started.id);
    expect(confirmed.status).toBe("confirmed");
    expect(confirmed.confirmedAt).toBe("2026-11-13T12:31:07.000Z");
    expect(confirmed.networkReference).not.toBeNull();
  });

  it.each([
    ["+256772000001", "declined"],
    ["+256772000002", "insufficient_funds"],
    ["+256772000003", "approval_timed_out"],
  ])("fails a payment from %s as %s", async (payer, failure) => {
    const { clock, client, request } = setup();
    const started = await client.payWithMobileMoney(request(payer));
    clock.ms += 8_000;
    const failed = await client.getDeposit(started.id);
    expect(failed.status).toBe("failed");
    expect(failed.failure).toBe(failure);
    expect(failed.confirmedAt).toBeNull();
  });

  it("checks a payment with no clear answer, and only then confirms it", async () => {
    const { clock, client, request } = setup();
    const started = await client.payWithMobileMoney(request("+256772000004"));
    clock.ms += 5_000;
    expect((await client.getDeposit(started.id)).status).toBe("checking");
    clock.ms += 10_000;
    expect((await client.getDeposit(started.id)).status).toBe("confirmed");
  });

  it("answers a repeated key with the same payment", async () => {
    const { client, request } = setup();
    const first = await client.payWithMobileMoney(request("+256772123456"));
    const second = await client.payWithMobileMoney(request("+256772123456"));
    expect(second.id).toBe(first.id);
  });

  it("refuses a known key that arrives with different details", async () => {
    const { client, request } = setup();
    await client.payWithMobileMoney(request("+256772123456"));
    await expect(
      client.payWithMobileMoney({ ...request("+256772123456"), network: "airtel" }),
    ).rejects.toEqual(new PaymentError("key_reused"));
    await expect(client.payWithMobileMoney(request("+256701000200"))).rejects.toEqual(
      new PaymentError("key_reused"),
    );
  });

  it("refuses a second payment while one is open, and after one is confirmed", async () => {
    const { clock, client, request } = setup();
    await client.payWithMobileMoney(request("+256772123456"));
    await expect(client.payWithMobileMoney(request("+256772123456", "key-2"))).rejects.toEqual(
      new PaymentError("already_paying"),
    );
    clock.ms += 8_000;
    await expect(client.payWithMobileMoney(request("+256772123456", "key-3"))).rejects.toEqual(
      new PaymentError("not_payable"),
    );
  });

  it("lets the client try again after a failed payment", async () => {
    const { clock, client, request } = setup();
    await client.payWithMobileMoney(request("+256772000002"));
    clock.ms += 8_000;
    const again = await client.payWithMobileMoney(request("+256772123456", "key-2"));
    expect(again.status).toBe("sending");
  });

  it("does not know a payment it never took", async () => {
    const { client } = setup();
    await expect(client.getDeposit("d99")).rejects.toEqual(new PaymentError("not_found"));
  });
});
