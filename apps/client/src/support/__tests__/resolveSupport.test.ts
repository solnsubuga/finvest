import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";
import { DEMO_PHONE, DEMO_PHONE_AUTHENTICATOR } from "@/auth/stubAuthClient";

import { resolveSupportClient, supportPhone } from "../resolveSupport";
import * as stub from "../stubSupportClient";
import { SupportError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

describe("resolveSupportClient", () => {
  it("loads the demo in development, which is how the tests run", async () => {
    const client = resolveSupportClient(DEMO_PHONE);
    expect((await client.listNotifications()).items.length).toBeGreaterThan(0);
    expect((await resolveSupportClient(DEMO_PHONE, stub).getPreferences()).length).toBe(7);
  });

  it("seeds only the demo's own accounts, and gives each account its own memory", async () => {
    expect(stub.SEEDED_ACCOUNTS).toEqual([DEMO_PHONE, DEMO_PHONE_AUTHENTICATOR]);
    const fresh = resolveSupportClient("+256701555012");
    expect((await fresh.listNotifications()).items).toEqual([]);
    expect(await fresh.listComplaints()).toEqual([]);

    const first = resolveSupportClient(DEMO_PHONE);
    await first.sendMessage({ body: "Hello", key: "key-00000001", topic: "other" });
    expect((await resolveSupportClient(DEMO_PHONE).getChat()).messages).toEqual([]);
  });

  it("answers nobody who is not signed in", async () => {
    await expect(resolveSupportClient(null).listNotifications()).rejects.toEqual(
      new SupportError("unavailable"),
    );
  });

  it("talks to the API when the demo is off, so nothing made up is ever shown", async () => {
    const seen: string[] = [];
    setFetch(async (url) => {
      seen.push(String(url).replace(API_URL, ""));
      return new Response(JSON.stringify({ items: [], unread: 0, next_before: null }), { status: 200 });
    });
    try {
      await expect(resolveSupportClient(DEMO_PHONE, null).listNotifications()).resolves.toEqual({
        items: [],
        unread: 0,
        nextBefore: null,
      });
      expect(seen).toEqual(["/client/notifications"]);
    } finally {
      setFetch(null);
    }
  });
});

describe("the number Call us dials", () => {
  const saved = process.env.EXPO_PUBLIC_SUPPORT_PHONE;
  afterEach(() => {
    process.env.EXPO_PUBLIC_SUPPORT_PHONE = saved;
  });

  it("is absent until a real number is set at build time", () => {
    delete process.env.EXPO_PUBLIC_SUPPORT_PHONE;
    expect(supportPhone()).toBeNull();
    process.env.EXPO_PUBLIC_SUPPORT_PHONE = "call us";
    expect(supportPhone()).toBeNull();
    process.env.EXPO_PUBLIC_SUPPORT_PHONE = "+256312000000";
    expect(supportPhone()).toBe("+256312000000");
  });
});
