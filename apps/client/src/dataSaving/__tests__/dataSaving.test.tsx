import { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import Documents from "@/app/(app)/documents";
import Insights from "@/app/(app)/insights";
import LanguageAndData from "@/app/account/language";
import { AuthProvider } from "@/auth/AuthProvider";
import { DataSavingProvider } from "@/dataSaving/DataSavingProvider";
import { currentConnection } from "@/dataSaving/network";
import { DataSavingSettings, parseSettings } from "@/dataSaving/types";
import { DocumentsProvider } from "@/documents/DocumentsProvider";
import { createStubDocumentsClient } from "@/documents/stubDocumentsClient";
import { InsightsProvider } from "@/insights/InsightsProvider";
import { InsightsClient } from "@/insights/types";
import { ProfileProvider } from "@/profile/ProfileProvider";
import { createStubProfileClient } from "@/profile/stubProfileClient";

const mockStore: { saved: string | null } = { saved: null };

jest.mock("@/dataSaving/storage", () => {
  const { parseSettings: parse } = jest.requireActual("@/dataSaving/types");
  return {
    readSettings: async () => parse(mockStore.saved),
    writeSettings: async (settings: unknown) => {
      mockStore.saved = JSON.stringify(settings);
    },
  };
});

jest.mock("@/dataSaving/network", () => ({
  currentConnection: jest.fn(async () => "wifi"),
  canTellConnection: () => true,
}));

jest.mock("expo-router", () => ({
  ...jest.requireActual("expo-router"),
  useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn(), canGoBack: () => false }),
  Stack: { Screen: () => null },
}));

jest.mock("@/autoinvest/schedule", () => ({
  ...jest.requireActual("@/autoinvest/schedule"),
  kampalaToday: () => "2026-11-13",
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

function save(settings: DataSavingSettings) {
  mockStore.saved = JSON.stringify(settings);
}

async function show(children: ReactNode) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <DataSavingProvider>
        <AuthProvider>{children}</AuthProvider>
      </DataSavingProvider>
    </SafeAreaProvider>,
  );
}

beforeEach(() => {
  mockStore.saved = null;
  jest.mocked(currentConnection).mockResolvedValue("wifi");
});

describe("saved data saving choices", () => {
  it("fall back to off when missing, damaged or of the wrong shape", () => {
    expect(parseSettings(null)).toEqual({ light: false, wifiOnlyDocuments: false });
    expect(parseSettings("{not json")).toEqual({ light: false, wifiOnlyDocuments: false });
    expect(parseSettings('"on"')).toEqual({ light: false, wifiOnlyDocuments: false });
    expect(parseSettings('{"light":"yes","wifiOnlyDocuments":true,"other":1}')).toEqual({
      light: false,
      wifiOnlyDocuments: true,
    });
  });
});

describe("the Language and data screen", () => {
  it("starts with both off, and keeps each change on the device", async () => {
    await show(
      <ProfileProvider client={createStubProfileClient("+256772123456")}>
        <LanguageAndData />
      </ProfileProvider>,
    );
    const light = await screen.findByTestId("data-saving-light");
    const wifi = screen.getByTestId("data-saving-wifi");
    await waitFor(() => expect(light.props.accessibilityState.disabled).toBe(false));
    expect(light.props.accessibilityState.checked).toBe(false);
    expect(wifi.props.accessibilityState.checked).toBe(false);

    await fireEvent.press(wifi);
    expect(screen.getByTestId("data-saving-wifi").props.accessibilityState.checked).toBe(true);
    await waitFor(() => expect(parseSettings(mockStore.saved)).toEqual({ light: false, wifiOnlyDocuments: true }));

    await fireEvent.press(screen.getByTestId("data-saving-light"));
    await waitFor(() => expect(parseSettings(mockStore.saved)).toEqual({ light: true, wifiOnlyDocuments: true }));
    expect(screen.getByText("These apply on this phone only.")).toBeTruthy();
  });

  it("shows the choices saved earlier", async () => {
    save({ light: true, wifiOnlyDocuments: false });
    await show(
      <ProfileProvider client={createStubProfileClient("+256772123456")}>
        <LanguageAndData />
      </ProfileProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("data-saving-light").props.accessibilityState.checked).toBe(true),
    );
    expect(screen.getByTestId("data-saving-wifi").props.accessibilityState.checked).toBe(false);
  });
});

