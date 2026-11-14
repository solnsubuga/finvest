import { fireEvent, screen } from "@testing-library/react-native";
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
});
