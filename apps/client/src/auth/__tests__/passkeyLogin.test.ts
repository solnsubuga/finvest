import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";
import { readToken, saveToken } from "@/api/token";

import { createApiAuthClient } from "../apiAuthClient";
import { createStubAuthClient, DEMO_PHONE } from "../stubAuthClient";
import { PasskeyCancelled } from "../webauthn";

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

const mockDevice = { supported: true, answer: null as null | (() => Promise<unknown>) };

jest.mock("../webauthn", () => {
  const actual = jest.requireActual("../webauthn");
  return {
    ...actual,
    passkeysSupported: () => mockDevice.supported,
    signWithPasskey: jest.fn((options: unknown) =>
      mockDevice.answer ? mockDevice.answer() : Promise.resolve({ id: "cred", signed: options }),
    ),
  };
});

const ID = "AAAAAAAAAAAAAAAAAAAA";
const ATTEMPT = "BBBBBBBBBBBBBBBBBBBB";
const RID = "R".repeat(20);

type Route = { status: number; body?: unknown };

function serve(routes: Record<string, Route>) {
  const seen: { key: string; body: unknown }[] = [];
  setFetch(async (url, init) => {
    const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
    seen.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null });
    const route = routes[key];
    if (!route) throw new TypeError("Network request failed");
    return new Response(JSON.stringify(route.body), { status: route.status });
  });
  return seen;
}

const OPTIONS: Route = {
  status: 200,
  body: { attempt_id: ATTEMPT, public_key: { challenge: "abc", userVerification: "required" } },
};

afterEach(async () => {
  setFetch(null);
  await saveToken(null);
  mockDevice.supported = true;
  mockDevice.answer = null;
});

describe("logging in with a passkey alone", () => {
  it("asks the device for a passkey and sends its answer with the attempt", async () => {
    const seen = serve({
      "POST /client/login/passkey-only/options": OPTIONS,
      "POST /client/login/passkey-only/verify": {
        status: 200,
        body: {
          token: "phone-token",
          idle_expires_at: "2026-11-15T12:00:00Z",
          absolute_expires_at: "2026-11-16T00:00:00Z",
          session: { client_id: ID, phone: "+256772123456", restricted_until: null },
        },
      },
    });
    const auth = createApiAuthClient();
    expect(auth.canLogInWithPasskey()).toBe(true);
    const session = await auth.logInWithPasskey();
    expect(session).toMatchObject({ phone: "+256772123456", restricted: false });
    expect(seen.map((call) => call.key)).toEqual([
      "POST /client/login/passkey-only/options",
      "POST /client/login/passkey-only/verify",
    ]);
    expect(seen[1]?.body).toEqual({
      attempt_id: ATTEMPT,
      credential: { id: "cred", signed: { challenge: "abc", userVerification: "required" } },
    });
    expect(await readToken()).toBe("phone-token");
  });

  it("says cancelled when the client closes the device's prompt, and sends nothing more", async () => {
    mockDevice.answer = () => Promise.reject(new PasskeyCancelled());
    const seen = serve({ "POST /client/login/passkey-only/options": OPTIONS });
    await expect(createApiAuthClient().logInWithPasskey()).rejects.toMatchObject({ code: "cancelled" });
    expect(seen).toHaveLength(1);
  });

  it("passes on a passkey that is not saved to an open account", async () => {
    serve({
      "POST /client/login/passkey-only/options": OPTIONS,
      "POST /client/login/passkey-only/verify": {
        status: 401,
        body: { request_id: RID, error: { code: "passkey_failed", message: "x", fields: [] } },
      },
    });
    await expect(createApiAuthClient().logInWithPasskey()).rejects.toMatchObject({ code: "passkey_failed" });
  });

  it("is not offered, and asks the API nothing, where the device has no passkeys", async () => {
    mockDevice.supported = false;
    const seen = serve({});
    const auth = createApiAuthClient();
    expect(auth.canLogInWithPasskey()).toBe(false);
    await expect(auth.logInWithPasskey()).rejects.toMatchObject({ code: "passkey_unsupported" });
    expect(seen).toHaveLength(0);
  });

  it("logs the demo device in to the demo account that has a passkey, unrestricted", async () => {
    const auth = createStubAuthClient({ delayMs: 0 });
    const session = await auth.logInWithPasskey();
    expect(session).toMatchObject({ phone: DEMO_PHONE, restricted: false });
  });
});
