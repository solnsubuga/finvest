import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { Text } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { reportConnection, resetConnection } from "@/api/connection";
import { setFetch } from "@/api/http";

import { ConnectionProvider, MAINTENANCE_RETRY_MS, OFFLINE_RETRY_MS } from "../ConnectionProvider";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 47, left: 0, right: 0, bottom: 34 } };

async function show() {
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ConnectionProvider>
        <Text>Home</Text>
      </ConnectionProvider>
    </SafeAreaProvider>,
  );
}

const products = [
  {
    code: "TREA-91",
    name: "91 Day Treasury Bill",
    kind: "treasury_bill",
    currency: "UGX",
    term_days: 91,
    minimum_minor: 100000,
    interest_paid: "at_maturity",
    status: "live",
    client_rate_percent: "10.63",
  },
];

beforeEach(() => resetConnection());
afterEach(() => {
  setFetch(null);
  jest.useRealTimers();
});

describe("the offline banner and the maintenance screen", () => {
  it("shows neither while the API answers", async () => {
    await show();
    expect(screen.getByText("Home")).toBeTruthy();
    expect(screen.queryByTestId("offline-banner")).toBeNull();
    expect(screen.queryByTestId("maintenance")).toBeNull();
  });

  it("puts a banner over the screen while offline, and clears it when an answer comes", async () => {
    await show();
    await act(async () => reportConnection("offline"));
    expect(screen.getByTestId("offline-banner")).toHaveTextContent(/No connection/);
    expect(screen.getByText("Home")).toBeTruthy();
    setFetch(async () => new Response(JSON.stringify(products), { status: 200 }));
    await fireEvent.press(screen.getByTestId("offline-retry"));
    expect(screen.queryByTestId("offline-banner")).toBeNull();
  });

  it("covers the app during maintenance until an answer comes", async () => {
    const asked: string[] = [];
    setFetch(async (url) => {
      asked.push(String(url));
      return new Response(JSON.stringify(products), { status: 200 });
    });
    await show();
    await act(async () => reportConnection("maintenance"));
    expect(screen.getByTestId("maintenance")).toHaveTextContent(/We are updating Finvest/);
    await fireEvent.press(screen.getByTestId("maintenance-retry"));
    expect(asked).toHaveLength(1);
    expect(asked[0]).toMatch(/\/client\/products$/);
    expect(screen.queryByTestId("maintenance")).toBeNull();
  });

  it("asks again by itself, less often during maintenance than while offline", async () => {
    jest.useFakeTimers();
    const asked: string[] = [];
    setFetch(async (url) => {
      asked.push(String(url));
      return new Promise<Response>(() => {});
    });
    await show();
    await act(async () => reportConnection("maintenance"));
    await act(async () => jest.advanceTimersByTime(MAINTENANCE_RETRY_MS - 1));
    expect(asked).toHaveLength(0);
    await act(async () => jest.advanceTimersByTime(1));
    expect(asked).toHaveLength(1);
    expect(OFFLINE_RETRY_MS).toBeLessThan(MAINTENANCE_RETRY_MS);
  });
});
