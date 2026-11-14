import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";
import { readToken, saveToken } from "@/api/token";
import { appPinApplies } from "@/auth/pin";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

// The real API client, so a 401 goes through api() and its unauthorized listener.
jest.mock("@/auth/resolveClient", () => ({
  resolveAuthClient: () => ({
    client: jest.requireActual("@/auth/apiAuthClient").createApiAuthClient(),
    demo: false,
    demoCode: null,
  }),
}));

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

// A phone that keeps an unlock key behind its fingerprint, and whose finger is the right one.
const mockKept = { key: null as string | null };
jest.mock("@/auth/biometric", () => ({
  biometricOnThisPhone: async () => true,
  keepUnlockKey: async () => {},
  unlockKeyKept: async () => mockKept.key !== null,
  readUnlockKey: async () => mockKept.key,
  forgetUnlockKey: async () => {
    mockKept.key = null;
  },
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

const RID = "R".repeat(20);
const SESSION = {
  client_id: "A".repeat(20),
  phone: "+256772123456",
  restricted_until: null,
  pin_set: true,
  locked: true,
};

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  jest.mocked(appPinApplies).mockReturnValue(true);
});

afterEach(async () => {
  mockKept.key = null;
  jest.mocked(appPinApplies).mockReturnValue(false);
  setFetch(null);
  await saveToken(null);
});

describe("the app PIN against the API", () => {
  it("keeps 'Signed out for your safety' up after the fifth wrong PIN answers 401", async () => {
    await saveToken("phone-token");
    let tries = 0;
    setFetch(async (url, init) => {
      const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
      if (key === "GET /client/session") {
        return new Response(JSON.stringify(SESSION), { status: 200 });
      }
      if (key === "POST /client/pin/unlock") {
        tries += 1;
        if (tries < 5) {
          return new Response(JSON.stringify({ unlocked: false, tries_left: 5 - tries }), { status: 200 });
        }
        return new Response(
          JSON.stringify({ request_id: RID, error: { code: "unauthorized", message: "x", fields: [] } }),
          { status: 401 },
        );
      }
      throw new TypeError("Network request failed");
    });

    await renderRouter("./src/app", { initialUrl: "/" });
    await screen.findByText("Enter your PIN to open the app on this phone");
    for (let attempt = 0; attempt < 5; attempt += 1) {
      for (const digit of "90817") {
        await fireEvent.press(screen.getByTestId(`pin-key-${digit}`));
      }
    }

    await screen.findByText("Signed out for your safety");
    expect(await readToken()).toBeNull();
    await fireEvent.press(screen.getByTestId("pin-log-in-again"));
    await screen.findByText("Welcome back");
  });

  it("opens with the fingerprint by sending the unlock key, and forgets a key the API refuses", async () => {
    await saveToken("phone-token");
    mockKept.key = "the-unlock-key";
    const sent: unknown[] = [];
    let unlocked = false;
    setFetch(async (url, init) => {
      const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
      if (key === "GET /client/session") {
        return new Response(JSON.stringify({ ...SESSION, locked: !unlocked }), { status: 200 });
      }
      if (key === "POST /client/pin/unlock-with-key") {
        sent.push(JSON.parse(String(init?.body)));
        unlocked = sent.length === 1;
        return new Response(JSON.stringify({ unlocked, tries_left: 5 }), { status: 200 });
      }
      throw new TypeError("Network request failed");
    });

    await renderRouter("./src/app", { initialUrl: "/" });
    await waitFor(() => expect(screen.queryByText("Enter your PIN to open the app on this phone")).toBeNull());
    expect(sent).toEqual([{ key: "the-unlock-key" }]);
    expect(mockKept.key).toBe("the-unlock-key");
  });

  it("goes back to the PIN when the API no longer knows the key", async () => {
    await saveToken("phone-token");
    mockKept.key = "a-forgotten-key";
    setFetch(async (url, init) => {
      const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
      if (key === "GET /client/session") {
        return new Response(JSON.stringify(SESSION), { status: 200 });
      }
      if (key === "POST /client/pin/unlock-with-key") {
        return new Response(JSON.stringify({ unlocked: false, tries_left: 5 }), { status: 200 });
      }
      throw new TypeError("Network request failed");
    });

    await renderRouter("./src/app", { initialUrl: "/" });
    await screen.findByText("Fingerprint or face unlock is off for this phone. Enter your PIN.");
    expect(mockKept.key).toBeNull();
  });
});
