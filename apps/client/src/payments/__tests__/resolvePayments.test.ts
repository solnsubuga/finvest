import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { resolvePaymentsClient } from "../resolvePayments";
import * as stub from "../stubPaymentsClient";
import { PaymentError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

describe("resolvePaymentsClient", () => {
  it("uses the demo when the stub was loaded, and offers something to pay for", () => {
    expect(resolvePaymentsClient(stub).demoPayableId).toBe(stub.DEMO_PAYABLE_ID);
  });

  it("loads the stub in tests", () => {
    expect(resolvePaymentsClient().demoPayableId).toBe(stub.DEMO_PAYABLE_ID);
  });

  it("talks to the API without the stub, and offers nothing made up to pay for", async () => {
    const urls: string[] = [];
    setFetch(async (url) => {
      urls.push(String(url));
      throw new TypeError("Network request failed");
    });
    const { client, demoPayableId } = resolvePaymentsClient(null);
    expect(demoPayableId).toBeNull();
    await expect(client.getDeposit("d1")).rejects.toEqual(new PaymentError("unavailable"));
    expect(urls).toEqual([`${API_URL}/client/payments/d1`]);
  });
});
