import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiActivityClient } from "../apiActivityClient";
import { ActivityError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

const id = (n: number) => String(n).padStart(20, "A");

function payment(n: number) {
  return {
    id: id(n),
    investment_id: id(90),
    method: "mtn",
    status: "confirmed",
    amount: { minor: 1000 * n, currency: "UGX" },
    failure: null,
    payer: "+256 772 ••• 456",
    receipt: null,
    created_at: `2026-11-0${n}T08:00:00Z`,
    confirmed_at: `2026-11-0${n}T08:01:00Z`,
  };
}

afterEach(() => setFetch(null));

describe("Activity from the API", () => {
  it("reads every page of payments, then investments and payouts", async () => {
    const seen: string[] = [];
    setFetch(async (url) => {
      const path = String(url).replace(API_URL, "");
      seen.push(path);
      const body =
        path === "/client/payments"
          ? { items: [payment(3), payment(2)], next_before: id(2) }
          : path.startsWith("/client/payments?before=")
            ? { items: [payment(1)], next_before: null }
            : path === "/client/investments" || path === "/client/payouts"
              ? []
              : null;
      return new Response(JSON.stringify(body), { status: body === null ? 404 : 200 });
    });
    const items = await createApiActivityClient().list();
    expect(items.map((item) => item.amount.minor)).toEqual([3000, 2000, 1000]);
    expect(seen).toEqual(["/client/payments", `/client/payments?before=${id(2)}`, "/client/investments", "/client/payouts"]);
  });

  it("fails as a whole when any part cannot be read", async () => {
    setFetch(async (url) => {
      const path = String(url).replace(API_URL, "");
      if (path === "/client/payouts") throw new TypeError("Network request failed");
      return new Response(JSON.stringify(path === "/client/payments" ? { items: [], next_before: null } : []), { status: 200 });
    });
    await expect(createApiActivityClient().list()).rejects.toBeInstanceOf(ActivityError);
  });
});
