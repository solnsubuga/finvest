import { fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import Insights from "@/app/(app)/insights";
import { AuthProvider } from "@/auth/AuthProvider";
import { InsightsProvider } from "@/insights/InsightsProvider";
import { Currency, Insights as InsightsData, InsightsClient } from "@/insights/types";

jest.mock("expo-router", () => ({
  ...jest.requireActual("expo-router"),
  useRouter: () => ({ push: jest.fn(), navigate: jest.fn() }),
}));

jest.mock("@/autoinvest/schedule", () => ({
  ...jest.requireActual("@/autoinvest/schedule"),
  kampalaToday: () => "2026-11-13",
}));

function figures(currency: Currency, earned: number, investedNow: number): InsightsData {
  const money = (minor: number) => ({ currency, minor });
  return {
    currency,
    months: [
      { month: "2026-10-01", invested: money(investedNow), earned: money(earned), paidOut: money(0) },
      { month: "2026-11-01", invested: money(0), earned: money(earned), paidOut: money(0) },
    ],
    investedNow: money(investedNow),
    earnedSoFar: money(earned * 2),
    upcoming: [],
  };
}

function client(held: Currency[]): InsightsClient & { asked: Currency[] } {
  const asked: Currency[] = [];
  return {
    asked,
    heldCurrencies: async () => held,
    getInsights: async (_months, currency) => {
      asked.push(currency);
      return currency === "USD" ? figures("USD", 1_250, 50_000) : figures("UGX", 31_408, 5_000_000);
    },
  };
}

async function show(insights: InsightsClient) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <AuthProvider>
        <InsightsProvider client={insights}>
          <Insights />
        </InsightsProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

describe("the Insights screen", () => {
  it("shows shillings with no switch for a client who holds no dollars", async () => {
    const fake = client(["UGX"]);
    await show(fake);
    expect(await screen.findByText("UGX 5,000,000")).toBeTruthy();
    expect(screen.getByText(/Thousands of shillings, after tax/)).toBeTruthy();
    expect(screen.queryByTestId("insights-currency-USD")).toBeNull();
    expect(fake.asked).toEqual(["UGX"]);
  });

  it("switches to dollars on their own, in dollars and cents, never added to shillings", async () => {
    const fake = client(["UGX", "USD"]);
    await show(fake);
    await screen.findByText("UGX 5,000,000");
    await fireEvent.press(await screen.findByTestId("insights-currency-USD"));

    expect(await screen.findByText("USD 500.00")).toBeTruthy();
    expect(screen.getByText(/Dollars, after tax/)).toBeTruthy();
    expect(screen.getAllByText("12.50").length).toBeGreaterThan(0);
    expect(screen.queryByText("UGX 5,000,000")).toBeNull();
    expect(fake.asked).toEqual(["UGX", "USD"]);

    await fireEvent.press(screen.getByTestId("insights-currency-UGX"));
    expect(await screen.findByText("UGX 5,000,000")).toBeTruthy();
  });

  it("still shows shillings when the investments cannot be read", async () => {
    const fake = client(["UGX"]);
    fake.heldCurrencies = async () => {
      throw new Error("offline");
    };
    await show(fake);
    expect(await screen.findByText("UGX 5,000,000")).toBeTruthy();
    expect(screen.queryByTestId("insights-currency-USD")).toBeNull();
  });

  it("says a dollar investment has not started earning, rather than offering a first one", async () => {
    const fake = client(["UGX", "USD"]);
    fake.getInsights = async (_months, currency) =>
      currency === "USD" ? figures("USD", 0, 0) : figures("UGX", 31_408, 5_000_000);
    await show(fake);
    await screen.findByText("UGX 5,000,000");
    await fireEvent.press(await screen.findByTestId("insights-currency-USD"));
    expect(await screen.findByText("No dollar earnings yet")).toBeTruthy();
    expect(screen.queryByText("Make your first investment")).toBeNull();
  });
});
