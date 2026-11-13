import { resolvePaymentsClient } from "../resolvePayments";
import * as stub from "../stubPaymentsClient";
import { PaymentError } from "../types";

describe("resolvePaymentsClient", () => {
  it("uses the demo when the stub was loaded, and offers something to pay for", () => {
    expect(resolvePaymentsClient(stub).demoPayableId).toBe(stub.DEMO_PAYABLE_ID);
  });

  it("loads the stub in development, which is how the tests run", () => {
    expect(resolvePaymentsClient().demoPayableId).toBe(stub.DEMO_PAYABLE_ID);
  });

  it("fails closed without the stub: nothing can be paid and nothing reads as paid", async () => {
    const { client, demoPayableId } = resolvePaymentsClient(null);
    expect(demoPayableId).toBeNull();
    await expect(client.getPayable(stub.DEMO_PAYABLE_ID)).rejects.toEqual(
      new PaymentError("unavailable"),
    );
    await expect(
      client.payWithMobileMoney({
        payableId: stub.DEMO_PAYABLE_ID,
        network: "mtn",
        payer: "+256772123456",
        idempotencyKey: "key-1",
      }),
    ).rejects.toBeInstanceOf(PaymentError);
    await expect(client.getDeposit("d1")).rejects.toBeInstanceOf(PaymentError);
    await expect(client.getBankInstructions(stub.DEMO_PAYABLE_ID)).rejects.toBeInstanceOf(
      PaymentError,
    );
    await expect(client.getBankTransfer("b1")).rejects.toBeInstanceOf(PaymentError);
  });
});