describe("Wi-Fi only downloads", () => {
  async function documents() {
    const client = createStubDocumentsClient("+256772123456", {
      delayMs: 0,
      now: () => new Date("2026-11-13T12:40:00Z"),
    });
    await show(
      <DocumentsProvider client={client}>
        <Documents />
      </DocumentsProvider>,
    );
    await screen.findByText("Monthly statements");
    return client;
  }

  it("holds a download on mobile data until the client taps again", async () => {
    save({ light: false, wifiOnlyDocuments: true });
    jest.mocked(currentConnection).mockResolvedValue("mobile");
    const client = await documents();
    await fireEvent.press(screen.getByLabelText("Download October 2026"));
    expect(await screen.findByText(/You are on mobile data/)).toBeTruthy();
    expect(client.downloaded).toEqual([]);

    await fireEvent.press(screen.getByLabelText("Download October 2026 on mobile data"));
    await waitFor(() => expect(client.downloaded).toEqual(["demo-monthly-1"]));
  });

  it("downloads straight away on Wi-Fi, or when the network cannot be told", async () => {
    save({ light: false, wifiOnlyDocuments: true });
    const client = await documents();
    await fireEvent.press(screen.getByLabelText("Download October 2026"));
    await waitFor(() => expect(client.downloaded).toEqual(["demo-monthly-1"]));

    jest.mocked(currentConnection).mockResolvedValue("unknown");
    await fireEvent.press(screen.getByLabelText("Download Receipt RC-Pq30vNa2"));
    await waitFor(() => expect(client.downloaded).toHaveLength(2));
    expect(screen.queryByText(/You are on mobile data/)).toBeNull();
  });

  it("never holds a download when the choice is off", async () => {
    jest.mocked(currentConnection).mockResolvedValue("mobile");
    const client = await documents();
    await fireEvent.press(screen.getByLabelText("Download October 2026"));
    await waitFor(() => expect(client.downloaded).toEqual(["demo-monthly-1"]));
  });
});

describe("light mode on Insights", () => {
  const insights: InsightsClient = {
    heldCurrencies: async () => ["UGX"],
    getInsights: async () => ({
      currency: "UGX",
      months: [
        { month: "2026-10-01", invested: { currency: "UGX", minor: 0 }, earned: { currency: "UGX", minor: 31_408 }, paidOut: { currency: "UGX", minor: 0 } },
        { month: "2026-11-01", invested: { currency: "UGX", minor: 0 }, earned: { currency: "UGX", minor: 12_000 }, paidOut: { currency: "UGX", minor: 0 } },
      ],
      investedNow: { currency: "UGX", minor: 5_000_000 },
      earnedSoFar: { currency: "UGX", minor: 43_408 },
      upcoming: [],
    }),
  };

  it("shows each month as a row of text instead of the bar chart", async () => {
    save({ light: true, wifiOnlyDocuments: false });
    await show(
      <InsightsProvider client={insights}>
        <Insights />
      </InsightsProvider>,
    );
    expect(await screen.findByTestId("insights-months")).toBeTruthy();
    expect(screen.getByText("October")).toBeTruthy();
    expect(screen.queryByRole("image")).toBeNull();
  });

  it("keeps the bar chart when light mode is off", async () => {
    await show(
      <InsightsProvider client={insights}>
        <Insights />
      </InsightsProvider>,
    );
    expect(await screen.findByTestId("insights-chart")).toBeTruthy();
    expect(screen.queryByTestId("insights-months")).toBeNull();
  });
});
