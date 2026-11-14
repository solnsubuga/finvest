import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockDownloaded: string[] = [];

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return { ...actual, createStubInvestingClient: () => actual.createStubInvestingClient({ delayMs: 0 }) };
});

jest.mock("@/documents/stubDocumentsClient", () => {
  const actual = jest.requireActual("@/documents/stubDocumentsClient");
  return {
    ...actual,
    createStubDocumentsClient: (phone: string | null) => {
      const client = actual.createStubDocumentsClient(phone, { delayMs: 0 });
      const download = client.download.bind(client);
      client.download = async (document: { id: string }) => {
        mockDownloaded.push(document.id);
        return download(document);
      };
      return client;
    },
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeAll(warmRouter, WARM_ROUTER_MS);

it("offers the note that confirms an investment started on its screen, and opens it", async () => {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByTestId("home");

  await act(async () => {
    router.push("/portfolio");
  });
  // The demo's note is for its first investment, the 91 day bill.
  await fireEvent.press((await screen.findAllByLabelText(/^91 Day Treasury Bill, Running/))[0]!);
  const note = await screen.findByTestId("investment-note");
  expect(note).toHaveTextContent(/Transaction note/);
  expect(note).toHaveTextContent(/Signed PDF, saved in Documents/);
  await fireEvent.press(note);
  expect(mockDownloaded).toEqual(["demo-investment-note-1"]);
});
