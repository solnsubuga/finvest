import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import Documents from "@/app/(app)/documents";
import { AuthProvider } from "@/auth/AuthProvider";
import { DocumentsProvider } from "@/documents/DocumentsProvider";
import { createStubDocumentsClient } from "@/documents/stubDocumentsClient";

jest.mock("@/autoinvest/schedule", () => ({
  ...jest.requireActual("@/autoinvest/schedule"),
  kampalaToday: () => "2026-11-13",
}));

jest.mock("@/documents/poll", () => ({
  STATEMENT_POLL_MS: 5,
  STATEMENT_POLL_LIMIT: 30,
  LIGHT_STATEMENT_POLL_MS: 5,
  LIGHT_STATEMENT_POLL_LIMIT: 30,
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

const NOW = () => new Date("2026-11-13T12:40:00Z");

async function show(phone: string | null) {
  const client = createStubDocumentsClient(phone, { delayMs: 0, now: NOW });
  await render(
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
      <AuthProvider>
        <DocumentsProvider client={client}>
          <Documents />
        </DocumentsProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
  return client;
}

describe("the Documents screen", () => {
  it("lists statements and receipts and downloads one", async () => {
    const client = await show("+256772123456");
    expect(await screen.findByText("Monthly statements")).toBeTruthy();
    expect(screen.getByText("October 2026")).toBeTruthy();
    expect(screen.getByText("1 Aug 2026 to 13 Nov 2026")).toBeTruthy();
    expect(screen.getByText("Receipt RC-Pq30vNa2")).toBeTruthy();
    expect(screen.getByText("Tax certificates")).toBeTruthy();
    expect(screen.getByText("Tax year 2025-26")).toBeTruthy();
    expect(screen.getByText(/1 Jul 2025 to 30 Jun 2026\. Shows the tax withheld/)).toBeTruthy();
    expect(screen.getByText("Investment notes")).toBeTruthy();
    expect(screen.getByText("Investment note IN-Pq31wXb7")).toBeTruthy();
    expect(screen.getByText("Investment note IN-Pq2mKc84")).toBeTruthy();
    expect(screen.getByText(/^Investment ended\. Note made on/)).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Download October 2026"));
    // The download finishes after the press returns, so wait for it.
    await waitFor(() => expect(client.downloaded).toEqual(["demo-monthly-1"]));
  });

  it("asks for a statement, follows it and offers the PDF", async () => {
    const client = await show("+256772123456");
    await screen.findByText("Monthly statements");
    await fireEvent.press(screen.getByTestId("pick-last_month"));
    expect(screen.getByTestId("statement-from").props.value).toBe("1 Oct 2026");
    expect(screen.getByTestId("statement-to").props.value).toBe("31 Oct 2026");
    await fireEvent.press(screen.getByTestId("make-statement"));
    expect(await screen.findByText("Making your statement")).toBeTruthy();
    expect(await screen.findByText("Your statement is ready")).toBeTruthy();
    expect(client.asked).toHaveLength(1);
    await fireEvent.press(screen.getByTestId("statement-download"));
    expect(await screen.findByText("Downloaded")).toBeTruthy();
    expect(client.downloaded).toEqual(["demo-asked-1"]);
  });

  it("says when a statement reaches days that are not final", async () => {
    await show("+256772123456");
    await screen.findByText("Monthly statements");
    await fireEvent.press(screen.getByTestId("pick-this_year"));
    await fireEvent.press(screen.getByTestId("make-statement"));
    expect(await screen.findByText("Your statement is ready")).toBeTruthy();
    expect(
      within(screen.getByTestId("statement-ready")).getByText(/Figures from 13 Nov 2026 may still change/),
    ).toBeTruthy();
  });

  it("refuses a backwards period before asking", async () => {
    const client = await show("+256772123456");
    await screen.findByText("Monthly statements");
    await fireEvent.changeText(screen.getByTestId("statement-from"), "20 Nov 2026");
    expect(screen.getByTestId("statement-date-error").props.children).toBe(
      "The start date is after the end date. Pick a start date on or before 13 Nov 2026.",
    );
    await fireEvent.press(screen.getByTestId("make-statement"));
    expect(client.asked).toEqual([]);
  });

  it("shows a statement that could not be made, and tries again with a new request", async () => {
    const client = await show("+256772123456");
    await screen.findByText("Monthly statements");
    await fireEvent.changeText(screen.getByTestId("statement-from"), "1 Jan 2019");
    await fireEvent.changeText(screen.getByTestId("statement-to"), "31 Dec 2019");
    await fireEvent.press(screen.getByTestId("make-statement"));
    expect(await screen.findByText("We could not make that statement")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("statement-retry"));
    expect(await screen.findByText("We could not make that statement")).toBeTruthy();
    expect(client.asked).toHaveLength(2);
  });

  it("shows the empty state for a new account", async () => {
    await show("+256700000123");
    expect(await screen.findByTestId("statements-empty")).toBeTruthy();
    expect(screen.getByTestId("receipts-empty")).toBeTruthy();
    expect(screen.getByText("Get a statement")).toBeTruthy();
    // A client with no certificate yet sees no empty section for one.
    expect(screen.queryByText("Tax certificates")).toBeNull();
  });

  it("offers to try again when the list cannot load", async () => {
    await show("+256700000199");
    expect(await screen.findByText(/We could not load your documents/)).toBeTruthy();
    await act(async () => {
      fireEvent.press(screen.getByTestId("documents-retry"));
    });
    expect(await screen.findByText(/We could not load your documents/)).toBeTruthy();
  });
});
