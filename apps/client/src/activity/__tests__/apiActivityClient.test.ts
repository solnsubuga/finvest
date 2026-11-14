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
    const { items, since } = await createApiActivityClient().list();
    expect(items.map((item) => item.amount.minor)).toEqual([3000, 2000, 1000]);
    expect(since).toBeNull();
    expect(seen).toEqual(["/client/payments", `/client/payments?before=${id(2)}`, "/client/investments", "/client/payouts"]);
  });

  it("stops after four pages and keeps only what is newer than the oldest payment read", async () => {
    const seen: string[] = [];
    setFetch(async (url) => {
      const path = String(url).replace(API_URL, "");
      seen.push(path);
      const page = seen.filter((p) => p.startsWith("/client/payments")).length;
      const body = path.startsWith("/client/payments")
        ? { items: [payment(10 - page * 2), payment(9 - page * 2)].map((item, i) => ({ ...item, id: id(page * 2 + i) })), next_before: id(page * 2 + 1) }
        : path === "/client/payouts"
          ? [
              { id: id(70), amount: { minor: 5, currency: "UGX" }, kind: "mobile_money", status: "paid", business_date: "2026-11-01", destination_id: id(80), created_at: "2026-11-01T09:00:00Z" },
              { id: id(71), amount: { minor: 7, currency: "UGX" }, kind: "mobile_money", status: "paid", business_date: "2026-11-01", destination_id: id(80), created_at: "2026-11-06T09:00:00Z" },
            ]
          : [];
      return new Response(JSON.stringify(body), { status: 200 });
    });
    const { items, since } = await createApiActivityClient().list();
    expect(seen.filter((p) => p.startsWith("/client/payments"))).toHaveLength(4);
    // Pages hold days 8 and 7, 6 and 5, 4 and 3, 2 and 1: the oldest read is day 1.
    expect(since).toBe("2026-11-01T08:00:00Z");
    expect(items.filter((item) => item.kind === "payout").map((item) => item.amount.minor)).toEqual([7, 5]);
  });

  it("drops older rows of every kind when the payments are cut short", async () => {
    let calls = 0;
    setFetch(async (url) => {
      const path = String(url).replace(API_URL, "");
      if (path.startsWith("/client/payments")) {
        calls += 1;
        return new Response(JSON.stringify({ items: [payment(5)].map((item) => ({ ...item, id: id(calls) })), next_before: id(calls) }), { status: 200 });
      }
      const body =
        path === "/client/payouts"
          ? [{ id: id(70), amount: { minor: 5, currency: "UGX" }, kind: "mobile_money", status: "paid", business_date: "2026-11-01", destination_id: id(80), created_at: "2026-11-02T09:00:00Z" }]
          : [];
      return new Response(JSON.stringify(body), { status: 200 });
    });
    const { items, since } = await createApiActivityClient().list();
    expect(since).toBe("2026-11-05T08:00:00Z");
    expect(items.some((item) => item.kind === "payout")).toBe(false);
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
